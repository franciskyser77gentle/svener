from __future__ import annotations

import argparse, csv, json, os, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[2]
FIELDS = ["observed_at","date","rank","uid","coldkey","hotkey","alpha_stake","tao_balance",
          "incentive","emission","rank_change","alpha_change","tao_change"]

def config(path=ROOT / "config.yaml"):
    values = {"netuid": 28, "top_n": 30, "role": "miners", "favorites": []}
    for line in Path(path).read_text().splitlines():
        line=line.strip()
        if line.startswith("netuid:"): values["netuid"]=int(line.split(":",1)[1])
        elif line.startswith("top_n:"): values["top_n"]=int(line.split(":",1)[1])
        elif line.startswith("role:"): values["role"]=line.split(":",1)[1].strip()
        elif line.startswith("- "): values["favorites"].append(int(line[2:]))
    return values

def _number(obj, *names):
    for name in names:
        value=obj.get(name)
        if value is not None and value != "":
            try: return float(value)
            except (TypeError, ValueError): pass
    return None

def _rao(obj, *names):
    value=_number(obj, *names)
    return None if value is None else value / 1_000_000_000

class TaoStats:
    """Small dependency-free client; endpoint and key header are configurable."""
    def __init__(self, key, base_url=None, opener=urlopen, sleep=time.sleep):
        self.key, self.base = key, (base_url or os.getenv("TAOSTATS_BASE_URL","https://api.taostats.io/v1")).rstrip("/")
        self.opener, self.sleep = opener, sleep

    def fetch(self, netuid, at=None):
        if at:
            endpoint=os.getenv("TAOSTATS_HISTORY_ENDPOINT", f"{self.base}/subnets/metagraph/history")
            target=datetime.fromisoformat(at.replace("Z","+00:00"))
            start=int(target.timestamp())
            params={"netuid": netuid, "timestamp_start": start, "timestamp_end": start + 86400,
                    "limit": 200, "order_by": "timestamp", "order_dir": "desc"}
            page_limit=200
        else:
            endpoint=os.getenv("TAOSTATS_NEURONS_ENDPOINT", f"{self.base}/subnets/metagraph")
            params={"netuid": netuid, "limit": 256, "order_by": "emission", "order_dir": "desc"}
            page_limit=256
        rows=[]
        for page in range(1, 101):
            page_rows=self._get(endpoint, {**params, "page": page})
            rows.extend(page_rows)
            if len(page_rows) < page_limit: break
        return rows

    def owner_keys(self, netuid):
        """Best-effort subnet owner lookup; metagraph role flags remain authoritative."""
        endpoint=os.getenv("TAOSTATS_SUBNETS_ENDPOINT", f"{self.base}/subnets/latest")
        try:
            payload=self._get_payload(endpoint, {"netuid": netuid})
        except RuntimeError:
            return set()
        values=payload.get("data", payload) if isinstance(payload,dict) else payload
        if isinstance(values, dict): values=[values]
        owners=set()
        for item in values if isinstance(values,list) else []:
            for key in ("owner_hotkey","owner","hotkey","coldkey"):
                value=item.get(key) if isinstance(item,dict) else None
                if isinstance(value,dict): value=value.get("ss58") or value.get("hex")
                if value: owners.add(value)
        return owners

    def _get(self, endpoint, params):
        payload=self._get_payload(endpoint, params)
        rows=payload.get("data", payload) if isinstance(payload,dict) else payload
        if isinstance(rows,dict):
            rows=rows.get("neurons", rows.get("results", []))
        return rows if isinstance(rows,list) else []

    def _get_payload(self, endpoint, params):
        headers={"Accept":"application/json", "User-Agent":"sven28M/0.1 (+https://github.com/franciskyser77gentle/svener)",
                 "Authorization":self.key}
        last=None
        for attempt in range(5):
            try:
                req=Request(endpoint+"?"+urlencode(params), headers=headers)
                with self.opener(req, timeout=30) as response:
                    if response.status == 429 or response.status >= 500:
                        body=response.read().decode(errors="replace")
                        last=f"HTTP Error {response.status}: {self._safe_body(body)}"
                        if attempt < 4: self.sleep(2 ** attempt)
                        continue
                    return json.loads(response.read().decode())
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                if isinstance(exc, HTTPError):
                    body=exc.read().decode(errors="replace")
                    last=f"HTTP Error {exc.code}: {self._safe_body(body) or exc.reason}"
                else:
                    last=str(exc)
                if isinstance(exc, HTTPError) and 400 <= exc.code < 500: break
                self.sleep(2 ** attempt)
        raise RuntimeError(f"TaoStats request failed: {last}")

    def _safe_body(self, body):
        """Expose enough provider detail to debug without ever echoing the key."""
        return body.replace(self.key, "[REDACTED]")[:500].strip()

def normalize(raw, netuid, top_n, observed, owner_keys=()):
    rows=[]
    for item in raw:
        if item.get("validator_permit") is True or item.get("is_child_key") is True:
            continue
        if item.get("is_owner_hotkey") is True or item.get("hotkey") in owner_keys or item.get("coldkey") in owner_keys:
            continue
        uid=item.get("uid", item.get("UID"))
        try: uid=int(uid)
        except (TypeError,ValueError): continue
        incentive=_number(item,"incentive","incentive_percent","incentive_percentage")
        emission=_rao(item,"emission","emission_percent","emission_percentage")
        stake=_rao(item,"alpha_stake","alpha","stake","stake_alpha","total_alpha_stake")
        rows.append({"uid":uid, "coldkey":item.get("coldkey",item.get("cold_key","")),
          "hotkey":item.get("hotkey",item.get("hot_key","")), "alpha_stake":stake,
          # TaoStats' metagraph does not expose a coldkey free balance. The
          # documented root_stake is the row's TAO stake, also in RAO.
          "tao_balance":_rao(item,"root_stake","tao_balance","balance","free_balance","tao"),
          "incentive":incentive, "emission":emission})
    # Miner ranking is emission first, then incentive. Do not rank by stake:
    # large validator/owner stake is not evidence of mining activity.
    rows.sort(key=lambda x: (x["emission"] if x["emission"] is not None else -1,
                             x["incentive"] if x["incentive"] is not None else -1), reverse=True)
    for rank,row in enumerate(rows[:top_n],1):
        row.update({"rank":rank,"observed_at":observed,"date":observed[:10],
                    "rank_change":None,"alpha_change":None,"tao_change":None})
    return rows[:top_n]

def read_history(path):
    return list(csv.DictReader(path.open())) if path.exists() else []

def enrich_changes(rows, history):
    previous={int(r["uid"]):r for r in history if r.get("uid","").isdigit()}
    for r in rows:
        old=previous.get(r["uid"])
        if old:
            r["rank_change"]=int(old["rank"])-r["rank"]
            for key in ("alpha_stake","tao_balance"):
                try: r[key.replace("_stake","_change").replace("_balance","_change")]=(
                    None if r[key] is None or old.get(key,"") in ("", "None") else r[key]-float(old[key]))
                except (TypeError,ValueError): pass
    return rows

def write_data(rows, data_dir=ROOT/"data", status="complete", error=None):
    data_dir.mkdir(parents=True,exist_ok=True)
    csv_path=data_dir/"snapshots.csv"; history=read_history(csv_path)
    date=rows[0]["date"] if rows else datetime.now(timezone.utc).date().isoformat()
    history=[r for r in history if r.get("date") != date]
    with csv_path.open("w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=FIELDS); writer.writeheader()
        for r in history+rows: writer.writerow({k:r.get(k) for k in FIELDS})
    latest={"schema_version":1,"monitor":"sven28M","netuid":28,"role":config()["role"],
            "favorites":config()["favorites"],"status":status,
            "observed_at":rows[0]["observed_at"] if rows else datetime.now(timezone.utc).isoformat(),
            "ranking":"miners only: emission, then incentive (descending)",
            "top":rows,"error":error}
    (data_dir/"latest.json").write_text(json.dumps(latest,indent=2)+"\n")
    stats={}
    for r in history+rows:
        s=stats.setdefault(str(r["uid"]),{"uid":int(r["uid"]),"coldkey":r["coldkey"],"hotkey":r["hotkey"],
          "snapshots":0,"best_rank":999,"last_seen":r["date"],"history":[]})
        s.update(coldkey=r["coldkey"],hotkey=r["hotkey"],last_seen=r["date"])
        s["snapshots"]+=1; s["best_rank"]=min(s["best_rank"],int(r["rank"]))
        s["history"].append(r)
    (data_dir/"stats.json").write_text(json.dumps(stats,indent=2)+"\n")

def collect(api_key=None, data_dir=ROOT/"data", observed=None):
    cfg=config(); backfill=observed is not None
    observed=observed or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    path=Path(data_dir); previous=read_history(path/"snapshots.csv")
    key=api_key if api_key is not None else os.getenv("TAOSTATS_API_KEY","")
    if not key.strip():
        raise RuntimeError("TAOSTATS_API_KEY is missing or empty; add it as a GitHub Actions repository secret.")
    client=TaoStats(key)
    rows=normalize(client.fetch(cfg["netuid"], observed if backfill else None),cfg["netuid"],cfg["top_n"],observed,
                   owner_keys=client.owner_keys(cfg["netuid"]))
    if not rows:
        raise RuntimeError(f"TaoStats API returned zero usable neuron rows for netuid {cfg['netuid']}.")
    write_data(enrich_changes(rows,previous),path)
    return rows

def main():
    p=argparse.ArgumentParser(); p.add_argument("--backfill-days",type=int,default=0); args=p.parse_args()
    try:
        if args.backfill_days:
            for days_ago in range(args.backfill_days, 0, -1):
                observed=(datetime.now(timezone.utc)-timedelta(days=days_ago)).replace(hour=0,minute=15,second=0,microsecond=0).isoformat()
                print(f"Backfill {observed}: {len(collect(observed=observed))} rows")
        rows=collect(); print(f"Recorded {len(rows)} top-{config()['top_n']} rows.")
    except RuntimeError as exc:
        raise SystemExit(f"ERROR: {exc}")

if __name__=="__main__": main()

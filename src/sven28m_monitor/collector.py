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
    values = {"netuid": 28, "top_n": 30, "favorites": []}
    for line in Path(path).read_text().splitlines():
        line=line.strip()
        if line.startswith("netuid:"): values["netuid"]=int(line.split(":",1)[1])
        elif line.startswith("top_n:"): values["top_n"]=int(line.split(":",1)[1])
        elif line.startswith("- "): values["favorites"].append(int(line[2:]))
    return values

def _number(obj, *names):
    for name in names:
        value=obj.get(name)
        if value is not None and value != "":
            try: return float(value)
            except (TypeError, ValueError): pass
    return None

class TaoStats:
    """Small dependency-free client; endpoint and key header are configurable."""
    def __init__(self, key, base_url=None, opener=urlopen, sleep=time.sleep):
        self.key, self.base = key, (base_url or os.getenv("TAOSTATS_BASE_URL","https://api.taostats.io/api/v1")).rstrip("/")
        self.opener, self.sleep = opener, sleep

    def fetch(self, netuid, at=None):
        endpoint=os.getenv("TAOSTATS_NEURONS_ENDPOINT", f"{self.base}/neurons")
        params={"netuid": netuid, "limit": 256}
        if at: params["timestamp"] = at
        headers={"Accept":"application/json", "Authorization":f"Bearer {self.key}", "X-API-Key":self.key}
        last=None
        for attempt in range(5):
            try:
                req=Request(endpoint+"?"+urlencode(params), headers=headers)
                with self.opener(req, timeout=30) as response:
                    if response.status == 429 or response.status >= 500:
                        raise HTTPError(endpoint, response.status, "retryable", response.headers, None)
                    payload=json.loads(response.read().decode())
                rows=payload.get("data", payload) if isinstance(payload,dict) else payload
                if isinstance(rows,dict):
                    rows=rows.get("neurons", rows.get("results", []))
                return rows if isinstance(rows,list) else []
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                last=exc
                if isinstance(exc, HTTPError) and exc.code in (401,403,404): break
                self.sleep(2 ** attempt)
        raise RuntimeError(f"TaoStats request failed: {last}")

def normalize(raw, netuid, top_n, observed):
    rows=[]
    for item in raw:
        uid=item.get("uid", item.get("UID"))
        try: uid=int(uid)
        except (TypeError,ValueError): continue
        incentive=_number(item,"incentive","incentive_percent","incentive_percentage")
        emission=_number(item,"emission","emission_percent","emission_percentage")
        stake=_number(item,"alpha_stake","alpha","stake","stake_alpha","total_stake")
        rows.append({"uid":uid, "coldkey":item.get("coldkey",item.get("cold_key","")),
          "hotkey":item.get("hotkey",item.get("hot_key","")), "alpha_stake":stake,
          "tao_balance":_number(item,"tao_balance","balance","free_balance","tao"),
          "incentive":incentive, "emission":emission})
    # Emission is the most direct miner-facing ranking; incentive is a useful
    # fallback when an API response omits emission.
    rows.sort(key=lambda x: (x["emission"] if x["emission"] is not None else
                             x["incentive"] if x["incentive"] is not None else
                             x["alpha_stake"] if x["alpha_stake"] is not None else -1), reverse=True)
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
    latest={"schema_version":1,"monitor":"sven28M","netuid":28,"favorites":config()["favorites"],"status":status,
            "observed_at":rows[0]["observed_at"] if rows else datetime.now(timezone.utc).isoformat(),
            "ranking":"emission, then incentive, then alpha stake (descending)",
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
    cfg=config(); observed=observed or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    path=Path(data_dir); previous=read_history(path/"snapshots.csv")
    try:
        rows=normalize(TaoStats(api_key or os.getenv("TAOSTATS_API_KEY","")).fetch(cfg["netuid"], observed),cfg["netuid"],cfg["top_n"],observed)
        if not rows: raise RuntimeError("API returned no neuron rows")
        write_data(enrich_changes(rows,previous),path)
        return rows
    except Exception as exc:
        latest=path/"latest.json"
        if latest.exists(): return []
        write_data([],path,"unavailable",str(exc)); return []

def main():
    p=argparse.ArgumentParser(); p.add_argument("--backfill-days",type=int,default=0); args=p.parse_args()
    if args.backfill_days:
        for days_ago in range(args.backfill_days, 0, -1):
            observed=(datetime.now(timezone.utc)-timedelta(days=days_ago)).replace(hour=0,minute=15,second=0,microsecond=0).isoformat()
            print(f"Backfill {observed}: {len(collect(observed=observed))} rows")
    rows=collect(); print(f"Recorded {len(rows)} top-{config()['top_n']} rows.")

if __name__=="__main__": main()

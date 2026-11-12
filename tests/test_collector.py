import json
import io
from urllib.error import HTTPError
from pathlib import Path
from sven28m_monitor.collector import normalize, write_data, enrich_changes, TaoStats, collect
import pytest

class Response:
    status=200
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self): return b'{"data":[{"uid":9,"emission":7,"validator_trust":0,"coldkey":"c","hotkey":"h"}]}'

def test_api_client_parses_mocked_response():
    seen={}
    def opener(request, timeout):
        seen["url"]=request.full_url
        seen["auth"]=request.get_header("Authorization")
        seen["user_agent"]=request.get_header("User-agent")
        return Response()
    client=TaoStats("test-key",base_url="https://example.test/v1",opener=opener)
    assert client.fetch(28)[0]["uid"] == 9
    assert seen["url"] == "https://example.test/v1/subnets/metagraph?netuid=28&limit=256&order_by=emission&order_dir=desc&page=1"
    assert seen["auth"] == "test-key"
    assert seen["user_agent"].startswith("sven28M/")

def test_history_uses_documented_timestamp_params():
    seen={}
    def opener(request, timeout):
        seen["url"]=request.full_url
        return Response()
    TaoStats("test-key",base_url="https://example.test/v1",opener=opener).fetch(
        28, "2026-01-01T00:15:00+00:00")
    assert "metagraph/history?" in seen["url"]
    assert "timestamp_start=1767226500" in seen["url"]
    assert "timestamp_end=1767312900" in seen["url"]
    assert "order_by=timestamp" in seen["url"]

def test_api_error_includes_redacted_response_body():
    def opener(request, timeout):
        raise HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(
            b'{"error":"Cloudflare denied request","token":"secret-key"}'))
    with pytest.raises(RuntimeError, match=r"Cloudflare denied request.*REDACTED"):
        TaoStats("secret-key", opener=opener).fetch(28)

def test_client_does_not_retry_bad_request():
    attempts=[]
    def opener(request, timeout):
        attempts.append(request.full_url)
        raise HTTPError(request.full_url, 400, "Bad Request", {}, io.BytesIO(b"invalid query"))
    with pytest.raises(RuntimeError, match="invalid query"):
        TaoStats("test-key", opener=opener, sleep=lambda _: pytest.fail("4xx retried")).fetch(28)
    assert len(attempts) == 1

def test_normalize_ranks_emission_then_stake():
    rows=normalize([
        {"uid":1,"emission":"2000000000","validator_trust":0,"coldkey":"c1","hotkey":"h1","alpha_stake":"4000000000"},
        {"uid":2,"emission":"1000000000","validator_trust":0,"incentive":9,"coldkey":"c2","hotkey":"h2","alpha_stake":"99000000000"},
        {"uid":3,"emission":"500000000","validator_trust":0,"alpha_stake":"100000000000","coldkey":"c3","hotkey":"h3"},
    ],28,3,"2026-01-01T00:15:00+00:00")
    assert [r["uid"] for r in rows] == [1,2,3]
    assert rows[0]["alpha_stake"] == 4
    assert rows[0]["emission"] == 2

def test_normalize_matches_taostats_miners_tab(capsys):
    rows=normalize([
        {"uid":1,"emission":9000000000,"validator_permit":True,"validator_trust":1},
        {"uid":157,"emission":5000000000,"validator_permit":True,"validator_trust":0,"incentive":0.2,
         "is_owner_hotkey":True,"coldkey":"owner-coldkey"},
        {"uid":5,"emission":4000000000,"validator_permit":False,"validator_trust":0,
         "is_child_key":True,"hotkey":"miner-hotkey"},
    ],28,30,"2026-01-01T00:15:00+00:00")
    assert [r["uid"] for r in rows] == [157,5]
    assert "uid157 validator_permit=True validator_trust=0" in capsys.readouterr().out

def test_miner_falls_back_to_zero_dividends():
    rows=normalize([
        {"uid":8,"emission":1000000000,"dividends":0},
        {"uid":9,"emission":2000000000,"dividends":1},
    ],28,30,"2026-01-01T00:15:00+00:00")
    assert [r["uid"] for r in rows] == [8]

def test_changes_and_outputs(tmp_path):
    old=[{"uid":"7","rank":"4","alpha_stake":"10","tao_balance":"2","date":"2026-01-01"}]
    rows=normalize([{"uid":7,"emission":4000000000,"validator_trust":0,"alpha_stake":13000000000,"root_stake":3000000000}],28,1,
                   "2026-01-02T00:15:00+00:00")
    enrich_changes(rows,old)
    assert rows[0]["rank_change"] == 3
    assert rows[0]["alpha_change"] == 3
    assert rows[0]["tao_balance"] == 3
    write_data(rows,tmp_path)
    assert json.loads((tmp_path/"latest.json").read_text())["top"][0]["uid"] == 7
    assert "7" in json.loads((tmp_path/"stats.json").read_text())

def test_malformed_uids_are_ignored():
    assert normalize([{"uid":"not-a-number"}],28,30,"2026-01-01T00:00:00Z") == []

def test_collect_requires_api_key(tmp_path):
    with pytest.raises(RuntimeError, match="TAOSTATS_API_KEY is missing"):
        collect(api_key="", data_dir=tmp_path)

def test_collect_rejects_empty_api_response(tmp_path, monkeypatch):
    monkeypatch.setattr(TaoStats, "fetch", lambda self, netuid, at=None: [])
    with pytest.raises(RuntimeError, match="zero eligible miners"):
        collect(api_key="test-key", data_dir=tmp_path)

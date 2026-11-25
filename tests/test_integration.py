"""Integration test end-to-end terhadap ekosistem yang SEDANG BERJALAN (python run_all.py --no-dash).
Test otomatis di-skip bila Control Center tidak aktif.
"""
import time

import pytest
import requests

from common.config import API_KEYS, ENTERPRISES, api_url

CC = api_url("control_center")


def _up() -> bool:
    try:
        return requests.get(f"{CC}/health", timeout=2).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _up(), reason="server belum berjalan (python run_all.py --no-dash)")


def run(scenario, strategy, auto_officer=True):
    r = requests.post(f"{CC}/scenario/run", json={"scenario_id": scenario, "strategy": strategy,
                                                  "auto_officer": auto_officer, "bus_delay_s": 0}, timeout=30)
    assert r.status_code == 200, r.text
    for _ in range(300):
        st = requests.get(f"{CC}/scenario/status", timeout=30).json()["run"]
        if st["status"] in ("DONE", "FAILED"):
            assert st["status"] == "DONE", st.get("error")
            return st
        time.sleep(1)
    raise TimeoutError


def test_all_servers_healthy_and_publish_agent_cards():
    for eid in ENTERPRISES:
        assert requests.get(f"{api_url(eid)}/health", timeout=5).json()["status"] == "UP"
        card = requests.get(f"{api_url(eid)}/.well-known/agent-card.json", timeout=5).json()
        assert card["skills"] and card["agents"]


def test_governance_blocks_unauthorized_access():
    # GlobalCargo tidak boleh membaca detail deklarasi Bea Cukai
    r = requests.get(f"{api_url('customs')}/customs/api/declarations", headers={"X-API-Key": API_KEYS["globalcargo"]})
    assert r.status_code == 403
    # Tanpa API key -> 401
    assert requests.get(f"{api_url('smartport')}/api/berth-plan").status_code == 401
    # Tool MCP manifest hanya untuk Customs
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_manifest", "arguments": {"vessel_id": "V001"}}}
    r = requests.post(f"{api_url('oceanline')}/mcp", json=body, headers={"X-API-Key": API_KEYS["globalcargo"]}).json()
    assert "error" in r
    r = requests.post(f"{api_url('oceanline')}/mcp", json=body, headers={"X-API-Key": API_KEYS["customs"]}).json()
    assert r["result"]["structuredContent"]["manifest"]


def test_vessel_delay_triggers_negotiation_and_measurable_change():
    collab = run("S1_vessel_delay", "collaborative")
    static = run("S1_vessel_delay", "static")
    a, s = collab["after"], static["after"]
    assert a["negotiations"] >= 1 and a["negotiations_agreed"] >= 1
    assert a["berth_plan_versions"] > collab["baseline"]["berth_plan_versions"]   # keputusan berubah
    assert a["J_global_objective"] < s["J_global_objective"]                     # adaptif lebih baik
    assert a["compliance_violations"] == 0 and s["compliance_violations"] == 0
    msgs = requests.get(f"{CC}/a2a/messages?limit=1000", headers={"X-API-Key": API_KEYS["control_center"]}).json()
    assert all(m["status"] == "DELIVERED" for m in msgs["messages"])


def test_deadlocked_negotiation_is_mediated_by_control_center():
    st = run("S8_hard_negotiation", "collaborative")
    assert st["after"]["mediations"] == 1 and st["after"]["negotiations_agreed"] == 1
    tr = requests.get(f"{CC}/trace?limit=500").json()["trace"]
    assert any(t["action"] == "MEDIATE" and t["agent"] == "control_center.coordinator_agent" for t in tr)


def test_customs_requires_human_decision():
    st = run("S0_normal", "collaborative", auto_officer=False)
    assert st["after"]["awaiting_officer"] >= 1 and st["after"]["released"] == 0
    q = requests.get(f"{api_url('customs')}/customs/api/review-queue", headers={"X-API-Key": API_KEYS["customs"]}).json()
    no = q["queue"][0]["declaration_no"]
    url = f"{api_url('customs')}/customs/api/officer/decisions"
    # role non-petugas ditolak
    bad = requests.post(url, json={"declaration_no": no, "decision": "APPROVE"},
                        headers={"X-API-Key": API_KEYS["customs"], "X-User-Role": "Risk Analyst"})
    assert bad.status_code == 403
    ok = requests.post(url, json={"declaration_no": no, "decision": "APPROVE", "officer": "pytest"},
                       headers={"X-API-Key": API_KEYS["customs"], "X-User-Role": "Customs Officer"})
    assert ok.status_code == 200 and ok.json()["status"] == "RELEASED"

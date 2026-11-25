"""Agregasi KPI ekosistem dari endpoint 'shared' tiap perusahaan (sesuai izin governance) + fungsi objektif global.

    min J = α·C/C_ref + β·T/T_ref + γ·R/R_ref − δ·Q
C : total biaya ekosistem (biaya tunggu & telat kapal + biaya crane + biaya armada truk + denda produksi importir
    + biaya kongesti yard 0,1 jt per kontainer-jam dwelling)
T : waktu (rata-rata tunggu kapal + rata-rata dwelling kontainer), jam
R : risiko (kontainer ditahan, deklarasi menunggu petugas, kapal dengan dokumen pending, pelanggaran kepatuhan x10)
Q : kualitas layanan = rasio kontainer terkirim tepat waktu
Utilitas lokal U_i = Revenue_i − Cost_i − RiskPenalty_i juga dilaporkan agar otonomi tiap perusahaan terlihat.
"""
from __future__ import annotations

import requests

from common import schema_mapping as sm
from common.config import API_KEYS, api_url

WEIGHTS = {"alpha": 0.4, "beta": 0.3, "gamma": 0.2, "delta": 0.1}
REF = {"C": 300.0, "T": 9.0, "R": 2.0}  # ~nilai kondisi normal -> tiap suku ternormalisasi ~1
H = {"X-API-Key": API_KEYS["control_center"]}


def _get(eid: str, path: str, xml: bool = False):
    r = requests.get(f"{api_url(eid)}{path}", headers=H, timeout=30)
    r.raise_for_status()
    return r.text if xml else r.json()


def collect() -> dict:
    out, errors = {}, {}
    calls = {
        "oceanline": ("/api/v1/shared/kpi", False), "smartport": ("/api/shared/kpi", False),
        "globalcargo": ("/gc/v2/Shared/Kpi", False), "importexport": ("/api/xml/kpi", True),
        "customs": ("/customs/api/shared/kpi", False),
    }
    for eid, (path, xml) in calls.items():
        try:
            out[eid] = _get(eid, path, xml)
        except Exception as exc:
            errors[eid] = str(exc)
    return {"raw": out, "errors": errors}


def compute(raw: dict) -> dict:
    ol = raw.get("oceanline", {})
    sp = raw.get("smartport", {})
    gc = sm.globalcargo_unwrap(raw["globalcargo"]) if "globalcargo" in raw else {}
    ie = sm.importexport_kpi(raw["importexport"]) if "importexport" in raw else {}
    cu = raw.get("customs", {})
    spk, gck, cuk = sp.get("localKpi", {}), gc.get("LocalKpi", {}), cu.get("local_kpi", {})

    vessel_cost = spk.get("vessel_wait_and_delay_cost_jt", 0.0) or 0.0
    crane_cost = sp.get("utility", {}).get("costJt", 0.0) or 0.0
    truck_cost = gc.get("Utility", {}).get("CostJt", 0.0) or 0.0
    penalty = ie.get("denda_produksi_juta", 0.0) or 0.0
    yard_cost = 0.1 * (gck.get("AvgDwellHours") or 0.0) * (gck.get("TripsPlanned") or 0)  # kongesti yard
    C = vessel_cost + crane_cost + truck_cost + penalty + yard_cost
    avg_wait = spk.get("avg_wait_h", 0.0) or 0.0
    dwell = gck.get("AvgDwellHours") or 0.0
    T = avg_wait + dwell
    violations = cuk.get("released_without_human_approval", 0) or 0
    pending_docs = (ie.get("jumlah_pengiriman") or 0) - (ie.get("dokumen_lengkap") or 0)
    R = (gck.get("WaitingRelease", 0) or 0) / 4 + (cuk.get("awaiting_officer", 0) or 0) + (cuk.get("on_hold", 0) or 0) \
        + pending_docs + 10 * violations
    Q = gck.get("OnTimeRate") if gck.get("OnTimeRate") is not None else 0.0
    J = (WEIGHTS["alpha"] * C / REF["C"] + WEIGHTS["beta"] * T / REF["T"] + WEIGHTS["gamma"] * R / REF["R"]
         - WEIGHTS["delta"] * Q)
    utilities = {
        "oceanline": ol.get("utility", {}).get("U"),
        "smartport": sp.get("utility", {}).get("U"),
        "globalcargo": gc.get("Utility", {}).get("U"),
        "importexport": ie.get("utilitas_U"),
        "customs": cu.get("utility", {}).get("U"),
    }
    return {
        "ecosystem": {
            "J_global_objective": round(J, 4), "C_total_cost_jt": round(C, 2), "T_time_h": round(T, 2),
            "R_risk": round(R, 2), "Q_on_time_rate": round(Q, 3),
            "avg_vessel_wait_h": avg_wait, "max_vessel_wait_h": spk.get("max_wait_h"),
            "vessels_wait_gt_2h": spk.get("vessels_wait_gt_2h"), "late_departures": spk.get("late_departures"),
            "berth_utilization_24h": spk.get("berth_utilization_24h"), "makespan_h": spk.get("makespan_h"),
            "avg_container_dwell_h": dwell, "late_container_hours": gck.get("LateContainerHours"),
            "trucks_used": gck.get("TrucksUsed"), "subcon_trips": gck.get("SubconTrips"),
            "waiting_release": gck.get("WaitingRelease"),
            "importer_penalty_jt": penalty, "express_contracts": ie.get("kontrak_ekspres"),
            "express_cost_jt": ie.get("biaya_ekspres_juta"),
            "vessel_cost_jt": round(vessel_cost, 2), "yard_dwell_cost_jt": round(yard_cost, 2), "crane_cost_jt": round(crane_cost, 2), "truck_cost_jt": round(truck_cost, 2),
            "negotiations": spk.get("negotiations"), "negotiations_agreed": spk.get("negotiations_agreed"),
            "negotiation_rounds": spk.get("negotiation_rounds_total"), "mediations": spk.get("mediations"),
            "berth_plan_versions": spk.get("plan_versions"), "berth_changes": spk.get("berth_changes_vs_first_plan"),
            "truck_plan_versions": gck.get("PlanVersions"),
            "declarations": cuk.get("declarations"), "released": cuk.get("released"),
            "awaiting_officer": cuk.get("awaiting_officer"), "on_hold": cuk.get("on_hold"),
            "lane_red": cuk.get("lane_red"), "lane_yellow": cuk.get("lane_yellow"), "lane_green": cuk.get("lane_green"),
            "compliance_violations": violations, "human_decisions": cuk.get("human_decisions"),
            "docs_complete": ie.get("dokumen_lengkap"),
        },
        "local_utilities": utilities,
        "local_kpi": {"oceanline": ol.get("localKpi"), "smartport": spk, "globalcargo": gck, "importexport": ie,
                      "customs": cuk},
        "berth_plan": sp.get("plan", []),
        "weights": WEIGHTS, "reference": REF,
    }


def snapshot() -> dict:
    c = collect()
    res = compute(c["raw"])
    res["errors"] = c["errors"]
    return res

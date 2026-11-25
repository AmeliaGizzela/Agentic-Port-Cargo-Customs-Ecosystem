"""Berth Allocation Problem (BAP) - Mixed-Integer Programming time-indexed (SciPy milp / HiGHS).

Variabel  x[v,b,t] = 1 jika kapal v mulai sandar di dermaga b pada slot t (slot 30 menit).
Kendala   (1) setiap kapal tepat satu (b,t)
          (2) kapasitas: pada setiap dermaga & slot paling banyak satu kapal (mempertimbangkan durasi p[v,b,t])
          (3) kompatibilitas fisik: LOA <= panjang dermaga, draft <= kedalaman
          (4) t >= ETA (dan >= waktu sekarang untuk kapal yang belum sandar); kapal yang sudah sandar dibekukan
Tujuan    min sum c[v,b,t] x[v,b,t], dengan c bergantung strategi:
          - global (ekosistem): biaya tunggu kapal (OceanLine/eksternal) + penalti telat berangkat
                               + biaya crane (lembur malam) + biaya stabilitas rencana
          - local (SmartPort saja): penalti kontrak tunggu > 2 jam + biaya crane (tanpa memikirkan biaya pelayaran)
Durasi p[v,b,t] dihitung dengan integrasi profil produktivitas crane (mis. crane rusak sebagian).
"""
from __future__ import annotations

import math
import time

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from data.master_data import COSTS

SLOT = 0.5


def productivity_factor(berth: dict, t: float) -> float:
    f = 1.0
    for d in berth.get("degradations", []):
        if d["from_h"] <= t < d["to_h"]:
            f = min(f, d["factor"])
    return f


def processing_hours(containers: int, berth: dict, start_h: float) -> float:
    """Durasi sandar (jam) = 0,5 sandar + bongkar (integrasi produktivitas) + 0,5 lepas sandar."""
    base_rate = berth["cranes"] * berth["crane_rate"]
    if not berth.get("degradations"):
        return containers / base_rate + 1.0
    t, remaining, step = start_h + 0.5, float(containers), 0.05
    while remaining > 1e-9:
        rate = base_rate * productivity_factor(berth, t)
        moved = rate * step
        if moved >= remaining:
            t += remaining / rate
            remaining = 0
        else:
            remaining -= moved
            t += step
    return (t - start_h) + 0.5


def night_fraction(start_h: float, dur_h: float) -> float:
    if dur_h <= 0:
        return 0.0
    n, steps = 0, max(1, int(dur_h / 0.25))
    for i in range(steps):
        hour = (start_h + (i + 0.5) * dur_h / steps) % 24
        if hour >= 22 or hour < 6:
            n += 1
    return n / steps


def compatible(v: dict, b: dict) -> bool:
    return v["loa_m"] <= b["length_m"] and v["draft_m"] <= b["max_draft_m"]


def cost_terms(v: dict, b: dict, start: float, proc: float, now_h: float, mode: str, prev: dict | None,
               policy: dict) -> dict:
    end = start + proc
    wait = max(0.0, start - v["eta_h"])
    late_dep = max(0.0, end - v["planned_departure_h"])
    crane = (proc - 1.0) * b["cranes"] * COSTS["crane_jt_per_crane_h"] * (
        1 + (COSTS["night_factor"] - 1) * night_fraction(start, proc))
    doc_factor = policy.get("pending_doc_priority_factor", 0.3) if v.get("doc_status") == "INCOMPLETE" else 1.0
    if v.get("is_partner_agent"):
        notice = v["eta_h"] - now_h
        wait_rate = COSTS["vessel_wait_jit_jt_per_h"] if notice >= 6 else COSTS["vessel_wait_jt_per_h"]
    else:
        wait_rate = COSTS["external_wait_jt_per_h"]
    contract = max(0.0, wait - 2.0) * COSTS["berth_contract_penalty_jt_per_h"]
    moved = bool(prev and prev["berth_id"] != b["berth_id"])
    stability = (COSTS["replan_disruption_jt"] if moved else 0.0) + (0.2 * abs(start - prev["start_h"]) if prev else 0.0)
    if mode == "local":
        total = contract * doc_factor + crane
    else:  # global / ekosistem
        total = wait * wait_rate * doc_factor + late_dep * COSTS["late_departure_jt_per_h"] + crane + stability
    return {"total": total, "wait": wait, "late_dep": late_dep, "crane": crane, "contract": contract,
            "moved": moved, "wait_cost": wait * wait_rate}


def solve(vessels: list[dict], berths: list[dict], now_h: float, mode: str = "global",
          prev_plan: dict | None = None, frozen: set | None = None, force: dict | None = None,
          policy: dict | None = None, horizon_h: float = 48.0, time_limit: float = 20.0) -> dict:
    """vessels: [{vessel_id, eta_h, containers, loa_m, draft_m, planned_departure_h, is_partner_agent, doc_status}]
    prev_plan: {vessel_id: {berth_id, start_h}}; frozen: vessel_id yang tidak boleh dipindah; force: {vessel_id: berth_id}."""
    t0 = time.perf_counter()
    policy = policy or {}
    prev_plan = prev_plan or {}
    frozen = frozen or set()
    force = force or {}
    S = int(horizon_h / SLOT)
    now_slot = math.ceil(round(now_h / SLOT, 6) - 1e-9)
    var, costs = [], []
    for vi, v in enumerate(vessels):
        prev = prev_plan.get(v["vessel_id"])
        for bi, b in enumerate(berths):
            if not compatible(v, b):
                continue
            if v["vessel_id"] in force and force[v["vessel_id"]] != b["berth_id"]:
                continue
            if v["vessel_id"] in frozen and prev:
                if prev["berth_id"] != b["berth_id"]:
                    continue
                slots = [int(round(prev["start_h"] / SLOT))]
            else:
                first = max(math.ceil(round(v["eta_h"] / SLOT, 6) - 1e-9), now_slot)
                slots = range(first, S)
            for t in slots:
                start = t * SLOT
                proc = processing_hours(v["containers"], b, start)
                p = math.ceil(round(proc / SLOT, 6) - 1e-9)
                ct = cost_terms(v, b, start, p * SLOT, now_h, mode, prev, policy)
                var.append((vi, bi, t, p))
                costs.append(ct["total"] + 1e-3 * t)
    if not var:
        return {"status": "infeasible", "plan": [], "objective": None}
    n = len(var)
    S_ext = S + max(p for *_, p in var) + 2
    rows, cols = [], []
    for j, (vi, _bi, _t, _p) in enumerate(var):
        rows.append(vi)
        cols.append(j)
    A1 = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(vessels), n))
    rows, cols = [], []
    for j, (_vi, bi, t, p) in enumerate(var):
        for tau in range(t, min(S_ext, t + p)):
            rows.append(bi * S_ext + tau)
            cols.append(j)
    A2 = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(berths) * S_ext, n))
    res = milp(c=np.array(costs), constraints=[LinearConstraint(A1.tocsr(), 1, 1), LinearConstraint(A2.tocsr(), 0, 1)],
               integrality=np.ones(n), bounds=Bounds(0, 1), options={"time_limit": time_limit, "disp": False})
    ms = (time.perf_counter() - t0) * 1000
    if res.x is None:
        return {"status": f"infeasible ({res.message})", "plan": [], "objective": None, "solve_ms": ms}
    plan = []
    for j in np.where(res.x > 0.5)[0]:
        vi, bi, t, p = var[j]
        v, b = vessels[vi], berths[bi]
        start = t * SLOT
        ct = cost_terms(v, b, start, p * SLOT, now_h, mode, prev_plan.get(v["vessel_id"]), policy)
        plan.append({"vessel_id": v["vessel_id"], "berth_id": b["berth_id"], "start_h": start, "end_h": start + p * SLOT,
                     "wait_h": round(ct["wait"], 2), "late_departure_h": round(ct["late_dep"], 2),
                     "crane_cost_jt": round(ct["crane"], 2), "moved": ct["moved"]})
    plan.sort(key=lambda r: (r["start_h"], r["berth_id"]))
    return {"status": "optimal" if res.status == 0 else res.message, "plan": plan,
            "objective": round(float(res.fun), 3), "solve_ms": round(ms, 1), "n_vars": n,
            "n_constraints": int(A1.shape[0] + A2.shape[0]), "mode": mode}


def fcfs_shift(vessels: list[dict], berths: list[dict], prev_plan: dict, now_h: float) -> dict:
    """Strategi statis: tidak ada re-optimasi. Kapal tetap di dermaga & urutan semula; mulai = max(ETA, dermaga kosong)."""
    bmap = {b["berth_id"]: b for b in berths}
    vmap = {v["vessel_id"]: v for v in vessels}
    plan = []
    for bid in bmap:
        seq = sorted([(vid, p) for vid, p in prev_plan.items() if p["berth_id"] == bid], key=lambda x: x[1]["start_h"])
        free = 0.0
        for vid, p in seq:
            v = vmap[vid]
            if p["start_h"] <= now_h:  # sudah berjalan
                start = p["start_h"]
            else:
                start = max(p["start_h"], math.ceil(round(v["eta_h"] / SLOT, 6) - 1e-9) * SLOT, free)
            proc = processing_hours(v["containers"], bmap[bid], start)
            end = start + math.ceil(round(proc / SLOT, 6) - 1e-9) * SLOT
            free = end
            plan.append({"vessel_id": vid, "berth_id": bid, "start_h": start, "end_h": end,
                         "wait_h": round(max(0.0, start - v["eta_h"]), 2),
                         "late_departure_h": round(max(0.0, end - v["planned_departure_h"]), 2),
                         "crane_cost_jt": 0.0, "moved": False})
    plan.sort(key=lambda r: (r["start_h"], r["berth_id"]))
    return {"status": "fcfs (no re-optimization)", "plan": plan, "objective": None, "solve_ms": 0.0, "mode": "static"}


def sp_utility(plan: list[dict], vessels: list[dict], berths: list[dict], prev_plan: dict | None = None) -> dict:
    """U_SP = pendapatan THC - biaya crane - penalti kontrak sandar - biaya gangguan operasional."""
    bmap = {b["berth_id"]: b for b in berths}
    vmap = {v["vessel_id"]: v for v in vessels}
    revenue = sum(vmap[p["vessel_id"]]["containers"] for p in plan) * COSTS["thc_jt_per_container"]
    crane = contract = disruption = 0.0
    for p in plan:
        b = bmap[p["berth_id"]]
        dur = p["end_h"] - p["start_h"]
        crane += (dur - 1.0) * b["cranes"] * COSTS["crane_jt_per_crane_h"] * (
            1 + (COSTS["night_factor"] - 1) * night_fraction(p["start_h"], dur))
        contract += max(0.0, p["wait_h"] - 2.0) * COSTS["berth_contract_penalty_jt_per_h"]
        if prev_plan and p["vessel_id"] in prev_plan and prev_plan[p["vessel_id"]]["berth_id"] != p["berth_id"]:
            disruption += COSTS["replan_disruption_jt"]
    cost = crane + disruption
    return {"revenue_jt": round(revenue, 2), "cost_jt": round(cost, 2), "risk_penalty_jt": round(contract, 2),
            "utility": round(revenue - cost - contract, 2)}


def plan_metrics(plan: list[dict], vessels: list[dict], berths: list[dict], horizon_h: float = 24.0) -> dict:
    if not plan:
        return {}
    waits = [p["wait_h"] for p in plan]
    busy = sum(min(p["end_h"], horizon_h) - min(p["start_h"], horizon_h) for p in plan)
    return {"avg_wait_h": round(float(np.mean(waits)), 2), "max_wait_h": round(float(np.max(waits)), 2),
            "vessels_wait_gt_2h": int(sum(w > 2 for w in waits)),
            "late_departures": int(sum(p["late_departure_h"] > 0 for p in plan)),
            "berth_utilization_24h": round(busy / (len(berths) * horizon_h), 3),
            "makespan_h": round(max(p["end_h"] for p in plan), 2)}

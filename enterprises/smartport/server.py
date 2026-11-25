"""SmartPort Terminal - server API (port 8002).

Model data: relasional ternormalisasi (berth, vessel_call, berth_plan, plan_meta, container_batch, negotiation).
Format API native: snake_case, waktu lokal WIB "YYYY-MM-DD HH:MM".
"""
from __future__ import annotations

from fastapi import Depends
from pydantic import BaseModel

from common import timeutil as tu
from common.agent_base import EnterpriseContext
from common.config import enterprise_db_path
from common.db import Database
from common.enterprise_base import create_enterprise_app, make_guard, register_tool
from enterprises.smartport import berth_mip as bm
from enterprises.smartport.agents import BerthPlanningAgent, OperationsMonitorAgent, YardAgent
from enterprises.smartport.repo import SmartPortRepo

EID = "smartport"
db = Database(enterprise_db_path(EID))
ctx = EnterpriseContext(EID, db, audit_table="audit_log")
repo = SmartPortRepo(db)


def reset() -> None:
    db.drop_all()
    repo.__init__(db)
    repo.seed()


if not db.scalar("SELECT COUNT(*) FROM berth"):
    reset()

agents = [BerthPlanningAgent(ctx, repo), YardAgent(ctx, repo), OperationsMonitorAgent(ctx, repo)]
app = create_enterprise_app(ctx, agents, reset,
                            description="SmartPort Terminal - Berth scheduling (MIP), yard & container release. "
                                        "Format native: snake_case + waktu lokal WIB.")


def _plan_rows(version: int | None = None) -> list[dict]:
    v = version or repo.current_version()
    return db.query("SELECT p.*, c.vessel_name, c.carrier, c.eta_current, c.containers FROM berth_plan p "
                    "JOIN vessel_call c ON c.vessel_id=p.vessel_id WHERE plan_version=? ORDER BY start_time", (v,))


@app.get("/api/berths", tags=["native"])
def berths(consumer: str = Depends(make_guard(ctx, "berths"))):
    return {"berths": repo.berths()}


@app.get("/api/vessel-calls", tags=["native"])
def vessel_calls(consumer: str = Depends(make_guard(ctx, "vessel_calls"))):
    return {"vessel_calls": db.query("SELECT * FROM vessel_call ORDER BY eta_current")}


@app.get("/api/berth-plan", tags=["native"])
def berth_plan(version: int | None = None, consumer: str = Depends(make_guard(ctx, "berth_plan"))):
    return {"plan_version": version or repo.current_version(), "rows": _plan_rows(version)}


@app.get("/api/berth-plan/history", tags=["native"])
def plan_history(consumer: str = Depends(make_guard(ctx, "berth_plan"))):
    metas = db.query("SELECT * FROM plan_meta ORDER BY plan_version")
    return {"versions": metas, "rows": db.query("SELECT * FROM berth_plan ORDER BY plan_version, start_time")}


@app.get("/api/yard/container-batches", tags=["native"])
def container_batches(consumer: str = Depends(make_guard(ctx, "yard"))):
    return {"container_batches": repo.batches(), "yard_blocks": db.query("SELECT * FROM yard_block")}


@app.get("/api/yard/availability", tags=["native"])
def availability(consumer: str = Depends(make_guard(ctx, "availability"))):
    rows = ctx.agents["yard_agent"].compute_availability()
    if consumer == "importexport":
        rows = [r for r in rows if r["consignee"] == "PT ImportExport Corp"]
    return {"availability": rows}


@app.get("/api/negotiations", tags=["native"])
def negotiations(consumer: str = Depends(make_guard(ctx, "negotiations"))):
    return {"negotiations": repo.negotiations()}


class WhatIf(BaseModel):
    mode: str = "global"
    eta_overrides: dict[str, float] = {}


@app.post("/api/planner/what-if", tags=["native"])
def what_if(body: WhatIf, consumer: str = Depends(make_guard(ctx, "planner"))):
    """Simulasi MIP tanpa mempublikasikan rencana (alat bantu Port Planner)."""
    vessels = repo.vessels()
    for v in vessels:
        if v["vessel_id"] in body.eta_overrides:
            v["eta_h"] = float(body.eta_overrides[v["vessel_id"]])
    berths_ = repo.berths()
    prev = repo.current_plan()
    if body.mode == "static" and prev:
        res = bm.fcfs_shift(vessels, berths_, prev, ctx.now())
    else:
        res = bm.solve(vessels, berths_, ctx.now(), body.mode, prev, policy=ctx.governance["decision_policy"])
    res["metrics"] = bm.plan_metrics(res["plan"], vessels, berths_)
    res["sp_utility"] = bm.sp_utility(res["plan"], vessels, berths_, prev)
    return res


@app.get("/api/shared/berth-plan", tags=["shared"])
def shared_plan(consumer: str = Depends(make_guard(ctx, "berth_plan_shared"))):
    rows = _plan_rows()
    if consumer == "oceanline":
        rows = [r for r in rows if r["carrier"] == "OceanLine Shipping"]
    return {"plan_version": repo.current_version(), "generated_at": tu.to_local(ctx.now()),
            "assignments": [{"vessel_id": r["vessel_id"], "vessel_name": r["vessel_name"], "carrier": r["carrier"],
                             "berth_id": r["berth_id"], "eta": r["eta_current"], "start_time": r["start_time"],
                             "end_time": r["end_time"], "wait_h": r["wait_h"], "status": r["status"]} for r in rows]}


@app.get("/api/shared/kpi", tags=["shared"])
def shared_kpi(consumer: str = Depends(make_guard(ctx, "kpi"))):
    vessels, berths_, plan = repo.vessels(), repo.berths(), repo.current_plan()
    rows = [{"vessel_id": vid, **p} for vid, p in plan.items()]
    for r in rows:
        v = next(x for x in vessels if x["vessel_id"] == r["vessel_id"])
        r["late_departure_h"] = max(0.0, r["end_h"] - v["planned_departure_h"])
        r["wait_h"] = max(0.0, r["start_h"] - v["eta_h"])
    metrics = bm.plan_metrics(rows, vessels, berths_) if rows else {}
    first = db.one("SELECT plan_version FROM plan_meta ORDER BY plan_version LIMIT 1")
    first_plan = {r["vessel_id"]: r["berth_id"] for r in db.query(
        "SELECT * FROM berth_plan WHERE plan_version=?", (first["plan_version"],))} if first else {}
    util = bm.sp_utility(rows, vessels, berths_, None) if rows else {"revenue_jt": 0, "cost_jt": 0,
                                                                     "risk_penalty_jt": 0, "utility": 0}
    from data.master_data import COSTS
    gangs = sum(1 for b in berths_ if b["status"] == "EXTRA_GANG")
    if gangs:  # biaya mobilisasi crane/gang tambahan hasil negosiasi
        util = dict(util, cost_jt=round(util["cost_jt"] + gangs * COSTS["extra_gang_jt"], 2),
                    utility=round(util["utility"] - gangs * COSTS["extra_gang_jt"], 2))
    negs = repo.negotiations()
    wait_cost = 0.0
    for r in rows:
        v = next(x for x in vessels if x["vessel_id"] == r["vessel_id"])
        rate = COSTS["vessel_wait_jit_jt_per_h"] if v["is_partner_agent"] else COSTS["external_wait_jt_per_h"]
        wait_cost += r["wait_h"] * rate + r["late_departure_h"] * COSTS["late_departure_jt_per_h"]
    return {"enterprise": EID, "localKpi": {
        **metrics, "plan_versions": repo.current_version(),
        "berth_changes_vs_first_plan": sum(1 for r in rows if first_plan.get(r["vessel_id"]) not in (None, r["berth_id"])),
        "negotiations": len(negs), "negotiations_agreed": sum(n["status"] == "AGREED" for n in negs),
        "negotiation_rounds_total": sum(n["round"] for n in negs),
        "mediations": sum(any(x.get("action") == "MEDIATE" for x in n["log"]) for n in negs),
        "vessel_wait_and_delay_cost_jt": round(wait_cost, 2),
        "crane_cost_jt": util["cost_jt"], "extra_gangs": gangs},
        "utility": {"revenueJt": util["revenue_jt"], "costJt": util["cost_jt"],
                    "riskPenaltyJt": util["risk_penalty_jt"], "U": util["utility"]},
        "plan": [{"vessel_id": r["vessel_id"], "berth_id": r["berth_id"], "start_h": r["start_h"], "end_h": r["end_h"],
                  "wait_h": round(r["wait_h"], 2), "late_departure_h": round(r["late_departure_h"], 2)} for r in rows]}


register_tool(ctx, "get_berth_plan", "Rencana sandar aktif (shared view)", {"type": "object", "properties": {}},
              lambda: {"plan_version": repo.current_version(), "assignments": repo.current_plan()})
register_tool(ctx, "get_container_availability", "Ketersediaan kontainer untuk diangkut",
              {"type": "object", "properties": {"shipment_id": {"type": "string"}}},
              lambda shipment_id=None: {"availability": [a for a in ctx.agents["yard_agent"].compute_availability()
                                                          if shipment_id in (None, a["shipment_id"])]})

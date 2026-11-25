"""GlobalCargo Logistics - server API (port 8003).

Model data: SQL dengan konvensi PascalCase; waktu disimpan sebagai Unix epoch.
Format API native: envelope {"Status": "OK", "Timestamp": <epoch>, "Data": {...}} dengan field PascalCase.
"""
from __future__ import annotations

import time

from fastapi import Depends

from common import timeutil as tu
from common.agent_base import EnterpriseContext
from common.config import enterprise_db_path
from common.db import Database
from common.enterprise_base import create_enterprise_app, make_guard, register_tool
from data.master_data import COSTS
from enterprises.globalcargo import vrp
from enterprises.globalcargo.agents import CommercialAgent, FleetPlanningAgent, GlobalCargoRepo, RouteAgent

EID = "globalcargo"
db = Database(enterprise_db_path(EID))
ctx = EnterpriseContext(EID, db, audit_table="AuditLog")
repo = GlobalCargoRepo(db)


def reset() -> None:
    db.drop_all()
    repo.__init__(db)
    repo.seed()


if not db.scalar("SELECT COUNT(*) FROM Truck"):
    reset()

agents = [FleetPlanningAgent(ctx, repo), RouteAgent(ctx, repo), CommercialAgent(ctx, repo)]
app = create_enterprise_app(ctx, agents, reset,
                            description="GlobalCargo Logistics - Truck allocation (VRPTW), route graph & container pickup. "
                                        "Format native: envelope {Status, Data} PascalCase + epoch.")


def envelope(data) -> dict:
    return {"Status": "OK", "Timestamp": int(time.time()), "SimEpoch": tu.to_epoch(ctx.now()), "Data": data}


@app.get("/gc/v2/Fleet", tags=["native"])
def fleet(consumer: str = Depends(make_guard(ctx, "Fleet"))):
    return envelope({"Trucks": repo.trucks()})


@app.get("/gc/v2/Jobs", tags=["native"])
def jobs(consumer: str = Depends(make_guard(ctx, "Jobs"))):
    return envelope({"Jobs": repo.jobs()})


@app.get("/gc/v2/TripPlan", tags=["native"])
def trip_plan(consumer: str = Depends(make_guard(ctx, "TripPlan"))):
    trips = db.query("SELECT t.*, j.ShipmentRef, j.Customer, j.DestinationNode, j.ReadyEpoch, j.DischargeEpoch, "
                     "j.DeadlineEpoch, k.Type AS TruckType FROM TripPlan t JOIN TransportJob j ON j.JobId=t.JobId "
                     "JOIN Truck k ON k.TruckId=t.TruckId WHERE PlanVersion=? ORDER BY t.TruckId, t.Seq",
                     (repo.current_version(),))
    return envelope({"PlanVersion": repo.current_version(), "Trips": trips,
                     "Versions": db.query("SELECT * FROM PlanMeta ORDER BY PlanVersion")})


@app.get("/gc/v2/RoadNetwork", tags=["native"])
def road_network(consumer: str = Depends(make_guard(ctx, "RoadNetwork"))):
    G = repo.graph()
    tt = vrp.travel_table(G, {"WH_CIKARANG", "WH_KARAWANG", "WH_BEKASI", "WH_SUNTER"})
    return envelope({"Nodes": repo.nodes(), "Edges": repo.edges(),
                     "Routes": {k: {"Minutes": v.get("minutes"), "Path": v.get("path")} for k, v in tt.items()}})


@app.get("/gc/v2/Route", tags=["native"])
def route(From: str = "PORT", To: str = "WH_CIKARANG", consumer: str = Depends(make_guard(ctx, "RoadNetwork"))):
    minutes, path = vrp.shortest(repo.graph(), From, To)
    return envelope({"From": From, "To": To, "Minutes": minutes, "Path": path})


@app.get("/gc/v2/Contracts", tags=["native"])
def contracts(consumer: str = Depends(make_guard(ctx, "Contracts"))):
    return envelope({"Contracts": db.query("SELECT * FROM Contract"), "Quotes": db.query("SELECT * FROM Quote")})


@app.get("/gc/v2/Shared/DeliveryStatus", tags=["shared"])
def delivery_status(consumer: str = Depends(make_guard(ctx, "DeliveryStatus"))):
    fleet_agent = ctx.agents["fleet_planning_agent"]
    customer = "PT ImportExport Corp" if consumer == "importexport" else None
    rows = fleet_agent.delivery_summary(customer)
    return envelope({"Shipments": [{
        "ShipmentRef": r["shipment_id"], "Customer": r["customer"], "Containers": r["containers"],
        "Scheduled": r["scheduled"], "WaitingRelease": r["waiting_release"], "LateContainers": r["late_containers"],
        "FirstPickupEpoch": tu.to_epoch(r["first_pickup_h"]) if r["first_pickup_h"] is not None else None,
        "LastDeliveryEpoch": tu.to_epoch(r["last_delivery_h"]) if r["last_delivery_h"] is not None else None,
        "DeadlineEpoch": tu.to_epoch(r["deadline_h"]), "Express": r["express"], "Status": r["status"]} for r in rows]})


@app.get("/gc/v2/Shared/Kpi", tags=["shared"])
def kpi(consumer: str = Depends(make_guard(ctx, "Kpi"))):
    trips = db.query("SELECT t.*, j.ShipmentRef, j.DischargeEpoch, j.ReadyEpoch, j.DeadlineEpoch, j.DestinationNode, "
                     "k.Type AS TruckType FROM TripPlan t JOIN TransportJob j ON j.JobId=t.JobId "
                     "JOIN Truck k ON k.TruckId=t.TruckId WHERE PlanVersion=?", (repo.current_version(),))
    jobs_all = repo.jobs()
    contracts_ = {c["ShipmentRef"]: c for c in db.query("SELECT * FROM Contract WHERE Type='STANDARD'")}
    express = {c["ShipmentRef"]: c for c in db.query("SELECT * FROM Contract WHERE Type='EXPRESS'")}
    revenue = cost = sla = overtime = 0.0
    dwell, late_ch, on_time = [], 0.0, 0
    for t in trips:
        pick, dl = tu.from_epoch(t["PickupEpoch"]), tu.from_epoch(t["DeliveredEpoch"])
        c = contracts_.get(t["ShipmentRef"])
        revenue += c["PricePerTrip"] if c else 0.0
        revenue += express[t["ShipmentRef"]]["Surcharge"] if t["ShipmentRef"] in express else 0.0
        cost += COSTS["subcon_trip_cost_jt"] if t["TruckType"] == "SUBCON" else COSTS["truck_trip_cost_jt"]
        night = vrp._night_h(pick, tu.from_epoch(t["DoneEpoch"]))
        overtime += night * COSTS["truck_overtime_jt_per_h"]
        late = max(0.0, dl - tu.from_epoch(t["DeadlineEpoch"]))
        late_ch += late
        on_time += int(late <= 1e-6)
        sla += late * COSTS["gc_sla_penalty_jt_per_container_h"]
        if t["DischargeEpoch"]:
            dwell.append(pick - tu.from_epoch(t["DischargeEpoch"]))
    used = len({t["TruckId"] for t in trips})
    own = sum(1 for k in repo.trucks() if k["Type"] == "OWN")
    total_cost = cost + overtime + 0.8 * used
    return envelope({"Enterprise": EID, "LocalKpi": {
        "JobsTotal": len(jobs_all), "TripsPlanned": len(trips),
        "WaitingRelease": sum(1 for j in jobs_all if j["ReadyEpoch"] is None or j["ReadyStatus"] == "HOLD"),
        "OnTimeRate": round(on_time / len(trips), 3) if trips else None,
        "LateContainerHours": round(late_ch, 2), "AvgDwellHours": round(sum(dwell) / len(dwell), 2) if dwell else None,
        "TrucksUsed": used, "OwnFleet": own, "SubconTrips": sum(1 for t in trips if t["TruckType"] == "SUBCON"),
        "PlanVersions": repo.current_version()},
        "Utility": {"RevenueJt": round(revenue, 2), "CostJt": round(total_cost, 2), "RiskPenaltyJt": round(sla, 2),
                    "U": round(revenue - total_cost - sla, 2)}})


register_tool(ctx, "shortest_route", "Rute tercepat di graf jalan (Dijkstra)", {
    "type": "object", "properties": {"from_node": {"type": "string"}, "to_node": {"type": "string"}}},
    lambda from_node="PORT", to_node="WH_CIKARANG": dict(zip(("minutes", "path"), vrp.shortest(repo.graph(), from_node, to_node))))
register_tool(ctx, "get_delivery_status", "Status pengiriman per shipment", {"type": "object", "properties": {}},
              lambda: {"shipments": ctx.agents["fleet_planning_agent"].delivery_summary("PT ImportExport Corp")})

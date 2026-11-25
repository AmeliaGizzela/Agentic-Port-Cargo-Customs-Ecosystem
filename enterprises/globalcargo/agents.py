"""AI Agents GlobalCargo Logistics (A_3) + repositori (skema PascalCase, waktu epoch).

- fleet_planning_agent : VRPTW multi-trip + local search, adaptive replanning saat ketersediaan/armada berubah
- route_agent          : graf jalan & shortest path (networkx); bereaksi terhadap penutupan jalan/kongesti
- commercial_agent     : Contract Net Protocol - penawaran trucking standar & express (truk subkon)
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import uuid

from common import timeutil as tu
from common.agent_base import Agent
from common.config import DATA_DIR
from data.master_data import COSTS, TRIP_PRICE_JT
from enterprises.globalcargo import vrp

SCHEMA = """
CREATE TABLE IF NOT EXISTS Truck(TruckId TEXT PRIMARY KEY, PlateNo TEXT, Type TEXT, Status TEXT, HomeNode TEXT,
  AvailableFromEpoch INTEGER, UnavailableUntilEpoch INTEGER, CostPerTrip REAL, DriverName TEXT, DedicatedShipment TEXT);
CREATE TABLE IF NOT EXISTS RoadNode(NodeId TEXT PRIMARY KEY, Name TEXT, Lat REAL, Lon REAL, Kind TEXT);
CREATE TABLE IF NOT EXISTS RoadEdge(EdgeId INTEGER PRIMARY KEY, FromNode TEXT, ToNode TEXT, Minutes REAL, RoadType TEXT,
  Status TEXT, CongestionFactor REAL);
CREATE TABLE IF NOT EXISTS Contract(ContractId TEXT PRIMARY KEY, Customer TEXT, ShipmentRef TEXT, DestinationNode TEXT,
  DeadlineEpoch INTEGER, Containers INTEGER, Type TEXT, PricePerTrip REAL, Surcharge REAL, PenaltyRate REAL, Status TEXT);
CREATE TABLE IF NOT EXISTS TransportJob(JobId TEXT PRIMARY KEY, ShipmentRef TEXT, Customer TEXT, ContainerSeq INTEGER,
  DestinationNode TEXT, DischargeEpoch INTEGER, ReadyEpoch INTEGER, DeadlineEpoch INTEGER, ReadyStatus TEXT,
  PriorityWeight REAL, Express INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS PendingAvailability(ShipmentRef TEXT PRIMARY KEY, PayloadJson TEXT);
CREATE TABLE IF NOT EXISTS TripPlan(PlanVersion INTEGER, JobId TEXT, TruckId TEXT, Seq INTEGER, PickupEpoch INTEGER,
  DeliveredEpoch INTEGER, DoneEpoch INTEGER, PRIMARY KEY(PlanVersion, JobId));
CREATE TABLE IF NOT EXISTS PlanMeta(PlanVersion INTEGER PRIMARY KEY, CreatedSimEpoch INTEGER, TriggerName TEXT,
  Strategy TEXT, Mode TEXT, Objective REAL, SolveMs REAL, TrucksUsed INTEGER, LateContainers INTEGER, Evaluations INTEGER);
CREATE TABLE IF NOT EXISTS Quote(QuoteId TEXT PRIMARY KEY, RequestId TEXT, Customer TEXT, Kind TEXT, PayloadJson TEXT,
  Status TEXT, CreatedSimEpoch INTEGER);
"""


class GlobalCargoRepo:
    def __init__(self, db):
        self.db = db
        db.script(SCHEMA)

    def seed(self) -> None:
        d = DATA_DIR / "globalcargo"
        with open(d / "trucks.csv", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                self.db.execute("INSERT INTO Truck VALUES (?,?,?,?,?,?,?,?,?,?)", (
                    r["TruckId"], r["PlateNo"], r["Type"], r["Status"], r["HomeNode"], int(r["AvailableFromEpoch"]),
                    None, float(r["CostPerTrip"]), r["DriverName"], None))
        with open(d / "road_nodes.csv", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                self.db.execute("INSERT INTO RoadNode VALUES (?,?,?,?,?)",
                                (r["NodeId"], r["Name"], float(r["Lat"]), float(r["Lon"]), r["Kind"]))
        with open(d / "road_edges.csv", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                self.db.execute("INSERT INTO RoadEdge VALUES (?,?,?,?,?,?,?)", (
                    int(r["EdgeId"]), r["FromNode"], r["ToNode"], float(r["Minutes"]), r["RoadType"], r["Status"],
                    float(r["CongestionFactor"])))
        with open(d / "contracts.csv", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                self.add_contract(r["ContractId"], r["Customer"], r["ShipmentRef"], r["DestinationNode"],
                                  int(r["DeadlineEpoch"]), int(r["Containers"]), r["Type"], float(r["PricePerTrip"]),
                                  float(r["Surcharge"]), float(r["PenaltyRate"]))

    def add_contract(self, cid, customer, shipment, dest, deadline_epoch, containers, ctype, price, surcharge,
                     penalty) -> None:
        self.db.execute("INSERT OR REPLACE INTO Contract VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (cid, customer, shipment, dest, deadline_epoch, containers, ctype, price, surcharge, penalty,
                         "ACTIVE"))
        if ctype == "STANDARD":
            for i in range(containers):
                self.db.execute("INSERT OR IGNORE INTO TransportJob VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                    f"{shipment}-{i + 1:02d}", shipment, customer, i + 1, dest, None, None, deadline_epoch, "UNKNOWN",
                    penalty, 0))

    def nodes(self) -> list[dict]:
        return self.db.query("SELECT * FROM RoadNode")

    def edges(self) -> list[dict]:
        return self.db.query("SELECT * FROM RoadEdge ORDER BY EdgeId")

    def graph(self):
        return vrp.build_graph(self.nodes(), self.edges())

    def trucks(self) -> list[dict]:
        return self.db.query("SELECT * FROM Truck ORDER BY TruckId")

    def jobs(self) -> list[dict]:
        return self.db.query("SELECT * FROM TransportJob ORDER BY JobId")

    def current_version(self) -> int:
        return int(self.db.scalar("SELECT COALESCE(MAX(PlanVersion),0) FROM PlanMeta") or 0)

    def current_trips(self) -> list[dict]:
        v = self.current_version()
        return self.db.query("SELECT * FROM TripPlan WHERE PlanVersion=? ORDER BY TruckId, Seq", (v,)) if v else []


def _sig(obj) -> str:
    return hashlib.md5(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


class FleetPlanningAgent(Agent):
    name = "fleet_planning_agent"
    role = "Planning & Optimization Agent"
    description = "Menjadwalkan truk (multi-trip VRPTW + local search) dan melakukan replanning adaptif."
    skills = [{"id": "vrptw", "name": "Truck scheduling VRPTW", "description": "Konstruksi greedy + local search",
               "tags": ["optimization", "routing"]}]

    def __init__(self, ctx, repo: GlobalCargoRepo):
        self.repo = repo
        super().__init__(ctx)

    def reset_state(self) -> None:
        super().reset_state()
        self.last_sig = None

    def on_container_availability(self, msg: dict) -> dict:
        updated = 0
        for b in msg["payload"]["batches"]:
            jobs = self.repo.db.query("SELECT JobId, ContainerSeq FROM TransportJob WHERE ShipmentRef=?",
                                      (b["shipment_id"],))
            if not jobs:
                self.repo.db.execute("INSERT OR REPLACE INTO PendingAvailability VALUES (?,?)",
                                     (b["shipment_id"], json.dumps(b)))
                continue
            updated += self.apply_batch(b)
        self.beliefs["last_availability"] = msg["payload"].get("reason")
        return self.replan(f"container_availability ({msg['payload'].get('reason')})") if updated else {"updated": 0}

    def apply_batch(self, b: dict) -> int:
        n = 0
        for j in self.repo.db.query("SELECT JobId, ContainerSeq FROM TransportJob WHERE ShipmentRef=?",
                                    (b["shipment_id"],)):
            i = j["ContainerSeq"] - 1
            if i >= len(b["discharge_h"]):
                continue
            ready = b["ready_h"][i]
            self.repo.db.execute("UPDATE TransportJob SET DischargeEpoch=?, ReadyEpoch=?, ReadyStatus=? WHERE JobId=?", (
                tu.to_epoch(b["discharge_h"][i]), tu.to_epoch(ready) if ready is not None else None, b["status"], j["JobId"]))
            n += 1
        return n

    def on_truck_breakdown(self, msg: dict) -> dict:
        p = msg["payload"]
        own = [t for t in self.repo.trucks() if t["Type"] == "OWN" and t["Status"] == "AVAILABLE"]
        ids = p.get("truck_ids") or [t["TruckId"] for t in own[-int(p.get("count", 1)):]]
        for tid in ids:
            self.repo.db.execute("UPDATE Truck SET Status='BREAKDOWN', UnavailableUntilEpoch=? WHERE TruckId=?",
                                 (tu.to_epoch(float(p["to_h"])), tid))
        self.decide("DETECT_DISRUPTION", f"{len(ids)} truk rusak/ditarik hingga {tu.clock(float(p['to_h']))}",
                    [f"Truk: {ids}", "Kapasitas armada turun -> perlu realokasi job"], "Replanning armada")
        return self.replan("truck_breakdown")

    def _truck_inputs(self) -> list[dict]:
        out = []
        for t in self.repo.trucks():
            avail = tu.from_epoch(t["AvailableFromEpoch"])
            if t["Status"] == "BREAKDOWN" and t["UnavailableUntilEpoch"]:
                avail = max(avail, tu.from_epoch(t["UnavailableUntilEpoch"]))
            out.append({"truck_id": t["TruckId"], "type": t["Type"], "available_from_h": avail,
                        "start_node": t["HomeNode"], "dedicated_shipment": t["DedicatedShipment"]})
        return out

    def replan(self, trigger: str) -> dict:
        self.state = "planning"
        self.goal = "Kirim kontainer tepat waktu dengan biaya armada minimum"
        now = self.ctx.now()
        G = self.repo.graph()
        rows = self.repo.jobs()
        tt = vrp.travel_table(G, {r["DestinationNode"] for r in rows} | {"WH_SUNTER"})
        prev = self.repo.current_trips()
        fixed, fixed_ids = [], set()
        for tr in prev:
            pick = tu.from_epoch(tr["PickupEpoch"])
            if pick <= now:
                job = next(r for r in rows if r["JobId"] == tr["JobId"])
                fixed.append({"job_id": tr["JobId"], "truck_id": tr["TruckId"], "pickup_h": pick,
                              "deliver_h": tu.from_epoch(tr["DeliveredEpoch"]), "done_h": tu.from_epoch(tr["DoneEpoch"]),
                              "dest": job["DestinationNode"]})
                fixed_ids.add(tr["JobId"])
        jobs = [{"job_id": r["JobId"], "shipment": r["ShipmentRef"], "dest": r["DestinationNode"],
                 "ready_h": tu.from_epoch(r["ReadyEpoch"]), "deadline_h": tu.from_epoch(r["DeadlineEpoch"]),
                 "priority_weight": r["PriorityWeight"] or 0.0}
                for r in rows if r["ReadyEpoch"] is not None and r["ReadyStatus"] != "HOLD" and r["JobId"] not in fixed_ids]
        waiting = [r["JobId"] for r in rows if r["ReadyEpoch"] is None or r["ReadyStatus"] == "HOLD"]
        trucks = self._truck_inputs()
        if prev and not self.ctx.adaptive:
            mode = "static"
            prev_routes: dict[str, list[str]] = {}
            for tr in prev:
                if tr["JobId"] not in fixed_ids:
                    prev_routes.setdefault(tr["TruckId"], []).append(tr["JobId"])
            planner = vrp.Planner(jobs, trucks, tt, "collaborative", now, fixed)
            res = planner.keep_sequence(prev_routes)
        else:
            mode = "local" if self.ctx.strategy == "local" else "collaborative"
            planner = vrp.Planner(jobs, trucks, tt, mode, now, fixed)
            res = planner.solve() if jobs else {"routes": {}, "trips": {}, "objective": 0, "solve_ms": 0, "evaluations": 0}
        version = self.repo.current_version() + 1
        all_trips = list(res["trips"].values()) + fixed
        seq: dict[str, int] = {}
        for tr in sorted(all_trips, key=lambda x: (x["truck_id"], x["pickup_h"])):
            seq[tr["truck_id"]] = seq.get(tr["truck_id"], 0) + 1
            self.repo.db.execute("INSERT INTO TripPlan VALUES (?,?,?,?,?,?,?)", (
                version, tr["job_id"], tr["truck_id"], seq[tr["truck_id"]], tu.to_epoch(tr["pickup_h"]),
                tu.to_epoch(tr["deliver_h"]), tu.to_epoch(tr["done_h"])))
        late = sum(1 for tr in all_trips if tr["deliver_h"] > tu.from_epoch(
            next(r for r in rows if r["JobId"] == tr["job_id"])["DeadlineEpoch"]) + 1e-6)
        used = len({tr["truck_id"] for tr in all_trips})
        self.repo.db.execute("INSERT INTO PlanMeta VALUES (?,?,?,?,?,?,?,?,?,?)", (
            version, tu.to_epoch(now), trigger, self.ctx.strategy, mode, res["objective"], res["solve_ms"], used, late,
            res["evaluations"]))
        self.beliefs = {"plan_version": version, "jobs_planned": len(all_trips), "waiting_release": len(waiting),
                        "trucks_used": used, "late_containers": late}
        self.decide("PLAN_TRUCKS", f"Pemicu: {trigger}", [
            f"Mode {mode}: {len(jobs)} job direncanakan ulang, {len(fixed)} trip sudah berjalan (dibekukan)",
            f"Rute Dijkstra: PORT->Cikarang {tt.get('WH_CIKARANG', {}).get('minutes', '-')} mnt, "
            f"PORT->Karawang {tt.get('WH_KARAWANG', {}).get('minutes', '-')} mnt",
            f"Objektif {res['objective']} ({res['evaluations']} evaluasi local search, {res['solve_ms']} ms)",
            f"Job menunggu release/hold: {len(waiting)}"],
            f"Rencana truk v{version}: {used} truk, {late} kontainer terlambat",
            kpi={"trucks_used": used, "late_containers": late, "planned": len(all_trips)})
        self.state = "idle"
        self.send_delivery_plans(version)
        return {"plan_version": version, "late": late}

    def delivery_summary(self, customer: str | None = None) -> list[dict]:
        rows = {r["JobId"]: r for r in self.repo.jobs()}
        trips = {t["JobId"]: t for t in self.repo.current_trips()}
        express = {r["ShipmentRef"] for r in self.repo.db.query("SELECT ShipmentRef FROM Contract WHERE Type='EXPRESS'")}
        out: dict[str, dict] = {}
        for jid, r in rows.items():
            if customer and r["Customer"] != customer:
                continue
            s = out.setdefault(r["ShipmentRef"], {"shipment_id": r["ShipmentRef"], "customer": r["Customer"],
                                                  "containers": 0, "scheduled": 0, "late_containers": 0,
                                                  "first_pickup_h": None, "last_delivery_h": None,
                                                  "deadline_h": tu.from_epoch(r["DeadlineEpoch"]),
                                                  "waiting_release": 0, "express": r["ShipmentRef"] in express,
                                                  "destination": r["DestinationNode"]})
            s["containers"] += 1
            t = trips.get(jid)
            if t:
                s["scheduled"] += 1
                pick, dl = tu.from_epoch(t["PickupEpoch"]), tu.from_epoch(t["DeliveredEpoch"])
                s["first_pickup_h"] = pick if s["first_pickup_h"] is None else min(s["first_pickup_h"], pick)
                s["last_delivery_h"] = dl if s["last_delivery_h"] is None else max(s["last_delivery_h"], dl)
                s["late_containers"] += int(dl > s["deadline_h"] + 1e-6)
            else:
                s["waiting_release"] += 1
        for s in out.values():
            if s["waiting_release"]:
                s["status"] = "WAITING_RELEASE"
            elif s["late_containers"]:
                s["status"] = "LATE"
            elif s["last_delivery_h"] is not None and s["last_delivery_h"] > s["deadline_h"] - 1.0:
                s["status"] = "AT_RISK"
            else:
                s["status"] = "ON_TIME"
            s["late_h"] = round(max(0.0, (s["last_delivery_h"] or 0) - s["deadline_h"]), 2) if s["last_delivery_h"] else 0.0
        return sorted(out.values(), key=lambda x: x["shipment_id"])

    def send_delivery_plans(self, version: int) -> None:
        summary = self.delivery_summary("PT ImportExport Corp")
        sig = _sig(summary)
        if sig != self.last_sig:
            self.last_sig = sig
            self.send("importexport.procurement_agent", "INFORM", "delivery_plan",
                      {"plan_version": version, "shipments": summary})


class RouteAgent(Agent):
    name = "route_agent"
    role = "Routing & Monitoring Agent"
    description = "Memelihara graf jalan, menghitung shortest path, bereaksi terhadap penutupan jalan / kongesti."
    skills = [{"id": "shortest_path", "name": "Shortest path Dijkstra", "description": "networkx",
               "tags": ["graph", "routing"]}]

    def __init__(self, ctx, repo: GlobalCargoRepo):
        self.repo = repo
        super().__init__(ctx)

    def on_road_closure(self, msg: dict) -> dict:
        p = msg["payload"]
        before = vrp.travel_table(self.repo.graph(), {"WH_CIKARANG", "WH_KARAWANG", "WH_BEKASI"})
        for a, b in p.get("close_edges", []):
            self.repo.db.execute("UPDATE RoadEdge SET Status='CLOSED' WHERE (FromNode=? AND ToNode=?) OR (FromNode=? AND ToNode=?)",
                                 (a, b, b, a))
        for road_type, factor in p.get("congestion", {}).items():
            self.repo.db.execute("UPDATE RoadEdge SET CongestionFactor=? WHERE RoadType=? AND Status='OPEN'",
                                 (float(factor), road_type))
        after = vrp.travel_table(self.repo.graph(), {"WH_CIKARANG", "WH_KARAWANG", "WH_BEKASI"})
        reasoning = [f"{d}: {before[d]['minutes']:.0f} -> {after[d]['minutes']:.0f} mnt via {' > '.join(after[d]['path'])}"
                     for d in ("WH_CIKARANG", "WH_KARAWANG", "WH_BEKASI")]
        self.beliefs = {"closed": p.get("close_edges"), "congestion": p.get("congestion")}
        self.decide("REROUTE", f"Gangguan jalan: {p.get('description', '')}", reasoning,
                    "Perbarui matriks waktu tempuh (Dijkstra) & minta replanning armada")
        return self.ctx.agents["fleet_planning_agent"].replan("road_closure")

    def on_road_reopen(self, msg: dict) -> dict:
        self.repo.db.execute("UPDATE RoadEdge SET Status='OPEN', CongestionFactor=1.0")
        self.decide("REROUTE", "Jalan dibuka kembali", ["Semua ruas OPEN"], "Replanning armada")
        return self.ctx.agents["fleet_planning_agent"].replan("road_reopen")


class CommercialAgent(Agent):
    name = "commercial_agent"
    role = "Negotiation Agent (Contract Net)"
    description = "Menjawab CFP trucking standar/express, menghitung harga berdasarkan biaya & kapasitas (what-if VRP)."
    skills = [{"id": "contract_net", "name": "Contract Net Protocol", "description": "CFP -> PROPOSE -> ACCEPT",
               "tags": ["negotiation", "pricing"]}]

    def __init__(self, ctx, repo: GlobalCargoRepo):
        self.repo = repo
        super().__init__(ctx)

    @property
    def policy(self) -> dict:
        return self.ctx.governance["decision_policy"]

    def _save_quote(self, request_id: str, customer: str, kind: str, payload: dict, status: str) -> str:
        qid = f"Q-{uuid.uuid4().hex[:6].upper()}"
        self.repo.db.execute("INSERT INTO Quote VALUES (?,?,?,?,?,?,?)",
                             (qid, request_id, customer, kind, json.dumps(payload), status, tu.to_epoch(self.ctx.now())))
        return qid

    def on_trucking_request(self, msg: dict) -> dict:
        p, mt = msg["payload"], msg["message_type"]
        customer = "PT ImportExport Corp"
        if mt == "CFP":
            offers = [{"shipment_id": s["shipment_id"], "containers": s["containers"], "destination": s["destination"],
                       "price_per_trip_jt": TRIP_PRICE_JT[s["destination"]],
                       "total_jt": round(TRIP_PRICE_JT[s["destination"]] * s["containers"], 2)} for s in p["shipments"]]
            self._save_quote(p["request_id"], customer, "STANDARD", {"offers": offers, "request": p}, "PROPOSED")
            self.decide("QUOTE", f"CFP trucking {len(offers)} shipment", [
                "Tarif standar per tujuan (berbasis jarak Dijkstra & biaya trip)",
                f"Kapasitas armada cukup untuk {sum(o['containers'] for o in offers)} kontainer"],
                f"PROPOSE total Rp{sum(o['total_jt'] for o in offers):.1f} jt", conversation_id=msg.get("conversation_id"))
            self.reply(msg, "PROPOSE", {"request_id": p["request_id"], "offers": offers})
            return {"proposed": len(offers)}
        if mt == "ACCEPT":
            req = json.loads(self.repo.db.one("SELECT PayloadJson FROM Quote WHERE RequestId=?",
                                              (p["request_id"],))["PayloadJson"])["request"]
            for s in req["shipments"]:
                if s["shipment_id"] not in p.get("accepted", [x["shipment_id"] for x in req["shipments"]]):
                    continue
                self.repo.add_contract(f"CT-{s['shipment_id']}", customer, s["shipment_id"], s["destination"],
                                       tu.to_epoch(s["deadline_h"]), s["containers"], "STANDARD",
                                       TRIP_PRICE_JT[s["destination"]], 0.0, float(s.get("priority_weight", 0.0)))
                pend = self.repo.db.one("SELECT PayloadJson FROM PendingAvailability WHERE ShipmentRef=?",
                                        (s["shipment_id"],))
                if pend:
                    self.ctx.agents["fleet_planning_agent"].apply_batch(json.loads(pend["PayloadJson"]))
            self.repo.db.execute("UPDATE Quote SET Status='ACCEPTED' WHERE RequestId=?", (p["request_id"],))
            self.decide("CONTRACT", "ACCEPT dari ImportExport", ["Kontrak standar dibuat; job per kontainer dibuat"],
                        f"Kontrak aktif untuk {len(p.get('accepted', []))} shipment", conversation_id=msg.get("conversation_id"))
            return self.ctx.agents["fleet_planning_agent"].replan("new_contracts")
        return {}

    def on_express_request(self, msg: dict) -> dict:
        p, mt = msg["payload"], msg["message_type"]
        fleet: FleetPlanningAgent = self.ctx.agents["fleet_planning_agent"]
        if mt == "CFP":
            proposals, reasoning = [], []
            pool = int(self.policy["subcon_pool_size"]) - self.repo.db.scalar(
                "SELECT COUNT(*) FROM Truck WHERE Type='SUBCON'")
            # alokasikan pool truk subkon yang terbatas ke shipment dengan kesediaan bayar (nilai) tertinggi
            ranked = sorted(p["shipments"], key=lambda s: -s["max_surcharge_per_container_jt"] * s["containers"])
            reasoning.append("Urutan alokasi subkon (nilai kesediaan bayar): " + ", ".join(
                f"{s['shipment_id']}={s['max_surcharge_per_container_jt'] * s['containers']:.1f} jt" for s in ranked))
            for s in ranked:
                if pool <= 0:
                    break
                k = min(pool, max(1, math.ceil(s["late_containers"] / 2)))
                est = self._what_if(s["shipment_id"], k)
                if est is None or est["late_h_after"] >= est["late_h_before"] - 1e-6:
                    reasoning.append(f"{s['shipment_id']}: subkon tidak mempercepat (bottleneck release/bongkar)")
                    continue
                surcharge = round((COSTS["subcon_trip_cost_jt"] - COSTS["truck_trip_cost_jt"]) * k / s["containers"]
                                  + float(self.policy["express_margin_jt_per_container"]), 2)
                proposals.append({"shipment_id": s["shipment_id"], "subcon_trucks": k,
                                  "surcharge_per_container_jt": surcharge,
                                  "surcharge_total_jt": round(surcharge * s["containers"], 2), **est})
                reasoning.append(f"{s['shipment_id']}: what-if +{k} truk subkon -> keterlambatan "
                                 f"{est['late_h_before']} j -> {est['late_h_after']} j; surcharge {surcharge} jt/kontainer")
                pool -= k
            if not proposals:
                self.decide("QUOTE_EXPRESS", "CFP express", reasoning or ["Pool subkon habis"], "REFUSE",
                            conversation_id=msg.get("conversation_id"))
                self.reply(msg, "REFUSE", {"request_id": p["request_id"], "reason": "; ".join(reasoning) or "no capacity"})
                return {"refused": True}
            self._save_quote(p["request_id"], "PT ImportExport Corp", "EXPRESS", {"proposals": proposals}, "PROPOSED")
            self.decide("QUOTE_EXPRESS", "CFP express dari ImportExport", reasoning,
                        f"PROPOSE express untuk {[x['shipment_id'] for x in proposals]}",
                        conversation_id=msg.get("conversation_id"))
            self.reply(msg, "PROPOSE", {"request_id": p["request_id"], "proposals": proposals})
            return {"proposed": len(proposals)}
        if mt == "ACCEPT":
            q = json.loads(self.repo.db.one("SELECT PayloadJson FROM Quote WHERE RequestId=? AND Kind='EXPRESS'",
                                            (p["request_id"],))["PayloadJson"])
            for pr in q["proposals"]:
                if pr["shipment_id"] not in p.get("accepted", []):
                    continue
                self._add_subcon(pr["shipment_id"], pr["subcon_trucks"])
                self.repo.db.execute("UPDATE TransportJob SET Express=1 WHERE ShipmentRef=?", (pr["shipment_id"],))
                c = self.repo.db.one("SELECT * FROM Contract WHERE ShipmentRef=?", (pr["shipment_id"],))
                self.repo.db.execute("INSERT OR REPLACE INTO Contract VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                    f"CTX-{pr['shipment_id']}", c["Customer"], pr["shipment_id"], c["DestinationNode"], c["DeadlineEpoch"],
                    c["Containers"], "EXPRESS", c["PricePerTrip"], pr["surcharge_per_container_jt"], c["PenaltyRate"],
                    "ACTIVE"))
            self.repo.db.execute("UPDATE Quote SET Status='ACCEPTED' WHERE RequestId=? AND Kind='EXPRESS'",
                                 (p["request_id"],))
            self.decide("CONTRACT_EXPRESS", "ACCEPT express", ["Mobilisasi truk subkon (dedicated)"],
                        f"Express aktif: {p.get('accepted')}", conversation_id=msg.get("conversation_id"))
            return fleet.replan("express_contract")
        if mt == "REJECT":
            self.repo.db.execute("UPDATE Quote SET Status='REJECTED' WHERE RequestId=? AND Kind='EXPRESS'",
                                 (p["request_id"],))
            self.decide("CONTRACT_EXPRESS", "REJECT express", [p.get("reason", "")], "Tidak ada perubahan armada",
                        share=False)
        return {}

    def _add_subcon(self, shipment: str, k: int) -> None:
        n0 = self.repo.db.scalar("SELECT COUNT(*) FROM Truck WHERE Type='SUBCON'")
        avail = tu.to_epoch(self.ctx.now() + float(self.policy["subcon_mobilization_h"]))
        for i in range(k):
            self.repo.db.execute("INSERT INTO Truck VALUES (?,?,?,?,?,?,?,?,?,?)", (
                f"S{n0 + i + 1:02d}", f"B {9800 + n0 + i} SUB", "SUBCON", "AVAILABLE", "DEPOT", avail, None,
                COSTS["subcon_trip_cost_jt"], "Subkon", shipment))

    def _what_if(self, shipment: str, k: int) -> dict | None:
        """Simulasi rencana dengan k truk subkon tambahan (tanpa menyimpan)."""
        rows = self.repo.jobs()
        G = self.repo.graph()
        tt = vrp.travel_table(G, {r["DestinationNode"] for r in rows})
        now = self.ctx.now()
        jobs = [{"job_id": r["JobId"], "shipment": r["ShipmentRef"], "dest": r["DestinationNode"],
                 "ready_h": tu.from_epoch(r["ReadyEpoch"]), "deadline_h": tu.from_epoch(r["DeadlineEpoch"]),
                 "priority_weight": r["PriorityWeight"] or 0.0}
                for r in rows if r["ReadyEpoch"] is not None and r["ReadyStatus"] != "HOLD"]
        trips = {t["JobId"]: t for t in self.repo.current_trips()}
        jobs = [j for j in jobs if j["job_id"] not in trips or tu.from_epoch(trips[j["job_id"]]["PickupEpoch"]) > now]
        if not any(j["shipment"] == shipment for j in jobs):
            return None
        trucks = self.ctx.agents["fleet_planning_agent"]._truck_inputs()
        base = vrp.Planner(jobs, trucks, tt, "collaborative", now).solve()
        extra = trucks + [{"truck_id": f"WHATIF{i}", "type": "SUBCON", "start_node": "DEPOT",
                           "available_from_h": now + float(self.policy["subcon_mobilization_h"]),
                           "dedicated_shipment": shipment} for i in range(k)]
        alt = vrp.Planner(jobs, extra, tt, "collaborative", now).solve()

        def late(res):
            dl = [x["deliver_h"] for jid, x in res["trips"].items() if jid.startswith(shipment)]
            deadline = next(j["deadline_h"] for j in jobs if j["shipment"] == shipment)
            return round(max(0.0, max(dl) - deadline), 2) if dl else 0.0, round(max(dl), 2) if dl else None

        lb, eb = late(base)
        la, ea = late(alt)
        return {"late_h_before": lb, "late_h_after": la, "eta_last_before_h": eb, "eta_last_after_h": ea}

"""AI Agents OceanLine Shipping (A_1).

- eta_agent         : monitoring cuaca/posisi + prediksi ETA (ML) -> ETA_UPDATE ke SmartPort
- negotiation_agent : mengevaluasi opsi jadwal sandar dengan utilitas lokal U_OL, ACCEPT / COUNTER_PROPOSE
- manifest_agent    : berbagi manifest (BC 1.1) ke Customs & arrival notice ke ImportExport (data minimization)
"""
from __future__ import annotations

import math

from common import timeutil as tu
from common.agent_base import Agent
from data.master_data import COSTS

NORMAL_WEATHER = {"windKn": 12.0, "waveM": 1.0, "currentKn": 0.2, "congestionIdx": 0.2}


class DocStore:
    """Penyimpanan berorientasi dokumen (JSON) di atas SQLite - model data OceanLine."""

    def __init__(self, db):
        self.db = db
        db.script("""CREATE TABLE IF NOT EXISTS documents(
            collection TEXT, doc_id TEXT, body TEXT, updated_at TEXT, PRIMARY KEY(collection, doc_id))""")

    def put(self, coll: str, doc_id: str, body: dict) -> None:
        import json
        from datetime import datetime
        self.db.execute("INSERT OR REPLACE INTO documents VALUES (?,?,?,?)",
                        (coll, doc_id, json.dumps(body, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")))

    def get(self, coll: str, doc_id: str) -> dict | None:
        import json
        row = self.db.one("SELECT body FROM documents WHERE collection=? AND doc_id=?", (coll, doc_id))
        return json.loads(row["body"]) if row else None

    def find(self, coll: str) -> list[dict]:
        import json
        return [json.loads(r["body"]) for r in
                self.db.query("SELECT body FROM documents WHERE collection=? ORDER BY doc_id", (coll,))]


def own_vessels(store: DocStore) -> list[dict]:
    return [v for v in store.find("vessels") if v.get("ownFleet")]


def current_eta_h(store: DocStore, vessel: dict) -> float:
    pred = store.get("etaPredictions", vessel["vesselId"])
    if pred:
        return tu.from_utc_z(pred["predictedEtaUtc"])
    return tu.from_utc_z(vessel["voyage"]["scheduledEtaUtc"])


def ol_utility(store: DocStore, option_vessels: list[dict], now_h: float, policy: dict) -> dict:
    """U_OL = -(biaya tunggu + penalti terlambat berangkat). Satuan juta Rupiah."""
    total, rows = 0.0, []
    fleet = {v["vesselId"]: v for v in own_vessels(store)}
    for ov in option_vessels:
        v = fleet.get(ov["vessel_id"])
        if v is None:
            continue
        eta = current_eta_h(store, v)
        wait = max(0.0, ov["start_h"] - eta)
        notice = eta - now_h
        rate = COSTS["vessel_wait_jit_jt_per_h"] if notice >= policy.get("jit_notice_h", 6) else COSTS["vessel_wait_jt_per_h"]
        planned_dep = tu.from_utc_z(v["voyage"]["plannedDepartureUtc"])
        late = max(0.0, ov["end_h"] - planned_dep)
        cost = wait * rate + late * COSTS["late_departure_jt_per_h"]
        total += cost
        rows.append({"vessel_id": ov["vessel_id"], "wait_h": round(wait, 2), "late_departure_h": round(late, 2),
                     "cost_jt": round(cost, 2), "wait_rate": rate})
    return {"utility": round(-total, 2), "cost_jt": round(total, 2), "breakdown": rows}


class EtaAgent(Agent):
    name = "eta_agent"
    role = "Monitoring & Prediction Agent"
    description = "Memantau cuaca & posisi kapal, memprediksi ETA dengan Gradient Boosting, menyiarkan ETA_UPDATE."
    skills = [{"id": "predict_eta", "name": "Prediksi ETA kapal", "description": "ML regression delay ETA",
               "tags": ["ml", "eta", "time-series"]},
              {"id": "broadcast_eta", "name": "Broadcast ETA", "description": "Kirim ETA_UPDATE ke terminal",
               "tags": ["a2a"]}]

    def __init__(self, ctx, store: DocStore, model):
        self.store, self.model = store, model
        super().__init__(ctx)

    def _features(self, v: dict, weather: dict, now_h: float) -> dict:
        sched = tu.from_utc_z(v["voyage"]["scheduledEtaUtc"])
        dist = max(0.0, (sched - now_h) * v["voyage"]["plannedSpeedKn"])
        return {"distanceNm": round(dist, 1), "plannedSpeedKn": v["voyage"]["plannedSpeedKn"],
                "windKn": weather["windKn"], "waveM": weather["waveM"], "currentKn": weather["currentKn"],
                "congestionIdx": weather["congestionIdx"], "vesselTeu": v["capacityTeu"]}

    def _predict_and_store(self, v: dict, weather: dict, now_h: float, reason: str) -> dict:
        thr = self.ctx.governance["decision_policy"]["eta_change_threshold_h"]
        feats = self._features(v, weather, now_h)
        pred = self.model.predict(feats)
        sched = tu.from_utc_z(v["voyage"]["scheduledEtaUtc"])
        delay = pred["delayHours"]
        applied = math.floor(delay / 0.5 + 0.5) * 0.5 if delay >= thr else 0.0  # bulatkan ke slot 30 menit terdekat
        eta = sched + applied
        doc = {"vesselId": v["vesselId"], "features": feats, "rawDelayHours": delay, "p10": pred["p10"],
               "p90": pred["p90"], "appliedDelayHours": applied, "scheduledEtaUtc": v["voyage"]["scheduledEtaUtc"],
               "predictedEtaUtc": tu.to_utc_z(eta), "predictedAtSimH": now_h, "reason": reason, "weather": weather}
        self.store.put("etaPredictions", v["vesselId"], doc)
        return doc

    def _canonical(self, v: dict, doc: dict) -> dict:
        return {"vessel_id": v["vesselId"], "name": v["name"], "carrier": v["operator"],
                "eta_h": tu.from_utc_z(doc["predictedEtaUtc"]), "scheduled_eta_h": tu.from_utc_z(doc["scheduledEtaUtc"]),
                "delay_h": doc["appliedDelayHours"], "p10": doc["p10"], "p90": doc["p90"], "loa_m": v["loaM"],
                "draft_m": v["draftM"], "containers": v["cargo"]["importContainers"],
                "planned_departure_h": tu.from_utc_z(v["voyage"]["plannedDepartureUtc"]),
                "pref_berth": v["voyage"]["preferredBerth"]}

    def on_start_of_day(self, msg: dict) -> dict:
        self.goal = "Menyediakan ETA akurat untuk perencanaan dermaga"
        now = self.ctx.now()
        out = []
        for v in own_vessels(self.store):
            doc = self._predict_and_store(v, dict(NORMAL_WEATHER), now, "start_of_day (cuaca normal)")
            out.append(self._canonical(v, doc))
        self.beliefs = {"weather": "normal", "vessels_tracked": len(out)}
        self.decide("PREDICT_ETA", "Awal hari: data AIS & cuaca normal",
                    [f"{o['vessel_id']}: delay mentah {self.store.get('etaPredictions', o['vessel_id'])['rawDelayHours']:.2f} jam"
                     f" -> ETA {tu.clock(o['eta_h'])}" for o in out] +
                    ["Delay < ambang 0,5 jam dianggap sesuai jadwal (mengurangi replanning yang tidak perlu)"],
                    f"Kirim ETA_UPDATE {len(out)} kapal ke SmartPort", kpi={"vessels": len(out)})
        self.send("smartport.berth_planning_agent", "INFORM", "eta_update",
                  {"vessels": out, "source": "GradientBoostingRegressor", "reason": "start_of_day"})
        mf = self.ctx.agents["manifest_agent"]
        mf.share_all()
        return {"eta_updates": len(out)}

    def on_weather_update(self, msg: dict) -> dict:
        p = msg["payload"]
        now = self.ctx.now()
        weather = {k: float(p.get(k, NORMAL_WEATHER[k])) for k in NORMAL_WEATHER}
        targets = p.get("vessels") or [v["vesselId"] for v in own_vessels(self.store)]
        thr = self.ctx.governance["decision_policy"]["eta_change_threshold_h"]
        changed, reasoning = [], []
        for v in own_vessels(self.store):
            if v["vesselId"] not in targets:
                continue
            before = current_eta_h(self.store, v)
            doc = self._predict_and_store(v, weather, now, f"weather_update: {p.get('description', '')}")
            after = tu.from_utc_z(doc["predictedEtaUtc"])
            reasoning.append(f"{v['vesselId']}: jarak {doc['features']['distanceNm']} nm, angin {weather['windKn']} kn, "
                             f"gelombang {weather['waveM']} m -> delay prediksi {doc['rawDelayHours']:.2f} jam "
                             f"(P10-P90 {doc['p10']:.1f}-{doc['p90']:.1f})")
            if abs(after - before) >= thr:
                changed.append(self._canonical(v, doc))
        self.beliefs = {"weather": weather, "affected": targets}
        if not changed:
            self.decide("PREDICT_ETA", "Update cuaca", reasoning, "ETA tidak berubah signifikan - tidak ada broadcast")
            return {"changed": 0}
        self.decide("PREDICT_ETA", f"Update cuaca: {p.get('description', '')}", reasoning,
                    "Broadcast ETA_UPDATE: " + ", ".join(f"{c['vessel_id']} -> {tu.clock(c['eta_h'])} (+{c['delay_h']} j)"
                                                       for c in changed),
                    kpi={"max_delay_h": max(c["delay_h"] for c in changed)})
        self.send("smartport.berth_planning_agent", "ALERT", "eta_update",
                  {"vessels": changed, "source": "GradientBoostingRegressor", "reason": "weather_update"})
        self.ctx.agents["manifest_agent"].arrival_notice([c["vessel_id"] for c in changed])
        return {"changed": len(changed)}


class NegotiationAgent(Agent):
    name = "negotiation_agent"
    role = "Negotiation & Decision Agent"
    description = "Mengevaluasi rencana/opsi sandar dari terminal dengan utilitas lokal U_OL dan bernegosiasi."
    skills = [{"id": "berth_negotiation", "name": "Negosiasi jadwal sandar",
               "description": "Alternating offers dengan konsesi berbasis waktu", "tags": ["negotiation", "utility"]}]

    def __init__(self, ctx, store: DocStore):
        self.store = store
        super().__init__(ctx)

    def _policy(self) -> dict:
        return self.ctx.governance["decision_policy"]

    def on_berth_plan(self, msg: dict) -> dict:
        p = msg["payload"]
        mine = [x for x in p.get("assignments", []) if any(v["vesselId"] == x["vessel_id"] for v in own_vessels(self.store))]
        for a in mine:
            self.store.put("berthWindows", a["vessel_id"], {
                "vesselId": a["vessel_id"], "berthId": a["berth_id"], "startUtc": tu.to_utc_z(a["start_h"]),
                "endUtc": tu.to_utc_z(a["end_h"]), "status": a.get("status"), "planVersion": p.get("plan_version"),
                "receivedAtSimH": self.ctx.now()})
        u = ol_utility(self.store, mine, self.ctx.now(), self._policy())
        self.beliefs = {"plan_version": p.get("plan_version"), "utility_jt": u["utility"]}
        self.decide("EVALUATE_BERTH_PLAN", f"Rencana sandar v{p.get('plan_version')} diterima ({p.get('mode', '')})",
                    [f"{r['vessel_id']}: tunggu {r['wait_h']} j, telat berangkat {r['late_departure_h']} j, biaya Rp{r['cost_jt']} jt"
                     for r in u["breakdown"]], f"Utilitas OceanLine U_OL = {u['utility']} jt", kpi={"U_OL": u["utility"]})
        return {"utility": u["utility"]}

    def on_berth_negotiation(self, msg: dict) -> dict:
        p = msg["payload"]
        mt = msg["message_type"]
        neg = self.store.get("negotiations", p["neg_id"]) or {"negId": p["neg_id"], "rounds": [], "status": "OPEN",
                                                               "trigger": p.get("trigger")}
        if mt == "CONFIRM":
            neg["status"] = "AGREED"
            neg["agreedOption"] = p.get("option_id")
            self.store.put("negotiations", p["neg_id"], neg)
            self.decide("NEGOTIATION_CLOSED", "Konfirmasi kesepakatan dari SmartPort",
                        [f"Opsi {p.get('option_id')} disepakati pada ronde {p.get('round')}"],
                        f"Komit jadwal sandar opsi {p.get('option_id')}", conversation_id=msg.get("conversation_id"))
            return {"closed": True}
        options = p["options"]
        k, K = int(p.get("round", 1)), int(p.get("max_rounds", self._policy()["negotiation_max_rounds"]))
        util = {o["option_id"]: ol_utility(self.store, o["vessels"], self.ctx.now(), self._policy()) for o in options}
        best_id = max(util, key=lambda i: util[i]["utility"])
        u_best = util[best_id]["utility"]
        u_res = -float(self._policy()["max_acceptable_cost_jt"])
        aspiration = u_best - (u_best - u_res) * ((k - 1) / max(K - 1, 1))  # konsesi linear berbasis waktu
        offer = p["offer"]
        u_offer = util[offer]["utility"]
        reasoning = [f"Opsi {oid}: U_OL = {u['utility']} jt (biaya {u['cost_jt']} jt)" for oid, u in util.items()]
        reasoning.append(f"Ronde {k}/{K}: aspirasi = {aspiration:.1f} jt; tawaran {offer} bernilai {u_offer} jt")
        neg["rounds"].append({"round": k, "offer": offer, "utilities": {i: u["utility"] for i, u in util.items()},
                              "aspiration": round(aspiration, 2)})
        if u_offer >= aspiration - 1e-6 or offer == best_id:
            neg["status"] = "ACCEPTED_BY_OL"
            self.store.put("negotiations", p["neg_id"], neg)
            self.decide("NEGOTIATE", f"PROPOSE dari SmartPort ronde {k}", reasoning,
                        f"ACCEPT opsi {offer}", alternatives=list(util.keys()), kpi={"U_OL": u_offer},
                        conversation_id=msg.get("conversation_id"))
            self.reply(msg, "ACCEPT", {"neg_id": p["neg_id"], "option_id": offer, "round": k,
                                       "comment": "Utilitas memenuhi aspirasi ronde ini"})
            return {"accept": offer}
        rejected = set(p.get("rejected_by_terminal", []))
        # counter = opsi terbaik bagi OceanLine yang belum ditolak terminal dan berbeda dari tawaran;
        # bila tidak ada, OceanLine bersikukuh pada opsi terbaiknya (bisa berujung mediasi)
        candidates = [i for i in util if i not in rejected and i != offer
                      and util[i]["utility"] > util[offer]["utility"]]
        counter = max(candidates, key=lambda i: util[i]["utility"]) if candidates else best_id
        neg["status"] = "COUNTERED"
        self.store.put("negotiations", p["neg_id"], neg)
        self.decide("NEGOTIATE", f"PROPOSE dari SmartPort ronde {k}", reasoning,
                    f"COUNTER_PROPOSE opsi {counter} (tolak {offer})", alternatives=list(util.keys()),
                    kpi={"U_OL_offer": u_offer, "U_OL_counter": util[counter]["utility"]},
                    conversation_id=msg.get("conversation_id"))
        self.reply(msg, "COUNTER_PROPOSE", {"neg_id": p["neg_id"], "option_id": counter, "round": k,
                                            "comment": "Tawaran terlalu mahal bagi armada (waktu tunggu / jadwal berangkat)"})
        return {"counter": counter}

    def on_mediation_utility(self, msg: dict) -> dict:
        """Mediator (Control Center) meminta utilitas tiap opsi - dibagikan HANYA ke mediator tepercaya."""
        p = msg["payload"]
        util = {o["option_id"]: ol_utility(self.store, o["vessels"], self.ctx.now(), self._policy())["utility"]
                for o in p["options"]}
        self.reply(msg, "INFORM", {"neg_id": p["neg_id"], "utilities": util,
                                   "reservation": -float(self._policy()["max_acceptable_cost_jt"])})
        return {"shared_with_mediator": True}


class ManifestAgent(Agent):
    name = "manifest_agent"
    role = "Data Sharing Agent"
    description = "Membagikan manifest ke Customs dan arrival notice (B/L milik konsinyi) ke ImportExport."
    skills = [{"id": "share_manifest", "name": "Kirim manifest BC 1.1", "description": "Manifest kargo ke Bea Cukai",
               "tags": ["compliance", "data-sharing"]}]

    def __init__(self, ctx, store: DocStore):
        self.store = store
        super().__init__(ctx)

    def share_all(self) -> None:
        manifests = self.store.find("manifests")
        for v in own_vessels(self.store):
            items = [m for m in manifests if m["vesselId"] == v["vesselId"]]
            eta = current_eta_h(self.store, v)
            self.send("customs.verification_agent", "INFORM", "cargo_manifest", {
                "vessel_id": v["vesselId"], "vessel_name": v["name"], "eta_h": eta,
                "items": [{"bl_no": m["blNo"], "consignee": m["consignee"], "hs_code": m["hsCode"],
                           "containers": m["containers"], "gross_weight_kg": m["grossWeightKg"]} for m in items]})
        self.arrival_notice([v["vesselId"] for v in own_vessels(self.store)])

    def arrival_notice(self, vessel_ids: list[str]) -> None:
        manifests = self.store.find("manifests")
        vessels = []
        for v in own_vessels(self.store):
            if v["vesselId"] not in vessel_ids:
                continue
            bls = [{"bl_no": m["blNo"], "shipment_ref": m["shipmentRef"], "containers": m["containers"]}
                   for m in manifests if m["vesselId"] == v["vesselId"] and m["consignee"] == "PT ImportExport Corp"]
            if bls:
                vessels.append({"vessel_id": v["vesselId"], "name": v["name"], "eta_h": current_eta_h(self.store, v),
                                "bl": bls})
        if vessels:
            self.send("importexport.document_agent", "INFORM", "arrival_notice", {"vessels": vessels})
            self.decide("SHARE_DATA", "Arrival notice", [f"{len(vessels)} kapal membawa B/L milik ImportExport",
                                                         "Data minimization: hanya B/L milik konsinyi tsb yang dikirim"],
                        "Kirim ARRIVAL_NOTICE ke ImportExport", share=False)


def position_now(v: dict, eta_h: float, now_h: float, port=(-6.10, 106.88)) -> tuple[float, float]:
    dist = max(0.0, (eta_h - now_h) * v["voyage"]["plannedSpeedKn"])
    lat0, lon0 = v["position"]["lat"], v["position"]["lon"]
    d0 = math.hypot(lat0 - port[0], lon0 - port[1]) * 60 or 1.0
    f = min(1.0, dist / d0)
    return round(port[0] + (lat0 - port[0]) * f, 4), round(port[1] + (lon0 - port[1]) * f, 4)

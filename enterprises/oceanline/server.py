"""OceanLine Shipping - server API (port 8001).

Model data: document store JSON (koleksi vessels, etaPredictions, manifests, negotiations, berthWindows).
Format API native: camelCase, waktu UTC 'Z', satuan knot & nautical mile.
"""
from __future__ import annotations

import json

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from common import timeutil as tu
from common.agent_base import EnterpriseContext
from common.config import DATA_DIR, enterprise_db_path, model_dir
from common.db import Database
from common.enterprise_base import create_enterprise_app, make_guard, register_tool
from enterprises.oceanline.ais_feed import AisFeed
from enterprises.oceanline.agents import (DocStore, EtaAgent, ManifestAgent, NegotiationAgent, current_eta_h,
                                          ol_utility, own_vessels, position_now)
from enterprises.oceanline.eta_model import EtaModel

EID = "oceanline"
db = Database(enterprise_db_path(EID))
ctx = EnterpriseContext(EID, db, audit_table="auditEvents")
store = DocStore(db)
eta_model = EtaModel(model_dir(EID), DATA_DIR / "oceanline" / "voyage_history.csv").load_or_train()
ais_feed = AisFeed().start()   # umpan AIS nyata (opsional; aktif bila AISSTREAM_API_KEY diset)


def reset() -> None:
    db.drop_all()
    global store
    store.__init__(db)
    for v in json.loads((DATA_DIR / "oceanline" / "fleet.json").read_text(encoding="utf-8")):
        store.put("vessels", v["vesselId"], v)
    for i, m in enumerate(json.loads((DATA_DIR / "oceanline" / "manifests.json").read_text(encoding="utf-8"))):
        store.put("manifests", f"{m['vesselId']}-{m['blNo']}", m)


if not store.find("vessels"):
    reset()

agents = [EtaAgent(ctx, store, eta_model), NegotiationAgent(ctx, store), ManifestAgent(ctx, store)]
app = create_enterprise_app(ctx, agents, reset,
                            description="OceanLine Shipping - Vessel tracking, ETA prediction (ML), cargo manifest. "
                                        "Format native: camelCase + UTC.")


def _vessel_view(v: dict) -> dict:
    eta = current_eta_h(store, v)
    lat, lon = position_now(v, eta, ctx.now())
    out = dict(v)
    out["currentEtaUtc"] = tu.to_utc_z(eta)
    out["livePosition"] = {"lat": lat, "lon": lon, "simTimeUtc": tu.to_utc_z(ctx.now())}
    bw = store.get("berthWindows", v["vesselId"])
    out["berthWindow"] = bw
    return out


@app.get("/api/v1/vessels", tags=["native"])
def list_vessels(consumer: str = Depends(make_guard(ctx, "vessels"))):
    return {"vessels": [_vessel_view(v) for v in store.find("vessels")], "simTimeUtc": tu.to_utc_z(ctx.now())}


@app.get("/api/v1/vessels/{vessel_id}", tags=["native"])
def get_vessel(vessel_id: str, consumer: str = Depends(make_guard(ctx, "vessels"))):
    v = store.get("vessels", vessel_id)
    if not v:
        raise HTTPException(404, "vessel not found")
    return _vessel_view(v)


@app.get("/api/v1/ais/live", tags=["native"])
def ais_live(consumer: str = Depends(make_guard(ctx, "ais"))):
    """Posisi kapal nyata di sekitar pelabuhan dari AISStream.io (layer lingkungan; bukan armada simulasi)."""
    out = ais_feed.snapshot()
    out["simTimeUtc"] = tu.to_utc_z(ctx.now())
    return out


@app.get("/api/v1/etaPredictions", tags=["native"])
def eta_predictions(consumer: str = Depends(make_guard(ctx, "eta_predictions"))):
    return {"predictions": store.find("etaPredictions")}


class EtaWhatIf(BaseModel):
    distanceNm: float = 126
    plannedSpeedKn: float = 18
    windKn: float = 35
    waveM: float = 3.8
    currentKn: float = -0.8
    congestionIdx: float = 0.3
    vesselTeu: int = 6500


@app.post("/api/v1/eta/predict", tags=["native"])
def predict(body: EtaWhatIf, consumer: str = Depends(make_guard(ctx, "model"))):
    return {"input": body.model_dump(), "prediction": eta_model.predict(body.model_dump())}


@app.get("/api/v1/model", tags=["native"])
def model_info(consumer: str = Depends(make_guard(ctx, "model"))):
    return eta_model.metrics


@app.get("/api/v1/manifests", tags=["native"])
def manifests(vesselId: str | None = None, consumer: str = Depends(make_guard(ctx, "manifests"))):
    items = store.find("manifests")
    return {"manifests": [m for m in items if vesselId in (None, m["vesselId"])]}


@app.get("/api/v1/negotiations", tags=["native"])
def negotiations(consumer: str = Depends(make_guard(ctx, "negotiations"))):
    return {"negotiations": store.find("negotiations")}


@app.get("/api/v1/berthWindows", tags=["native"])
def berth_windows(consumer: str = Depends(make_guard(ctx, "berth_windows"))):
    return {"berthWindows": store.find("berthWindows")}


@app.get("/api/v1/shared/fleetEta", tags=["shared"])
def shared_fleet_eta(consumer: str = Depends(make_guard(ctx, "fleet_eta"))):
    out = []
    for v in own_vessels(store):
        pred = store.get("etaPredictions", v["vesselId"]) or {}
        out.append({"vesselId": v["vesselId"], "name": v["name"],
                    "scheduledEtaUtc": v["voyage"]["scheduledEtaUtc"],
                    "predictedEtaUtc": tu.to_utc_z(current_eta_h(store, v)),
                    "delayHours": pred.get("appliedDelayHours", 0.0), "p10": pred.get("p10"), "p90": pred.get("p90")})
    return {"fleetEta": out, "simTimeUtc": tu.to_utc_z(ctx.now())}


@app.get("/api/v1/shared/kpi", tags=["shared"])
def shared_kpi(consumer: str = Depends(make_guard(ctx, "kpi"))):
    windows = store.find("berthWindows")
    opts = [{"vessel_id": w["vesselId"], "start_h": tu.from_utc_z(w["startUtc"]), "end_h": tu.from_utc_z(w["endUtc"])}
            for w in windows]
    u = ol_utility(store, opts, 0.0, ctx.governance["decision_policy"])
    preds = store.find("etaPredictions")
    negs = store.find("negotiations")
    return {"enterprise": EID, "localKpi": {
        "vesselsTracked": len(own_vessels(store)),
        "vesselsDelayed": sum(1 for p in preds if p["appliedDelayHours"] > 0),
        "totalWaitingHours": round(sum(r["wait_h"] for r in u["breakdown"]), 2),
        "lateDepartureHours": round(sum(r["late_departure_h"] for r in u["breakdown"]), 2),
        "waitingCostJt": u["cost_jt"], "negotiations": len(negs),
        "negotiationsAgreed": sum(1 for n in negs if n["status"] in ("AGREED", "ACCEPTED_BY_OL"))},
        "utility": {"revenueJt": 0.0, "costJt": u["cost_jt"], "riskPenaltyJt": 0.0, "U": u["utility"]}}


register_tool(ctx, "get_vessel_eta", "ETA terkini satu kapal (UTC)", {"type": "object", "properties": {
    "vessel_id": {"type": "string"}}, "required": ["vessel_id"]},
    lambda vessel_id: {"vesselId": vessel_id, "etaUtc": tu.to_utc_z(current_eta_h(store, store.get("vessels", vessel_id)))})
register_tool(ctx, "get_manifest", "Manifest kargo (BC 1.1) satu kapal", {"type": "object", "properties": {
    "vessel_id": {"type": "string"}}, "required": ["vessel_id"]},
    lambda vessel_id: {"manifest": [m for m in store.find("manifests") if m["vesselId"] == vessel_id]})
register_tool(ctx, "predict_eta", "Prediksi delay ETA (what-if)", {"type": "object", "properties": {
    f: {"type": "number"} for f in EtaWhatIf.model_fields}}, lambda **kw: eta_model.predict(kw))

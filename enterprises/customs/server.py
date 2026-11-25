"""Customs Authority - server API (port 8005).

Model data: SQL + audit trail hash-chained; waktu ISO-8601 dengan offset +07:00.
Format API native: JSON ketat dengan API key; keputusan release hanya via endpoint petugas (RBAC header X-User-Role).
"""
from __future__ import annotations

import json

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel

from common import timeutil as tu
from common.agent_base import EnterpriseContext
from common.config import DATA_DIR, enterprise_db_path, model_dir
from common.db import Database
from common.enterprise_base import consumer_from_key, create_enterprise_app, make_guard, register_tool
from enterprises.customs.agents import CustomsRepo, OfficerDesk, RiskAgent, VerificationAgent
from enterprises.customs.risk_model import RiskModel

EID = "customs"
db = Database(enterprise_db_path(EID))
ctx = EnterpriseContext(EID, db, audit_table="audit_trail")
repo = CustomsRepo(db)


def reset() -> None:
    db.drop_all()
    repo.__init__(db)
    repo.seed()


if not db.scalar("SELECT COUNT(*) FROM hs_reference"):
    reset()

risk_model = RiskModel(model_dir(EID), DATA_DIR / "customs" / "declaration_history.csv", repo.ref(),
                       repo.origin()).load_or_train()
desk = OfficerDesk(ctx, repo)
agents = [RiskAgent(ctx, repo, risk_model, desk), VerificationAgent(ctx, repo)]
app = create_enterprise_app(ctx, agents, reset,
                            description="Customs Authority - Document verification & customs risk review. AI hanya "
                                        "merekomendasikan; keputusan release oleh petugas (human-in-the-loop).")


def _decl(r: dict) -> dict:
    r = dict(r)
    r.pop("payload", None)
    last = db.one("SELECT * FROM risk_assessments WHERE declaration_no=? ORDER BY id DESC LIMIT 1", (r["declaration_no"],))
    if last:
        last["rule_hits"] = json.loads(last["rule_hits"])
        last["features"] = json.loads(last["features"])
    r["latest_assessment"] = last
    return r


@app.get("/customs/api/declarations", tags=["native"])
def declarations(consumer: str = Depends(make_guard(ctx, "declarations"))):
    return {"declarations": [_decl(r) for r in db.query("SELECT * FROM declarations ORDER BY submitted_at")]}


@app.get("/customs/api/review-queue", tags=["native"])
def review_queue(consumer: str = Depends(make_guard(ctx, "review_queue"))):
    rows = db.query("SELECT * FROM declarations WHERE status IN ('AWAITING_OFFICER','HOLD_RECOMMENDED','HOLD') "
                    "ORDER BY submitted_at")
    return {"queue": [_decl(r) for r in rows], "auto_officer": ctx.config.get("auto_officer")}


class OfficerDecision(BaseModel):
    declaration_no: str
    decision: str  # APPROVE | HOLD | REJECT
    officer: str = "Petugas-01"
    final_lane: str | None = None
    notes: str = ""


@app.post("/customs/api/officer/decisions", tags=["native"])
def officer_decision(body: OfficerDecision, x_api_key: str | None = Header(default=None),
                     x_user_role: str | None = Header(default=None)):
    if consumer_from_key(x_api_key) != EID:
        raise HTTPException(403, "Hanya sistem internal Customs yang dapat mencatat keputusan petugas")
    try:
        with ctx.lock:
            return desk.decide(body.declaration_no, body.decision.upper(), body.officer, x_user_role or "",
                               body.notes, body.final_lane)
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except KeyError:
        raise HTTPException(404, "deklarasi tidak ditemukan")


@app.get("/customs/api/officer/decisions", tags=["native"])
def officer_log(consumer: str = Depends(make_guard(ctx, "review_queue"))):
    return {"decisions": db.query("SELECT * FROM officer_decisions ORDER BY id DESC")}


@app.get("/customs/api/risk-model", tags=["native"])
def model_info(consumer: str = Depends(make_guard(ctx, "risk_model"))):
    return risk_model.metrics


class WhatIf(BaseModel):
    hs_code: str = "8517.13"
    origin_country: str = "China"
    gross_weight_kg: float = 16000
    customs_value_usd: float = 210000
    containers: int = 4
    has_permit: bool = True
    doc_consistency: float = 1.0
    importer_score: float = 0.85


@app.post("/customs/api/risk/what-if", tags=["native"])
def what_if(body: WhatIf, consumer: str = Depends(make_guard(ctx, "risk_model"))):
    return risk_model.assess(body.model_dump())


@app.get("/customs/api/policy-rules", tags=["native"])
def policy_rules():
    return {"rules": [
        {"id": "P1", "lane": "RED", "rule": "Komoditas larangan/pembatasan (lartas) tanpa izin impor"},
        {"id": "P2", "lane": "RED", "rule": "Cocok dengan nota hasil intelijen (NHI) aktif"},
        {"id": "P3", "lane": "RED", "rule": "Nilai pabean < 35% nilai referensi HS (undervaluation berat)"},
        {"id": "P4", "lane": "RED", "rule": "Anomali persentil >= 97% DAN (nilai < 50% atau risiko negara asal >= 0,4)"},
        {"id": "P5", "lane": "YELLOW", "rule": "Anomali persentil >= 85% (Isolation Forest)"},
        {"id": "P6", "lane": "YELLOW", "rule": "Nilai pabean < 60% nilai referensi"},
        {"id": "P7", "lane": "YELLOW", "rule": "Konsistensi dokumen < 0,90"},
        {"id": "P8", "lane": "YELLOW", "rule": "Deklarasi tidak cocok dengan manifest kapal"},
        {"id": "P9", "lane": "YELLOW", "rule": "Komoditas lartas dengan izin -> penelitian dokumen"}],
        "lane_hours": ctx.governance["decision_policy"]["lane_hours"],
        "note": "AI hanya merekomendasikan jalur; release/hold diputuskan petugas."}


@app.get("/customs/api/intel", tags=["native"])
def intel(consumer: str = Depends(make_guard(ctx, "intel"))):
    return {"alerts": db.query("SELECT * FROM intel_alerts")}


@app.get("/customs/api/manifests", tags=["native"])
def manifests(consumer: str = Depends(make_guard(ctx, "manifests"))):
    return {"manifests": db.query("SELECT * FROM manifests")}


@app.get("/customs/api/shared/declaration-status", tags=["shared"])
def declaration_status(consumer: str = Depends(make_guard(ctx, "declaration_status"))):
    rows = db.query("SELECT declaration_no, shipment_ref, importer, vessel_id, containers, status, final_lane, "
                    "submitted_at, release_at FROM declarations ORDER BY submitted_at")
    if consumer == "importexport":
        rows = [r for r in rows if r["importer"] == "PT ImportExport Corp"]
    if consumer == "smartport":
        rows = [{"shipment_ref": r["shipment_ref"], "status": r["status"], "release_at": r["release_at"]} for r in rows]
    return {"as_of": tu.to_iso(ctx.now()), "declarations": rows}


@app.get("/customs/api/shared/kpi", tags=["shared"])
def kpi(consumer: str = Depends(make_guard(ctx, "kpi"))):
    rows = db.query("SELECT * FROM declarations")
    ref = repo.ref()
    decisions = db.query("SELECT * FROM officer_decisions")
    duty = 0.0
    for r in rows:
        if r["status"] != "RELEASED":
            continue
        val = r["customs_value_usd"]
        if r["final_lane"] == "RED":  # koreksi nilai pabean setelah pemeriksaan
            val = max(val, ref[r["hs_code"]]["ref_unit_value_usd_per_kg"] * r["gross_weight_kg"])
        duty += val * ref[r["hs_code"]]["tariff_pct"] / 100 * 16000 / 1e6
    proc_hours = sum(ctx.governance["decision_policy"]["lane_hours"].get(r["final_lane"] or "GREEN", 0.5)
                     for r in rows if r["status"] == "RELEASED")
    high_risk_green = sum(1 for r in rows if r["recommended_lane"] == "RED" and r["final_lane"] == "GREEN")
    released_wo_human = sum(1 for r in rows if r["status"] == "RELEASED" and not any(
        d["declaration_no"] == r["declaration_no"] and d["decision"] == "APPROVE" for d in decisions))
    cost = proc_hours * 0.2
    risk_pen = high_risk_green * 50.0
    return {"enterprise": EID, "local_kpi": {
        "declarations": len(rows), "released": sum(r["status"] == "RELEASED" for r in rows),
        "awaiting_officer": sum(r["status"] in ("AWAITING_OFFICER", "HOLD_RECOMMENDED") for r in rows),
        "on_hold": sum(r["status"] == "HOLD" for r in rows),
        "lane_green": sum(r["final_lane"] == "GREEN" for r in rows),
        "lane_yellow": sum(r["final_lane"] == "YELLOW" for r in rows),
        "lane_red": sum(r["final_lane"] == "RED" for r in rows),
        "human_decisions": len(decisions), "simulated_human_decisions": sum(d["is_simulated_human"] for d in decisions),
        "released_without_human_approval": released_wo_human, "duty_collected_jt": round(duty, 2)},
        "utility": {"revenue_jt": round(duty, 2), "cost_jt": round(cost, 2), "risk_penalty_jt": risk_pen,
                    "U": round(duty - cost - risk_pen, 2)}}


register_tool(ctx, "get_declaration_status", "Status deklarasi (tanpa detail risiko)",
              {"type": "object", "properties": {"shipment_ref": {"type": "string"}}},
              lambda shipment_ref=None: {"declarations": [
                  {"shipment_ref": r["shipment_ref"], "status": r["status"], "release_at": r["release_at"]}
                  for r in db.query("SELECT * FROM declarations") if shipment_ref in (None, r["shipment_ref"])]})

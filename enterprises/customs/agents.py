"""AI Agents Customs Authority (A_5) + meja petugas (human-in-the-loop).

- verification_agent : menyimpan manifest (BC 1.1) & mencocokkan deklarasi vs manifest (bisa via MCP tool OceanLine)
- risk_agent         : Isolation Forest + policy rules -> REKOMENDASI jalur + penjelasan; nota intelijen -> rekomendasi HOLD
- OfficerDesk        : keputusan release/hold oleh petugas manusia (atau proxy manusia tersimulasi saat auto_officer)
"""
from __future__ import annotations

import csv
import json

from common import timeutil as tu
from common.agent_base import Agent
from common.config import DATA_DIR
from common.enterprise_base import mcp_call
from enterprises.customs.risk_model import LANE_HOURS, RiskModel

SCHEMA = """
CREATE TABLE IF NOT EXISTS declarations(declaration_no TEXT PRIMARY KEY, shipment_ref TEXT, importer TEXT, hs_code TEXT,
  description TEXT, origin_country TEXT, quantity INTEGER, gross_weight_kg REAL, customs_value_usd REAL, containers INTEGER,
  vessel_id TEXT, bl_no TEXT, has_permit INTEGER, permit_no TEXT, fta_scheme TEXT, doc_consistency REAL,
  submitted_at TEXT, status TEXT, recommended_lane TEXT, final_lane TEXT, release_at TEXT, manifest_match INTEGER,
  payload TEXT);
CREATE TABLE IF NOT EXISTS risk_assessments(id INTEGER PRIMARY KEY AUTOINCREMENT, declaration_no TEXT, assessed_at TEXT,
  anomaly_score REAL, anomaly_percentile REAL, recommended_lane TEXT, rule_hits TEXT, features TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS officer_decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, declaration_no TEXT, officer TEXT,
  role TEXT, decision TEXT, final_lane TEXT, notes TEXT, decided_at TEXT, is_simulated_human INTEGER);
CREATE TABLE IF NOT EXISTS hs_reference(hs_code TEXT PRIMARY KEY, description TEXT, ref_unit_value_usd_per_kg REAL,
  restricted INTEGER, tariff_pct REAL);
CREATE TABLE IF NOT EXISTS origin_risk(country TEXT PRIMARY KEY, risk_index REAL);
CREATE TABLE IF NOT EXISTS intel_alerts(id INTEGER PRIMARY KEY AUTOINCREMENT, hs_prefix TEXT, origin TEXT, level TEXT,
  note TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS manifests(vessel_id TEXT, bl_no TEXT, consignee TEXT, hs_code TEXT, containers INTEGER,
  gross_weight_kg REAL, vessel_eta TEXT, received_at TEXT, PRIMARY KEY(vessel_id, bl_no));
"""


class CustomsRepo:
    def __init__(self, db):
        self.db = db
        db.script(SCHEMA)

    def seed(self) -> None:
        d = DATA_DIR / "customs"
        with open(d / "hs_reference.csv", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                self.db.execute("INSERT INTO hs_reference VALUES (?,?,?,?,?)", (
                    r["hs_code"], r["description"], float(r["ref_unit_value_usd_per_kg"]), int(r["restricted"]),
                    float(r["tariff_pct"])))
        with open(d / "origin_risk.csv", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                self.db.execute("INSERT INTO origin_risk VALUES (?,?)", (r["country"], float(r["risk_index"])))

    def ref(self) -> dict:
        return {r["hs_code"]: r for r in self.db.query("SELECT * FROM hs_reference")}

    def origin(self) -> dict:
        return {r["country"]: r["risk_index"] for r in self.db.query("SELECT * FROM origin_risk")}

    def declaration(self, no: str) -> dict | None:
        return self.db.one("SELECT * FROM declarations WHERE declaration_no=?", (no,))


class VerificationAgent(Agent):
    name = "verification_agent"
    role = "Document Verification Agent"
    description = "Menyimpan manifest kapal dan memverifikasi kecocokan deklarasi impor terhadap manifest."
    skills = [{"id": "manifest_matching", "name": "Pencocokan manifest", "description": "B/L, jumlah kontainer, berat",
               "tags": ["verification", "rules"]}]

    def __init__(self, ctx, repo: CustomsRepo):
        self.repo = repo
        super().__init__(ctx)

    def on_cargo_manifest(self, msg: dict) -> dict:
        p = msg["payload"]
        for it in p["items"]:
            self.repo.db.execute("INSERT OR REPLACE INTO manifests VALUES (?,?,?,?,?,?,?,?)", (
                p["vessel_id"], it["bl_no"], it["consignee"], it["hs_code"], it["containers"], it["gross_weight_kg"],
                tu.to_iso(p["eta_h"]), tu.to_iso(self.ctx.now())))
        self.beliefs["manifests"] = self.repo.db.scalar("SELECT COUNT(*) FROM manifests")
        return {"stored": len(p["items"])}

    def verify(self, d: dict) -> tuple[bool, list[str]]:
        m = self.repo.db.one("SELECT * FROM manifests WHERE bl_no=?", (d["bl_no"],))
        notes = []
        if m is None:  # fallback: ambil langsung dari OceanLine via tool ala MCP (governance: hanya Customs)
            try:
                res = mcp_call("oceanline", "get_manifest", {"vessel_id": d["vessel_id"]}, as_eid="customs")
                for it in res.get("manifest", []):
                    if it["blNo"] == d["bl_no"]:
                        m = {"containers": it["containers"], "gross_weight_kg": it["grossWeightKg"],
                             "consignee": it["consignee"], "hs_code": it["hsCode"]}
                notes.append("Manifest diambil via MCP tool oceanline.get_manifest")
            except Exception as exc:
                notes.append(f"Manifest tidak tersedia ({exc})")
        if m is None:
            return False, notes + ["B/L tidak ditemukan di manifest"]
        ok = True
        if int(m["containers"]) != int(d["containers"]):
            ok = False
            notes.append(f"Jumlah kontainer manifest {m['containers']} != deklarasi {d['containers']}")
        if abs(float(m["gross_weight_kg"]) - float(d["gross_weight_kg"])) > 0.05 * float(m["gross_weight_kg"]):
            ok = False
            notes.append("Berat kotor berbeda > 5% dari manifest")
        if m["consignee"] != d["importer"]:
            ok = False
            notes.append("Consignee manifest berbeda")
        if ok:
            notes.append("Deklarasi cocok dengan manifest (B/L, jumlah kontainer, berat, consignee)")
        return ok, notes


class OfficerDesk:
    """Keputusan manusia. AI tidak memanggil approve; hanya petugas (atau proxy manusia tersimulasi)."""

    def __init__(self, ctx, repo: CustomsRepo):
        self.ctx, self.repo = ctx, repo

    def _release_h(self, d: dict, lane: str, reinspect: bool = False) -> float:
        now = self.ctx.now()
        submitted = tu.from_iso(d["submitted_at"])
        m = self.repo.db.one("SELECT vessel_eta FROM manifests WHERE bl_no=?", (d["bl_no"],))
        eta = tu.from_iso(m["vessel_eta"]) if m else now
        pol = self.ctx.governance["decision_policy"]
        if reinspect:
            return max(now, eta + 2.0) + pol["red_inspection_after_discharge_h"]
        if lane == "RED":
            return max(submitted, now, eta + 2.0) + pol["red_inspection_after_discharge_h"]
        return max(submitted, now) + pol["lane_hours"][lane]

    def decide(self, declaration_no: str, decision: str, officer: str, role: str, notes: str = "",
               final_lane: str | None = None, simulated: bool = False) -> dict:
        if role not in ("Customs Officer", "Supervisor"):
            raise PermissionError("Hanya role Customs Officer / Supervisor yang boleh memutuskan")
        d = self.repo.declaration(declaration_no)
        if d is None:
            raise KeyError(declaration_no)
        lane = final_lane or d["recommended_lane"]
        reinspect = d["status"] == "HOLD_RECOMMENDED" or d["status"] == "HOLD"
        if decision == "APPROVE":
            rel = self._release_h(d, lane, reinspect=reinspect)
            status = "RELEASED"
        elif decision == "HOLD":
            rel, status = None, "HOLD"
        else:
            rel, status = None, "REJECTED"
        self.repo.db.execute("UPDATE declarations SET status=?, final_lane=?, release_at=? WHERE declaration_no=?",
                             (status, lane, tu.to_iso(rel) if rel is not None else None, declaration_no))
        self.repo.db.execute("INSERT INTO officer_decisions(declaration_no,officer,role,decision,final_lane,notes,decided_at,"
                             "is_simulated_human) VALUES (?,?,?,?,?,?,?,?)",
                             (declaration_no, officer, role, decision, lane, notes, tu.to_iso(self.ctx.now()), int(simulated)))
        self.ctx.audit("officer_desk", f"OFFICER_{decision}", {
            "declaration_no": declaration_no, "officer": officer, "lane": lane, "release_h": rel,
            "simulated_human": simulated, "notes": notes}, human_required=True)
        agent = self.ctx.agents["risk_agent"]
        payload = {"declaration_no": declaration_no, "shipment_ref": d["shipment_ref"],
                   "status": "RELEASED" if status == "RELEASED" else "HOLD", "release_h": rel, "lane": lane,
                   "note": f"Keputusan petugas {officer}" + (" (proxy manusia tersimulasi)" if simulated else "")}
        agent.send("smartport.yard_agent", "INFORM", "customs_release", payload)
        agent.send("importexport.document_agent", "INFORM", "customs_release", payload)
        self.ctx.bus.report_decision({
            "enterprise": "customs", "agent": "customs.officer_desk (HUMAN)", "action": f"OFFICER_{decision}",
            "perception": f"Rekomendasi AI: jalur {d['recommended_lane']}", "reasoning": [notes or "-"],
            "decision": f"{decision} {d['shipment_ref']} jalur {lane}" + (f", release {tu.clock(rel)}" if rel else ""),
            "alternatives": [], "kpi": {}, "human_required": True, "strategy": self.ctx.strategy,
            "sim_time_h": self.ctx.now(), "conversation_id": None, "simulated_human": simulated})
        return {"declaration_no": declaration_no, "status": status, "release_h": rel, "lane": lane}

    def auto(self, declaration_no: str) -> dict:
        """Proxy manusia tersimulasi (aktif hanya jika auto_officer=True)."""
        d = self.repo.declaration(declaration_no)
        lane = d["recommended_lane"]
        notes = {"GREEN": "Dokumen sesuai - SPPB terbit",
                 "YELLOW": "Penelitian dokumen selesai - SPPB terbit",
                 "RED": "Pemeriksaan fisik selesai; nilai pabean dikoreksi (SPTNP) - SPPB terbit"}[lane]
        return self.decide(declaration_no, "APPROVE", "SIM-OFFICER-01", "Customs Officer", notes, lane, simulated=True)


class RiskAgent(Agent):
    name = "risk_agent"
    role = "Risk Assessment Agent (recommendation only)"
    description = "Menilai risiko deklarasi dengan Isolation Forest + policy rules dan merekomendasikan jalur ke petugas."
    skills = [{"id": "risk_scoring", "name": "Risk scoring", "description": "Isolation Forest anomaly detection",
               "tags": ["ml", "anomaly-detection"]},
              {"id": "lane_recommendation", "name": "Rekomendasi jalur", "description": "Policy rules P1-P9",
               "tags": ["rules", "explainability"]}]

    def __init__(self, ctx, repo: CustomsRepo, model: RiskModel, desk: OfficerDesk):
        self.repo, self.model, self.desk = repo, model, desk
        super().__init__(ctx)

    def _intel_hit(self, d: dict) -> bool:
        for a in self.repo.db.query("SELECT * FROM intel_alerts"):
            if d["hs_code"].startswith(a["hs_prefix"]) and a["origin"] in (d["origin_country"], "*"):
                return True
        return False

    def _assess(self, d: dict, reason: str) -> dict:
        ver: VerificationAgent = self.ctx.agents["verification_agent"]
        manifest_ok, vnotes = ver.verify(d)
        imp = self.ctx.governance["decision_policy"]["importer_scores"].get(d["importer"], 0.7)
        a = self.model.assess({**d, "importer_score": imp, "has_permit": bool(d["has_permit"])},
                              intel_hit=self._intel_hit(d), manifest_ok=manifest_ok)
        a["verification"] = vnotes
        self.repo.db.execute("INSERT INTO risk_assessments(declaration_no,assessed_at,anomaly_score,anomaly_percentile,"
                             "recommended_lane,rule_hits,features,reason) VALUES (?,?,?,?,?,?,?,?)", (
                                 d["declaration_no"], tu.to_iso(self.ctx.now()), a["anomaly_score"],
                                 a["anomaly_percentile"], a["recommended_lane"], json.dumps(a["rule_hits"] + vnotes),
                                 json.dumps(a["features"]), reason))
        self.repo.db.execute("UPDATE declarations SET manifest_match=? WHERE declaration_no=?",
                             (int(manifest_ok), d["declaration_no"]))
        return a

    def on_customs_declaration(self, msg: dict) -> dict:
        self.goal = "Kepatuhan regulasi tanpa menghambat arus barang"
        d = dict(msg["payload"])
        d["submitted_at"] = tu.to_iso(d.get("submitted_h", self.ctx.now()))
        self.repo.db.execute("INSERT OR REPLACE INTO declarations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            d["declaration_no"], d["shipment_ref"], d["importer"], d["hs_code"], d["description"], d["origin_country"],
            d["quantity"], d["gross_weight_kg"], d["customs_value_usd"], d["containers"], d["vessel_id"], d["bl_no"],
            int(bool(d["has_permit"])), d.get("permit_no"), d.get("fta_scheme"), d["doc_consistency"], d["submitted_at"],
            "RECEIVED", None, None, None, None, json.dumps(d)))
        a = self._assess(d, "deklarasi baru")
        self.repo.db.execute("UPDATE declarations SET status='AWAITING_OFFICER', recommended_lane=? WHERE declaration_no=?",
                             (a["recommended_lane"], d["declaration_no"]))
        self.decide("RECOMMEND_LANE", f"Deklarasi {d['declaration_no']} ({d['shipment_ref']}, HS {d['hs_code']}, "
                    f"{d['origin_country']})",
                    [f"Skor anomali {a['anomaly_score']} (persentil {a['anomaly_percentile']:.0%})",
                     f"Rasio nilai/referensi {a['features']['value_ratio']:.2f}"] + a["rule_hits"] + a["verification"],
                    f"REKOMENDASI jalur {a['recommended_lane']} -> menunggu keputusan petugas", human_required=True,
                    details=a)
        self.reply(msg, "INFORM", {"declaration_no": d["declaration_no"], "shipment_ref": d["shipment_ref"],
                                   "status": "UNDER_REVIEW"}, task="declaration_received")
        if self.ctx.config.get("auto_officer"):
            self.desk.auto(d["declaration_no"])
        return {"lane": a["recommended_lane"]}

    def on_risk_intel(self, msg: dict) -> dict:
        p = msg["payload"]
        self.repo.db.execute("INSERT INTO intel_alerts(hs_prefix,origin,level,note,created_at) VALUES (?,?,?,?,?)",
                             (p["hs_prefix"], p.get("origin", "*"), p.get("level", "HIGH"), p.get("note", ""),
                              tu.to_iso(self.ctx.now())))
        escalated = []
        for d in self.repo.db.query("SELECT * FROM declarations WHERE status IN ('AWAITING_OFFICER','RELEASED')"):
            if not self._intel_hit(d):
                continue
            a = self._assess(d, "re-assessment nota intelijen")
            if a["recommended_lane"] == "RED" and d["recommended_lane"] != "RED":
                self.repo.db.execute("UPDATE declarations SET status='HOLD_RECOMMENDED', recommended_lane='RED' "
                                     "WHERE declaration_no=?", (d["declaration_no"],))
                escalated.append(d["declaration_no"])
        self.decide("RECOMMEND_HOLD", f"Nota intelijen: HS {p['hs_prefix']}* asal {p.get('origin', '*')} ({p.get('note', '')})",
                    [f"Deklarasi aktif dinilai ulang; eskalasi: {escalated or '-'}",
                     "Kontainer yang belum keluar gate direkomendasikan HOLD untuk pemeriksaan fisik"],
                    f"Rekomendasi HOLD {escalated} -> menunggu keputusan petugas" if escalated else "Tidak ada eskalasi",
                    human_required=bool(escalated))
        if self.ctx.config.get("auto_officer"):
            for no in escalated:
                self.desk.decide(no, "HOLD", "SIM-OFFICER-01", "Customs Officer", "Hold berdasarkan NHI", "RED",
                                 simulated=True)
                self.desk.decide(no, "APPROVE", "SIM-OFFICER-01", "Customs Officer",
                                 "Pemeriksaan fisik ulang selesai - release", "RED", simulated=True)
        return {"escalated": escalated}

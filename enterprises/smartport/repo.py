"""Repositori relasional SmartPort (skema ternormalisasi, snake_case, waktu lokal WIB)."""
from __future__ import annotations

import csv
import json
from datetime import datetime

from common import timeutil as tu
from common.config import DATA_DIR
from common.db import Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS berth(
  berth_id TEXT PRIMARY KEY, length_m REAL, max_draft_m REAL, cranes INTEGER, crane_rate REAL,
  status TEXT DEFAULT 'OPERATIONAL', degradations TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS vessel_call(
  call_id TEXT PRIMARY KEY, vessel_id TEXT UNIQUE, vessel_name TEXT, carrier TEXT, is_partner_agent INTEGER,
  loa_m REAL, draft_m REAL, containers INTEGER, eta_sched TEXT, eta_current TEXT, planned_departure TEXT,
  doc_status TEXT, pref_berth TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS berth_plan(
  plan_version INTEGER, vessel_id TEXT, berth_id TEXT, start_time TEXT, end_time TEXT, wait_h REAL,
  status TEXT, PRIMARY KEY(plan_version, vessel_id));
CREATE TABLE IF NOT EXISTS plan_meta(
  plan_version INTEGER PRIMARY KEY, created_sim TEXT, trigger TEXT, strategy TEXT, mode TEXT, solver TEXT,
  objective REAL, solve_ms REAL, sp_utility REAL, negotiation_id TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS container_batch(
  batch_id TEXT PRIMARY KEY, shipment_id TEXT, vessel_id TEXT, consignee TEXT, trucker TEXT, containers INTEGER,
  stow_from REAL, stow_to REAL, customs_status TEXT, release_time TEXT, hold INTEGER DEFAULT 0, lane TEXT);
CREATE TABLE IF NOT EXISTS yard_block(block_id TEXT PRIMARY KEY, capacity_teu INTEGER, used_teu INTEGER);
CREATE TABLE IF NOT EXISTS negotiation(
  neg_id TEXT PRIMARY KEY, conversation_id TEXT, counterpart TEXT, trigger TEXT, status TEXT, round INTEGER,
  max_rounds INTEGER, options_json TEXT, offers_json TEXT, agreed_option TEXT, created_sim TEXT, closed_sim TEXT,
  log_json TEXT);
"""


class SmartPortRepo:
    def __init__(self, db: Database):
        self.db = db
        db.script(SCHEMA)

    def seed(self) -> None:
        d = DATA_DIR / "smartport"
        with open(d / "berths.csv", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                self.db.execute("INSERT INTO berth(berth_id,length_m,max_draft_m,cranes,crane_rate) VALUES (?,?,?,?,?)",
                                (r["berth_id"], float(r["length_m"]), float(r["max_draft_m"]), int(r["cranes"]),
                                 float(r["crane_rate"])))
        with open(d / "vessel_calls.csv", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                self.db.execute("INSERT INTO vessel_call VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    r["call_id"], r["vessel_id"], r["vessel_name"], r["carrier"], int(r["is_partner_agent"]),
                    float(r["loa_m"]), float(r["draft_m"]), int(r["containers"]), r["eta_sched"], r["eta_current"],
                    r["planned_departure"], r["doc_status"], r["pref_berth"], r["status"]))
        with open(d / "container_batches.csv", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                self.db.execute("INSERT INTO container_batch VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                    r["batch_id"], r["shipment_id"], r["vessel_id"], r["consignee"], r["trucker"], int(r["containers"]),
                    float(r["stow_from"]), float(r["stow_to"]), r["customs_status"], r["release_time"] or None,
                    int(r["hold"]), None))
        with open(d / "yard_blocks.csv", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                self.db.execute("INSERT INTO yard_block VALUES (?,?,?)",
                                (r["block_id"], int(r["capacity_teu"]), int(r["used_teu"])))

    # ------------------------------------------------------------------ reads
    def berths(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM berth ORDER BY berth_id")
        for r in rows:
            r["degradations"] = json.loads(r["degradations"] or "[]")
        return rows

    def vessels(self) -> list[dict]:
        """Representasi kanonik (jam sejak T0) untuk optimizer."""
        out = []
        for r in self.db.query("SELECT * FROM vessel_call ORDER BY vessel_id"):
            out.append({"vessel_id": r["vessel_id"], "name": r["vessel_name"], "carrier": r["carrier"],
                        "is_partner_agent": bool(r["is_partner_agent"]), "loa_m": r["loa_m"], "draft_m": r["draft_m"],
                        "containers": r["containers"], "eta_h": tu.from_local(r["eta_current"]),
                        "eta_sched_h": tu.from_local(r["eta_sched"]),
                        "planned_departure_h": tu.from_local(r["planned_departure"]), "doc_status": r["doc_status"],
                        "pref_berth": r["pref_berth"], "status": r["status"]})
        return out

    def current_version(self) -> int:
        return int(self.db.scalar("SELECT COALESCE(MAX(plan_version),0) FROM plan_meta") or 0)

    def current_plan(self) -> dict:
        v = self.current_version()
        if not v:
            return {}
        rows = self.db.query("SELECT * FROM berth_plan WHERE plan_version=?", (v,))
        return {r["vessel_id"]: {"berth_id": r["berth_id"], "start_h": tu.from_local(r["start_time"]),
                                 "end_h": tu.from_local(r["end_time"]), "wait_h": r["wait_h"], "status": r["status"]}
                for r in rows}

    def save_plan(self, plan: list[dict], meta: dict, vessels: list[dict]) -> int:
        version = self.current_version() + 1
        vmap = {v["vessel_id"]: v for v in vessels}
        for p in plan:
            status = "PENDING" if vmap[p["vessel_id"]]["doc_status"] == "INCOMPLETE" else "SCHEDULED"
            p["status"] = status
            self.db.execute("INSERT INTO berth_plan VALUES (?,?,?,?,?,?,?)", (
                version, p["vessel_id"], p["berth_id"], tu.to_local(p["start_h"]), tu.to_local(p["end_h"]),
                p["wait_h"], status))
            self.db.execute("UPDATE vessel_call SET status=? WHERE vessel_id=?", (status, p["vessel_id"]))
        self.db.execute("INSERT INTO plan_meta VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            version, tu.to_local(meta.get("sim_h", 0.0)), meta.get("trigger"), meta.get("strategy"), meta.get("mode"),
            meta.get("solver"), meta.get("objective"), meta.get("solve_ms"), meta.get("sp_utility"),
            meta.get("negotiation_id"), meta.get("note")))
        return version

    def batches(self) -> list[dict]:
        return self.db.query("SELECT * FROM container_batch ORDER BY batch_id")

    def update_eta(self, vessel_id: str, eta_h: float) -> None:
        self.db.execute("UPDATE vessel_call SET eta_current=? WHERE vessel_id=?", (tu.to_local(eta_h), vessel_id))

    # ------------------------------------------------------------ negotiation
    def save_negotiation(self, neg: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO negotiation VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            neg["neg_id"], neg.get("conversation_id"), neg.get("counterpart"), neg.get("trigger"), neg["status"],
            neg["round"], neg["max_rounds"], json.dumps(neg["options"]), json.dumps(neg.get("offers", [])),
            neg.get("agreed_option"), neg.get("created_sim"), neg.get("closed_sim"), json.dumps(neg.get("log", []))))

    def get_negotiation(self, neg_id: str) -> dict | None:
        r = self.db.one("SELECT * FROM negotiation WHERE neg_id=?", (neg_id,))
        if not r:
            return None
        r["options"] = json.loads(r.pop("options_json"))
        r["offers"] = json.loads(r.pop("offers_json") or "[]")
        r["log"] = json.loads(r.pop("log_json") or "[]")
        return r

    def open_negotiation(self) -> dict | None:
        r = self.db.one("SELECT neg_id FROM negotiation WHERE status IN ('OPEN','MEDIATION') ORDER BY created_sim DESC")
        return self.get_negotiation(r["neg_id"]) if r else None

    def negotiations(self) -> list[dict]:
        return [self.get_negotiation(r["neg_id"]) for r in
                self.db.query("SELECT neg_id FROM negotiation ORDER BY rowid")]


def now_str() -> str:
    return datetime.now().isoformat(timespec="seconds")

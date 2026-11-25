"""Kelas dasar agen dan konteks perusahaan (E_i = U_i, M_i, D_i, A_i, P_i, G_i).

Siklus agen: perceive (pesan / event lingkungan) -> reason (model AI, optimasi, aturan)
-> decide (dicatat ke audit lokal + decision trace global) -> act (kirim pesan / ubah data lokal).
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime
from pathlib import Path

from common.a2a import BusClient
from common.config import ENTERPRISES, ROOT
from common.db import Database, dumps


class EnterpriseContext:
    """Konteks runtime satu perusahaan: database lokal, bus, governance, konfigurasi."""

    DEFAULT_CONFIG = {
        "strategy": "collaborative",   # static | local | collaborative
        "adaptive": True,              # False = rencana dibekukan (strategi static setelah baseline)
        "auto_officer": False,         # True = petugas Bea Cukai disimulasikan (proxy manusia)
        "sim_time_h": 0.0,
    }

    def __init__(self, eid: str, db: Database, audit_table: str = "audit_log"):
        self.eid = eid
        self.meta = ENTERPRISES[eid]
        self.db = db
        self.bus = BusClient(eid)
        self.lock = threading.RLock()
        self.config = dict(self.DEFAULT_CONFIG)
        self.audit_table = audit_table
        self.reload_governance()
        self.agents: dict = {}
        self.tools: dict = {}
        self.ensure_audit_table()

    def reload_governance(self) -> None:
        gov_path = ROOT / "enterprises" / self.eid / "governance.json"
        self.governance = json.loads(Path(gov_path).read_text(encoding="utf-8"))

    # ------------------------------------------------------------------ config
    def now(self) -> float:
        return float(self.config.get("sim_time_h", 0.0))

    @property
    def strategy(self) -> str:
        return self.config.get("strategy", "collaborative")

    @property
    def adaptive(self) -> bool:
        return bool(self.config.get("adaptive", True))

    # ------------------------------------------------------------------ audit
    def ensure_audit_table(self) -> None:
        self.db.script(f"""
        CREATE TABLE IF NOT EXISTS "{self.audit_table}" (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_real TEXT, sim_time_h REAL, agent TEXT, action TEXT,
            detail TEXT, human_required INTEGER DEFAULT 0,
            prev_hash TEXT, hash TEXT)
        """)

    def audit(self, agent: str, action: str, detail: dict, human_required: bool = False) -> None:
        """Audit log lokal dengan hash-chain (tamper-evident)."""
        with self.db.lock:
            prev = self.db.scalar(f'SELECT hash FROM "{self.audit_table}" ORDER BY seq DESC LIMIT 1') or "GENESIS"
            body = dumps(detail)
            ts = datetime.now().isoformat(timespec="seconds")
            h = hashlib.sha256(f"{prev}|{ts}|{agent}|{action}|{body}".encode("utf-8")).hexdigest()[:24]
            self.db.execute(
                f'INSERT INTO "{self.audit_table}"(ts_real, sim_time_h, agent, action, detail, human_required, prev_hash, hash) '
                "VALUES (?,?,?,?,?,?,?,?)",
                (ts, self.now(), agent, action, body, int(human_required), prev, h),
            )

    def get_audit(self, limit: int = 200) -> list[dict]:
        rows = self.db.query(f'SELECT * FROM "{self.audit_table}" ORDER BY seq DESC LIMIT ?', (limit,))
        for r in rows:
            try:
                r["detail"] = json.loads(r["detail"])
            except Exception:
                pass
        return rows

    def verify_audit_chain(self) -> dict:
        rows = self.db.query(f'SELECT * FROM "{self.audit_table}" ORDER BY seq ASC')
        prev = "GENESIS"
        for r in rows:
            h = hashlib.sha256(f"{prev}|{r['ts_real']}|{r['agent']}|{r['action']}|{r['detail']}".encode("utf-8")).hexdigest()[:24]
            if r["prev_hash"] != prev or r["hash"] != h:
                return {"valid": False, "broken_at": r["seq"], "entries": len(rows)}
            prev = r["hash"]
        return {"valid": True, "entries": len(rows)}

    # ------------------------------------------------------------- governance
    def allowed(self, resource: str, consumer: str | None) -> bool:
        if consumer is None:
            return False
        if consumer == self.eid:
            return True
        allowed = self.governance.get("access", {}).get(resource, [])
        return consumer in allowed or "*" in allowed


class Agent:
    """Agen dasar. Subkelas mendefinisikan handler `on_<task>` atau `on_<message_type>`."""

    name = "agent"
    role = ""
    description = ""
    skills: list[dict] = []

    def __init__(self, ctx: EnterpriseContext):
        self.ctx = ctx
        self.reset_state()

    def reset_state(self) -> None:
        self.state = "idle"
        self.goal = ""
        self.beliefs: dict = {}
        self.last_decision: dict | None = None
        self.handled = 0
        self.sent = 0
        self.last_active: str | None = None

    @property
    def address(self) -> str:
        return f"{self.ctx.eid}.{self.name}"

    # ----------------------------------------------------------------- perceive
    def handle(self, msg: dict) -> dict:
        self.handled += 1
        self.state = "reasoning"
        self.last_active = datetime.now().isoformat(timespec="seconds")
        task = msg.get("task", "")
        mtype = msg.get("message_type", "").lower()
        fn = getattr(self, f"on_{task}", None) or getattr(self, f"on_{mtype}", None)
        try:
            result = fn(msg) if fn else {"ignored": True, "reason": f"no handler for {task}/{mtype}"}
        finally:
            if self.state == "reasoning":
                self.state = "idle"
        return result or {}

    # ---------------------------------------------------------------------- act
    def send(self, receiver: str, message_type: str, task: str, payload: dict,
             conversation_id: str | None = None, in_reply_to: str | None = None,
             reply_required: bool = False) -> dict:
        msg = {
            "sender": self.address,
            "receiver": receiver,
            "message_type": message_type,
            "task": task,
            "payload": payload,
            "conversation_id": conversation_id,
            "in_reply_to": in_reply_to,
            "reply_required": reply_required,
            "sim_time_h": self.ctx.now(),
        }
        self.sent += 1
        return self.ctx.bus.send(msg)

    def reply(self, msg: dict, message_type: str, payload: dict, task: str | None = None,
              reply_required: bool = False) -> dict:
        return self.send(msg["sender"], message_type, task or msg["task"], payload,
                         conversation_id=msg.get("conversation_id"), in_reply_to=msg.get("message_id"),
                         reply_required=reply_required)

    # ------------------------------------------------------------------- decide
    def decide(self, action: str, perception: str, reasoning: list[str], decision: str,
               alternatives: list | None = None, kpi: dict | None = None, human_required: bool = False,
               share: bool = True, conversation_id: str | None = None, details: dict | None = None) -> dict:
        record = {
            "enterprise": self.ctx.eid,
            "agent": self.address,
            "action": action,
            "perception": perception,
            "reasoning": reasoning,
            "decision": decision,
            "alternatives": alternatives or [],
            "kpi": kpi or {},
            "human_required": human_required,
            "strategy": self.ctx.strategy,
            "sim_time_h": self.ctx.now(),
            "conversation_id": conversation_id,
            "real_time": time.time(),
        }
        self.last_decision = record
        local = dict(record)
        if details:
            local["details"] = details
        self.ctx.audit(self.name, action, local, human_required)
        if share:
            self.ctx.bus.report_decision(record)
        return record

    def status(self) -> dict:
        return {
            "address": self.address,
            "role": self.role,
            "state": self.state,
            "goal": self.goal,
            "handled": self.handled,
            "sent": self.sent,
            "last_active": self.last_active,
            "beliefs": self.beliefs,
            "last_decision": (self.last_decision or {}).get("decision"),
            "last_action": (self.last_decision or {}).get("action"),
        }

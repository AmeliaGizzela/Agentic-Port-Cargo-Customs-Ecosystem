"""Message bus ekosistem (broker A2A sederhana) di Global Agentic Control Center.

- Pesan diterima via POST /a2a/send, dicatat, lalu diantar SECARA BERURUTAN oleh satu worker thread
  ke /a2a/inbox perusahaan penerima -> urutan kausal deterministik & mudah diamati.
- Control Center hanya melihat isi pesan yang memang dibagikan antar-perusahaan (bukan database internal).
"""
from __future__ import annotations

import json
import queue
import threading
import time
from datetime import datetime

import requests

from common.config import API_KEYS, ENTERPRISES, api_url
from common.db import Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT, conversation_id TEXT,
  in_reply_to TEXT, sender TEXT, receiver TEXT, message_type TEXT, task TEXT, payload TEXT, sim_time_h REAL,
  created_at TEXT, status TEXT, delivered_at TEXT, latency_ms REAL, response TEXT, run_id TEXT);
CREATE TABLE IF NOT EXISTS trace(id INTEGER PRIMARY KEY AUTOINCREMENT, enterprise TEXT, agent TEXT, action TEXT,
  perception TEXT, reasoning TEXT, decision TEXT, alternatives TEXT, kpi TEXT, human_required INTEGER, strategy TEXT,
  sim_time_h REAL, conversation_id TEXT, received_at TEXT, run_id TEXT);
CREATE TABLE IF NOT EXISTS env_events(id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT, target TEXT, payload TEXT,
  sim_time_h REAL, created_at TEXT, run_id TEXT);
CREATE TABLE IF NOT EXISTS kpi_snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT, sim_time_h REAL, strategy TEXT,
  scenario TEXT, kpi TEXT, created_at TEXT, run_id TEXT);
CREATE TABLE IF NOT EXISTS scenario_runs(run_id TEXT PRIMARY KEY, scenario TEXT, strategy TEXT, auto_officer INTEGER,
  started_at TEXT, finished_at TEXT, status TEXT, result TEXT);
"""


class MessageBus:
    def __init__(self, db: Database, local_handler):
        self.db = db
        db.script(SCHEMA)
        self.q: queue.Queue = queue.Queue()
        self.delay_s = 0.25
        self.busy = False
        self.local_handler = local_handler  # untuk pesan ke control_center.*
        self.counter = 0
        self.run_id = "manual"
        self.lock = threading.Lock()
        self.last_activity = time.time()
        threading.Thread(target=self._worker, daemon=True, name="a2a-bus").start()

    def submit(self, msg: dict) -> dict:
        with self.lock:
            self.counter += 1
            if not msg.get("message_id"):
                msg["message_id"] = f"MSG-{self.counter:05d}"
        msg["created_at"] = datetime.now().isoformat(timespec="milliseconds")
        rowid = self.db.execute(
            "INSERT INTO messages(message_id,conversation_id,in_reply_to,sender,receiver,message_type,task,payload,"
            "sim_time_h,created_at,status,run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (msg["message_id"], msg.get("conversation_id"), msg.get("in_reply_to"), msg["sender"], msg["receiver"],
             msg["message_type"], msg["task"], json.dumps(msg.get("payload", {}), ensure_ascii=False),
             msg.get("sim_time_h"), msg["created_at"], "QUEUED", self.run_id))
        self.last_activity = time.time()
        self.q.put((rowid, msg))
        return {"accepted": True, "message_id": msg["message_id"], "queue": self.q.qsize()}

    def pending(self) -> int:
        return self.q.qsize() + (1 if self.busy else 0)

    def _worker(self) -> None:
        while True:
            rowid, msg = self.q.get()
            self.busy = True
            try:
                if self.delay_s > 0:
                    time.sleep(self.delay_s)
                t0 = time.perf_counter()
                target = msg["receiver"].split(".", 1)[0]
                if target == "control_center":
                    resp, status = self.local_handler(msg), "DELIVERED"
                elif target in ENTERPRISES:
                    try:
                        r = requests.post(f"{api_url(target)}/a2a/inbox", json=msg,
                                          headers={"X-API-Key": API_KEYS["control_center"]}, timeout=180)
                        resp = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
                        status = "DELIVERED" if r.status_code == 200 and (
                            not isinstance(resp, dict) or resp.get("status") != "error") else "FAILED"
                    except Exception as exc:
                        resp, status = {"error": str(exc)}, "FAILED"
                else:
                    resp, status = {"error": f"unknown receiver {msg['receiver']}"}, "FAILED"
                self.db.execute("UPDATE messages SET status=?, delivered_at=?, latency_ms=?, response=? WHERE id=?", (
                    status, datetime.now().isoformat(timespec="milliseconds"), round((time.perf_counter() - t0) * 1000, 1),
                    json.dumps(resp, ensure_ascii=False, default=str)[:2000], rowid))
            except Exception as exc:  # jangan pernah mematikan worker
                self.db.execute("UPDATE messages SET status='FAILED', response=? WHERE id=?", (str(exc), rowid))
            finally:
                self.busy = False
                self.last_activity = time.time()
                self.q.task_done()

    def wait_idle(self, timeout: float = 600, settle: float = 0.6) -> bool:
        t_end = time.time() + timeout
        while time.time() < t_end:
            if self.pending() == 0 and time.time() - self.last_activity >= settle:
                return True
            time.sleep(0.1)
        return False

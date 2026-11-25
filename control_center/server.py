"""Global Agentic Control Center - server API (port 8000).

Fungsi: message bus A2A, registry agen (discovery via Agent Card), decision trace, injeksi perubahan
lingkungan, runner skenario & strategi, mediator negosiasi (Nash bargaining), agregasi KPI ekosistem.
Control Center TIDAK mengakses database perusahaan; hanya endpoint 'shared' sesuai governance.
"""
from __future__ import annotations

import csv
import html
import json
import threading
import time
import uuid
from datetime import datetime

import requests
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from common import schema_mapping as sm
from common.config import API_KEYS, CONTROL_CENTER, ENTERPRISES, KEY_TO_ID, RESULTS_DIR, ROOT, api_url, dash_url
from common.db import Database
from control_center import kpi as kpi_mod
from control_center.bus import MessageBus
from control_center.scenarios import SCENARIOS, STRATEGIES

(ROOT / "control_center" / "runtime").mkdir(exist_ok=True)
db = Database(ROOT / "control_center" / "runtime" / "control_center.db")
H = {"X-API-Key": API_KEYS["control_center"]}
STATE = {"strategy": "collaborative", "adaptive": True, "auto_officer": False, "sim_time_h": 0.0,
         "scenario": None, "run": None}


# ---------------------------------------------------------------- coordinator
class Coordinator:
    """Agen koordinator global: mediasi negosiasi bila agen lokal tidak mencapai kesepakatan."""
    address = "control_center.coordinator_agent"

    def __init__(self):
        self.pending: dict[str, dict] = {}
        self.handled = 0
        self.state = "idle"
        self.last_decision = None

    def handle(self, msg: dict) -> dict:
        self.handled += 1
        p = msg["payload"]
        if msg["message_type"] == "MEDIATION_REQUEST":
            self.pending[p["neg_id"]] = {"request": msg, "payload": p}
            self.state = "mediating"
            send({"sender": self.address, "receiver": p["partner"], "message_type": "REQUEST",
                  "task": "mediation_utility", "payload": {"neg_id": p["neg_id"], "options": p["options"]},
                  "conversation_id": msg.get("conversation_id"), "in_reply_to": msg.get("message_id"),
                  "sim_time_h": STATE["sim_time_h"], "reply_required": True})
            return {"mediation": "utilities requested"}
        if msg["task"] == "mediation_utility":
            pend = self.pending.pop(p["neg_id"], None)
            if not pend:
                return {"ignored": True}
            req = pend["payload"]
            u_a, u_b = req["requester_utilities"], p["utilities"]
            d_a, d_b = req["requester_reservation"], p["reservation"]
            scored = []
            for oid in u_a:
                ga, gb = u_a[oid] - d_a, u_b[oid] - d_b
                nash = ga * gb if ga >= 0 and gb >= 0 else -1e9
                scored.append((nash, u_a[oid] + u_b[oid], oid))
            feasible = [s for s in scored if s[0] > -1e9]
            if feasible:
                best = max(feasible)
                method = "Nash bargaining (maks. produk surplus di atas reservasi)"
            else:
                best = max(scored, key=lambda s: s[1])
                method = "Utilitarian (maks. jumlah utilitas) karena tidak ada opsi di atas reservasi"
            oid = best[2]
            record = {"enterprise": "control_center", "agent": self.address, "action": "MEDIATE",
                      "perception": f"Negosiasi {p['neg_id']} buntu", "reasoning": [
                          f"Opsi {o}: U_SP={u_a[o]}, U_OL={u_b[o]}, jumlah={u_a[o] + u_b[o]:.1f}" for o in u_a] + [method],
                      "decision": f"Pilih opsi {oid}", "alternatives": list(u_a), "kpi": {},
                      "human_required": False, "strategy": STATE["strategy"], "sim_time_h": STATE["sim_time_h"],
                      "conversation_id": msg.get("conversation_id")}
            add_trace(record)
            self.last_decision = record
            self.state = "idle"
            send({"sender": self.address, "receiver": req["requester"], "message_type": "INFORM",
                  "task": "mediation_result", "payload": {"neg_id": p["neg_id"], "option_id": oid, "method": method},
                  "conversation_id": msg.get("conversation_id"), "sim_time_h": STATE["sim_time_h"]})
            return {"mediated": oid}
        return {"ignored": True}


coordinator = Coordinator()
bus = MessageBus(db, coordinator.handle)

app = FastAPI(title="🌐 Global Agentic Control Center API",
              description="Message bus A2A, registry, decision trace, environment & scenario control, KPI ekosistem.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def send(msg: dict) -> dict:
    return bus.submit(msg)


def add_trace(rec: dict) -> None:
    db.execute("INSERT INTO trace(enterprise,agent,action,perception,reasoning,decision,alternatives,kpi,human_required,"
               "strategy,sim_time_h,conversation_id,received_at,run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                   rec.get("enterprise"), rec.get("agent"), rec.get("action"), rec.get("perception"),
                   json.dumps(rec.get("reasoning", []), ensure_ascii=False), rec.get("decision"),
                   json.dumps(rec.get("alternatives", []), ensure_ascii=False),
                   json.dumps(rec.get("kpi", {}), ensure_ascii=False), int(bool(rec.get("human_required"))),
                   rec.get("strategy"), rec.get("sim_time_h"), rec.get("conversation_id"),
                   datetime.now().isoformat(timespec="milliseconds"), bus.run_id))


def _consumer(x_api_key: str | None) -> str:
    c = KEY_TO_ID.get(x_api_key or "")
    if not c:
        raise HTTPException(401, "API key tidak valid")
    return c


# ------------------------------------------------------------------- landing
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def landing():
    rows = "".join(f"<tr><td>{m['icon']} {html.escape(m['name'])}</td><td><a href='{api_url(e)}'>{api_url(e)}</a></td>"
                   f"<td><a href='{dash_url(e)}'>{dash_url(e)}</a></td></tr>" for e, m in ENTERPRISES.items())
    return f"""<!doctype html><html lang="id"><head><meta charset="utf-8"><title>Control Center API</title>
<style>body{{font-family:Segoe UI,Arial;margin:0;background:#f6f7f9}}header{{background:{CONTROL_CENTER['color']};color:#fff;padding:20px 28px}}
main{{padding:20px 28px}}table{{border-collapse:collapse;background:#fff}}td,th{{border:1px solid #ddd;padding:6px 12px}}</style></head>
<body><header><h1 style="margin:0">🌐 Global Agentic Control Center</h1><div>Message bus A2A · registry · decision trace · KPI</div></header>
<main><p><a href="/docs">Swagger API</a> · <a href="/registry">Registry agen</a> · <a href="/kpi">KPI ekosistem (JSON)</a> ·
<a href="{dash_url('control_center')}">Dashboard Control Center</a></p>
<table><tr><th>Perusahaan</th><th>Server API</th><th>Dashboard</th></tr>{rows}</table>
<p>Strategi aktif: <b>{STATE['strategy']}</b> · waktu simulasi {STATE['sim_time_h']:.2f} jam · antrean bus {bus.pending()}</p>
</main></body></html>"""


@app.get("/health")
def health():
    return {"enterprise": "control_center", "status": "UP", "bus_pending": bus.pending(), **STATE}


# ------------------------------------------------------------------- A2A bus
@app.post("/a2a/send")
async def a2a_send(request: Request, x_api_key: str | None = Header(default=None)):
    consumer = _consumer(x_api_key)
    msg = await request.json()
    sender_ent = msg.get("sender", "").split(".", 1)[0]
    if consumer != "control_center" and sender_ent != consumer:
        raise HTTPException(403, f"{consumer} tidak boleh mengirim atas nama {msg.get('sender')}")
    for k in ("sender", "receiver", "message_type", "task"):
        if not msg.get(k):
            raise HTTPException(422, f"field {k} wajib")
    return send(msg)


@app.get("/a2a/messages")
def messages(after_id: int = 0, limit: int = 300, enterprise: str | None = None, run_id: str | None = None,
             x_api_key: str | None = Header(default=None)):
    consumer = _consumer(x_api_key)
    if consumer != "control_center":
        enterprise = consumer  # perusahaan hanya melihat pesan yang melibatkan dirinya
    sql, args = "SELECT * FROM messages WHERE id>?", [after_id]
    if enterprise:
        sql += " AND (sender LIKE ? OR receiver LIKE ?)"
        args += [f"{enterprise}.%", f"{enterprise}.%"]
    if run_id:
        sql += " AND run_id=?"
        args.append(run_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    rows = db.query(sql, args)
    for r in rows:
        r["payload"] = json.loads(r["payload"] or "{}")
    return {"messages": rows[::-1], "pending": bus.pending()}


@app.get("/a2a/conversations")
def conversations():
    rows = db.query("SELECT conversation_id, MIN(id) first_id, COUNT(*) n, GROUP_CONCAT(message_type, ' > ') types, "
                    "MIN(sender) a, MAX(receiver) b, MIN(task) task FROM messages WHERE conversation_id IS NOT NULL "
                    "GROUP BY conversation_id ORDER BY first_id")
    return {"conversations": rows}


@app.get("/bus/status")
def bus_status():
    stats = db.query("SELECT status, COUNT(*) n FROM messages GROUP BY status")
    return {"pending": bus.pending(), "delay_s": bus.delay_s, "run_id": bus.run_id, "stats": stats}


@app.post("/bus/config")
def bus_config(body: dict):
    bus.delay_s = float(body.get("delay_s", bus.delay_s))
    return {"delay_s": bus.delay_s}


# -------------------------------------------------------------------- trace
@app.post("/trace")
async def trace_in(request: Request, x_api_key: str | None = Header(default=None)):
    consumer = _consumer(x_api_key)
    rec = await request.json()
    if consumer != "control_center" and rec.get("enterprise") != consumer:
        raise HTTPException(403, "enterprise mismatch")
    add_trace(rec)
    return {"ok": True}


@app.get("/trace")
def trace(after_id: int = 0, limit: int = 500, enterprise: str | None = None):
    sql, args = "SELECT * FROM trace WHERE id>?", [after_id]
    if enterprise:
        sql += " AND enterprise=?"
        args.append(enterprise)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    rows = db.query(sql, args)
    for r in rows:
        for k in ("reasoning", "alternatives", "kpi"):
            r[k] = json.loads(r[k] or "null")
    return {"trace": rows[::-1]}


# --------------------------------------------------------------- registry
@app.get("/registry")
def registry():
    out = []
    for eid, m in ENTERPRISES.items():
        item = {"enterprise": eid, "name": m["name"], "api": api_url(eid), "dashboard": dash_url(eid),
                "platform": m["platform"], "color": m["color"], "icon": m["icon"]}
        try:
            item["card"] = requests.get(f"{api_url(eid)}/.well-known/agent-card.json", timeout=5).json()
            item["status"] = requests.get(f"{api_url(eid)}/agents/status", timeout=5).json()
            item["health"] = "UP"
        except Exception as exc:
            item["health"] = f"DOWN ({exc.__class__.__name__})"
        out.append(item)
    out.append({"enterprise": "control_center", "name": CONTROL_CENTER["name"], "health": "UP",
                "status": {"agents": [{"address": coordinator.address, "role": "Global Coordinator / Mediator",
                                       "state": coordinator.state, "handled": coordinator.handled,
                                       "last_decision": (coordinator.last_decision or {}).get("decision")}]}})
    return {"registry": out}


# -------------------------------------------------------------- environment
def broadcast_config(cfg: dict) -> dict:
    res = {}
    for eid in ENTERPRISES:
        try:
            res[eid] = requests.post(f"{api_url(eid)}/admin/config", json=cfg, headers=H, timeout=10).json()
        except Exception as exc:
            res[eid] = {"error": str(exc)}
    return res


def set_time(h: float) -> None:
    STATE["sim_time_h"] = float(h)
    broadcast_config({"sim_time_h": float(h)})


def inject(event_type: str, target: str, payload: dict, at: float | None = None) -> dict:
    if at is not None:
        set_time(at)
    db.execute("INSERT INTO env_events(event_type,target,payload,sim_time_h,created_at,run_id) VALUES (?,?,?,?,?,?)", (
        event_type, target, json.dumps(payload, ensure_ascii=False), STATE["sim_time_h"],
        datetime.now().isoformat(timespec="milliseconds"), bus.run_id))
    return send({"sender": "environment.simulator", "receiver": target, "message_type": "ENV_EVENT",
                 "task": event_type, "payload": payload, "sim_time_h": STATE["sim_time_h"]})


@app.post("/env/inject")
def env_inject(body: dict):
    return inject(body["event_type"], body["target"], body.get("payload", {}), body.get("at"))


@app.get("/env/events")
def env_events():
    rows = db.query("SELECT * FROM env_events ORDER BY id")
    for r in rows:
        r["payload"] = json.loads(r["payload"])
    return {"events": rows}


# ----------------------------------------------------------------- sim control
def reset_all() -> dict:
    out = {}
    for eid in ENTERPRISES:
        try:
            out[eid] = requests.post(f"{api_url(eid)}/admin/reset", headers=H, timeout=60).json()
        except Exception as exc:
            out[eid] = {"error": str(exc)}
    for t in ("messages", "trace", "env_events", "kpi_snapshots"):
        db.execute(f"DELETE FROM {t}")
    coordinator.pending.clear()
    coordinator.handled = 0
    coordinator.last_decision = None
    STATE.update({"sim_time_h": 0.0, "adaptive": True})
    return out


@app.post("/sim/reset")
def sim_reset():
    if STATE.get("run") and STATE["run"].get("status") == "RUNNING":
        raise HTTPException(409, "Skenario sedang berjalan")
    bus.run_id = "manual"
    return reset_all()


@app.get("/sim/config")
def sim_config():
    return {**STATE, "bus_delay_s": bus.delay_s, "strategies": STRATEGIES}


@app.post("/sim/config")
def sim_set_config(body: dict):
    for k in ("strategy", "adaptive", "auto_officer"):
        if k in body:
            STATE[k] = body[k]
    if "bus_delay_s" in body:
        bus.delay_s = float(body["bus_delay_s"])
    cfg = {k: STATE[k] for k in ("strategy", "adaptive", "auto_officer")}
    return {"state": STATE, "enterprises": broadcast_config(cfg)}


def take_snapshot(label: str) -> dict:
    snap = kpi_mod.snapshot()
    db.execute("INSERT INTO kpi_snapshots(label,sim_time_h,strategy,scenario,kpi,created_at,run_id) VALUES (?,?,?,?,?,?,?)",
               (label, STATE["sim_time_h"], STATE["strategy"], STATE.get("scenario"), json.dumps(snap, default=str),
                datetime.now().isoformat(timespec="seconds"), bus.run_id))
    return snap


def run_scenario(sid: str, strategy: str, auto_officer: bool, bus_delay: float, run_id: str,
                 policy_overrides: dict | None = None) -> None:
    sc = SCENARIOS[sid]
    run = STATE["run"]
    try:
        bus.run_id = run_id
        reset_all()
        bus.delay_s = bus_delay
        STATE.update({"strategy": strategy, "adaptive": True, "auto_officer": auto_officer, "scenario": sid})
        broadcast_config({"strategy": strategy, "adaptive": True, "auto_officer": auto_officer, "sim_time_h": 0.0})
        overrides = {**sc.get("policy_overrides", {}), **(policy_overrides or {})}
        for eid, pol in overrides.items():  # eksperimen sensitivitas kebijakan (dipulihkan saat reset)
            requests.post(f"{api_url(eid)}/admin/policy", json=pol, headers=H, timeout=10)
        run["policy_overrides"] = overrides
        run["step"] = "baseline: start_of_day"
        inject("start_of_day", "oceanline.eta_agent", {"description": "Awal hari operasi"}, at=0.0)
        bus.wait_idle()
        run["baseline"] = take_snapshot("baseline")["ecosystem"]
        if strategy == "static":
            STATE["adaptive"] = False
            broadcast_config({"adaptive": False})
        for ev in sc["events"]:
            run["step"] = f"event {ev['type']} @ {ev['at']}"
            inject(ev["type"], ev["target"], ev["payload"], at=ev["at"])
            bus.wait_idle()
        final = take_snapshot("after_events")
        run["after"] = final["ecosystem"]
        run["utilities"] = final["local_utilities"]
        run["messages"] = db.scalar("SELECT COUNT(*) FROM messages WHERE run_id=?", (run_id,))
        run["status"] = "DONE"
    except Exception as exc:
        import traceback
        run["status"] = "FAILED"
        run["error"] = f"{exc}\n{traceback.format_exc()[-1200:]}"
    finally:
        run["finished_at"] = datetime.now().isoformat(timespec="seconds")
        db.execute("INSERT OR REPLACE INTO scenario_runs VALUES (?,?,?,?,?,?,?,?)", (
            run_id, sid, strategy, int(auto_officer), run["started_at"], run["finished_at"], run["status"],
            json.dumps(run, default=str)))


@app.get("/scenarios")
def scenarios():
    return {"scenarios": SCENARIOS, "strategies": STRATEGIES}


@app.post("/scenario/run")
def scenario_run(body: dict):
    sid = body.get("scenario_id", "S1_vessel_delay")
    if sid not in SCENARIOS:
        raise HTTPException(404, "skenario tidak dikenal")
    if STATE.get("run") and STATE["run"].get("status") == "RUNNING":
        raise HTTPException(409, "Skenario lain sedang berjalan")
    run_id = f"RUN-{datetime.now().strftime('%H%M%S')}-{uuid.uuid4().hex[:4]}"
    STATE["run"] = {"run_id": run_id, "scenario": sid, "strategy": body.get("strategy", "collaborative"),
                    "status": "RUNNING", "started_at": datetime.now().isoformat(timespec="seconds"), "step": "reset"}
    threading.Thread(target=run_scenario, daemon=True, args=(
        sid, body.get("strategy", "collaborative"), bool(body.get("auto_officer", True)),
        float(body.get("bus_delay_s", 0.25)), run_id, body.get("policy_overrides"))).start()
    return STATE["run"]


@app.get("/scenario/status")
def scenario_status():
    return {"run": STATE.get("run"), "pending": bus.pending(), "sim_time_h": STATE["sim_time_h"]}


@app.get("/scenario/runs")
def scenario_runs():
    rows = db.query("SELECT * FROM scenario_runs ORDER BY started_at DESC")
    for r in rows:
        r["result"] = json.loads(r["result"] or "{}")
    return {"runs": rows}


@app.post("/sim/start-day")
def start_day():
    """Mode manual: mulai hari operasi tanpa reset (dipakai dashboard)."""
    return inject("start_of_day", "oceanline.eta_agent", {"description": "Awal hari operasi"}, at=0.0)


@app.post("/sim/wait-idle")
def wait_idle(timeout: float = 120):
    ok = bus.wait_idle(timeout=timeout)
    return {"idle": ok, "pending": bus.pending()}


# -------------------------------------------------------------------- KPI
@app.get("/kpi")
def kpi_now():
    return kpi_mod.snapshot()


@app.post("/kpi/snapshot")
def kpi_snapshot(body: dict):
    return take_snapshot(body.get("label", "manual"))


@app.get("/kpi/history")
def kpi_history(run_id: str | None = None):
    sql, args = "SELECT id,label,sim_time_h,strategy,scenario,kpi,created_at,run_id FROM kpi_snapshots", []
    if run_id:
        sql += " WHERE run_id=?"
        args.append(run_id)
    rows = db.query(sql + " ORDER BY id", args)
    for r in rows:
        r["kpi"] = json.loads(r["kpi"])
    return {"history": rows}


@app.get("/ecosystem/state")
def ecosystem_state():
    """Pandangan kanonik ekosistem hasil schema mapping dari endpoint shared (sesuai izin)."""
    out, errors = {}, {}
    try:
        out["fleet_eta"] = sm.oceanline_fleet(requests.get(f"{api_url('oceanline')}/api/v1/shared/fleetEta", headers=H,
                                                           timeout=10).json())
    except Exception as exc:
        errors["oceanline"] = str(exc)
    try:
        out["berth_plan"] = sm.smartport_plan(requests.get(f"{api_url('smartport')}/api/shared/berth-plan", headers=H,
                                                           timeout=10).json())
    except Exception as exc:
        errors["smartport"] = str(exc)
    try:
        out["deliveries"] = sm.globalcargo_delivery(requests.get(
            f"{api_url('globalcargo')}/gc/v2/Shared/DeliveryStatus", headers=H, timeout=10).json())
    except Exception as exc:
        errors["globalcargo"] = str(exc)
    try:
        out["shipments"] = sm.importexport_status(requests.get(
            f"{api_url('importexport')}/api/xml/shared/status-pengiriman", headers=H, timeout=10).text)
    except Exception as exc:
        errors["importexport"] = str(exc)
    try:
        out["declarations"] = sm.customs_status(requests.get(
            f"{api_url('customs')}/customs/api/shared/declaration-status", headers=H, timeout=10).json())
    except Exception as exc:
        errors["customs"] = str(exc)
    out["errors"] = errors
    out["sim_time_h"] = STATE["sim_time_h"]
    return out


@app.get("/schema-mapping")
def schema_mapping():
    return {"field_map": sm.FIELD_MAP, "time_example": sm.example_roundtrip()}


@app.get("/evaluation")
def evaluation():
    p = RESULTS_DIR / "evaluation_results.csv"
    if not p.exists():
        return {"rows": [], "note": "Jalankan: python -m simulation.run_evaluation"}
    with open(p, encoding="utf-8") as fh:
        return {"rows": list(csv.DictReader(fh))}

"""Factory FastAPI untuk gateway tiap perusahaan.

Semua perusahaan mengimplementasikan "Ecosystem Gateway Spec" yang sama:
  GET  /                              landing page (bisa dibuka di browser)
  GET  /health
  GET  /.well-known/agent-card.json   discovery agen (konsep A2A)
  POST /a2a/inbox                     penerimaan pesan dari message bus
  POST /mcp                           akses tools ala MCP (JSON-RPC 2.0: tools/list, tools/call)
  GET  /agents/status, /audit, /governance
  POST /admin/reset, /admin/config
Endpoint data native (heterogen) didefinisikan oleh masing-masing server.
"""
from __future__ import annotations

import html
import json
from typing import Callable

import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from common.a2a import build_agent_card
from common.agent_base import Agent, EnterpriseContext
from common.config import API_KEYS, KEY_TO_ID, api_url, dash_url


def consumer_from_key(x_api_key: str | None) -> str | None:
    return KEY_TO_ID.get(x_api_key or "")


def make_guard(ctx: EnterpriseContext, resource: str):
    """Dependency FastAPI: menegakkan kebijakan berbagi data (G_i)."""

    def _guard(x_api_key: str | None = Header(default=None)) -> str:
        consumer = consumer_from_key(x_api_key)
        if consumer is None:
            raise HTTPException(401, detail="API key tidak valid / tidak ada (header X-API-Key)")
        if not ctx.allowed(resource, consumer):
            ctx.audit("gateway", "ACCESS_DENIED", {"resource": resource, "consumer": consumer})
            raise HTTPException(403, detail=f"Governance {ctx.eid}: '{consumer}' tidak berhak mengakses '{resource}'")
        return consumer

    return _guard


def mcp_call(target: str, tool: str, arguments: dict, as_eid: str, timeout: float = 15) -> dict:
    """Panggil tool perusahaan lain via endpoint /mcp (JSON-RPC 2.0, gaya MCP)."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    r = requests.post(f"{api_url(target)}/mcp", json=body,
                      headers={"X-API-Key": API_KEYS[as_eid]}, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"].get("message"))
    return data["result"].get("structuredContent", {})


def create_enterprise_app(ctx: EnterpriseContext, agents: list[Agent], reset_fn: Callable[[], None],
                          description: str = "") -> FastAPI:
    meta = ctx.meta
    app = FastAPI(title=f"{meta['icon']} {meta['name']} API", description=description or meta["tagline"],
                  version="1.0.0")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    for a in agents:
        ctx.agents[a.name] = a
    app.state.ctx = ctx

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def landing():
        rows = "".join(
            f"<tr><td><code>{html.escape(a.address)}</code></td><td>{html.escape(a.role)}</td>"
            f"<td>{html.escape(a.state)}</td><td>{a.handled}</td><td>{a.sent}</td></tr>" for a in agents)
        tools = "".join(f"<li><code>{html.escape(n)}</code> — {html.escape(t['description'])}</li>"
                        for n, t in ctx.tools.items())
        color = meta["color"]
        return f"""<!doctype html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(meta['short'])} API</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f6f7f9;color:#1d2330}}
header{{background:{color};color:#fff;padding:20px 28px}}main{{padding:20px 28px;max-width:1000px}}
table{{border-collapse:collapse;width:100%;background:#fff}}td,th{{border:1px solid #dde1e7;padding:6px 10px;text-align:left}}
a{{color:{color}}}.card{{background:#fff;border:1px solid #dde1e7;border-radius:8px;padding:14px 18px;margin:14px 0}}</style></head>
<body><header><h1 style="margin:0">{meta['icon']} {html.escape(meta['name'])}</h1>
<div>{html.escape(meta['tagline'])}</div><small>Platform: {html.escape(meta['platform'])}</small></header>
<main><div class="card"><b>Tautan:</b> <a href="/docs">Swagger API (/docs)</a> ·
<a href="/.well-known/agent-card.json">Agent Card</a> · <a href="/governance">Governance policy</a> ·
<a href="/agents/status">Agent status (JSON)</a> · <a href="{dash_url(ctx.eid)}">Dashboard UI</a> ·
<a href="{dash_url('control_center')}">Global Control Center</a></div>
<div class="card"><b>Mode strategi:</b> {html.escape(ctx.strategy)} · <b>adaptive:</b> {ctx.adaptive} ·
<b>waktu simulasi:</b> {ctx.now():.2f} jam sejak T0</div>
<h3>AI Agents (A<sub>i</sub>)</h3><table><tr><th>Agen</th><th>Peran</th><th>State</th><th>Pesan diproses</th><th>Pesan dikirim</th></tr>{rows}</table>
<h3>Tools (akses gaya MCP via POST /mcp)</h3><ul>{tools or '<li>-</li>'}</ul>
<p><small>Semua data sintetis; nama perusahaan fiktif.</small></p></main></body></html>"""

    @app.get("/health")
    def health():
        return {"enterprise": ctx.eid, "status": "UP", "strategy": ctx.strategy, "adaptive": ctx.adaptive,
                "sim_time_h": ctx.now(), "agents": len(agents)}

    @app.get("/.well-known/agent-card.json")
    def agent_card():
        return build_agent_card(ctx.eid, meta, agents)

    @app.get("/governance")
    def governance():
        return ctx.governance

    @app.post("/a2a/inbox")
    async def inbox(request: Request, x_api_key: str | None = Header(default=None)):
        if consumer_from_key(x_api_key) != "control_center":
            raise HTTPException(403, "Inbox hanya menerima pesan dari message bus ekosistem")
        msg = await request.json()
        receiver = msg.get("receiver", "")
        agent_name = receiver.split(".", 1)[1] if "." in receiver else receiver
        agent = ctx.agents.get(agent_name)
        if agent is None:
            raise HTTPException(404, f"Agen {receiver} tidak ditemukan")
        import anyio
        return await anyio.to_thread.run_sync(_process, agent, msg)

    def _process(agent: Agent, msg: dict) -> dict:
        with ctx.lock:
            if msg.get("sim_time_h") is not None:
                ctx.config["sim_time_h"] = max(ctx.now(), float(msg["sim_time_h"]))
            try:
                result = agent.handle(msg)
                return {"status": "processed", "agent": agent.address, "result": _short(result)}
            except Exception as exc:  # jangan sampai satu error menghentikan ekosistem
                import traceback
                ctx.audit(agent.name, "ERROR", {"error": str(exc), "trace": traceback.format_exc()[-1500:]})
                return {"status": "error", "agent": agent.address, "error": str(exc)}

    @app.get("/agents/status")
    def agents_status():
        return {"enterprise": ctx.eid, "agents": [a.status() for a in agents], "config": ctx.config}

    @app.get("/audit")
    def audit(limit: int = 200, consumer: str = Depends(make_guard(ctx, "audit"))):
        return {"enterprise": ctx.eid, "chain": ctx.verify_audit_chain(), "entries": ctx.get_audit(limit)}

    @app.get("/admin/config")
    def get_config():
        return ctx.config

    @app.post("/admin/config")
    def set_config(body: dict, x_api_key: str | None = Header(default=None)):
        consumer = consumer_from_key(x_api_key)
        if consumer not in ("control_center", ctx.eid):
            raise HTTPException(403, "Hanya Control Center / pemilik yang dapat mengubah konfigurasi")
        for k in ("strategy", "adaptive", "auto_officer", "sim_time_h"):
            if k in body:
                ctx.config[k] = body[k]
        ctx.audit("gateway", "CONFIG_UPDATE", {"by": consumer, "config": ctx.config})
        return ctx.config

    @app.post("/admin/policy")
    def set_policy(body: dict, x_api_key: str | None = Header(default=None)):
        """Override sementara decision_policy (eksperimen sensitivitas); dipulihkan dari file saat reset."""
        consumer = consumer_from_key(x_api_key)
        if consumer not in ("control_center", ctx.eid):
            raise HTTPException(403, "Hanya Control Center / pemilik yang dapat mengubah kebijakan")
        ctx.governance.setdefault("decision_policy", {}).update(body)
        ctx.audit("gateway", "POLICY_OVERRIDE", {"by": consumer, "changes": body}, human_required=True)
        return ctx.governance["decision_policy"]

    @app.post("/admin/reset")
    def reset(x_api_key: str | None = Header(default=None)):
        consumer = consumer_from_key(x_api_key)
        if consumer not in ("control_center", ctx.eid):
            raise HTTPException(403, "Hanya Control Center / pemilik yang dapat me-reset")
        with ctx.lock:
            reset_fn()
            ctx.reload_governance()
            ctx.config = dict(EnterpriseContext.DEFAULT_CONFIG)
            for a in agents:
                a.reset_state()
            ctx.ensure_audit_table()
            ctx.audit("gateway", "RESET", {"by": consumer})
        return {"enterprise": ctx.eid, "reset": True}

    # ------------------------------------------------------------ MCP-style tools
    @app.post("/mcp")
    async def mcp(request: Request, x_api_key: str | None = Header(default=None)):
        body = await request.json()
        rid = body.get("id")
        consumer = consumer_from_key(x_api_key)
        method = body.get("method")
        if method == "tools/list":
            tools = [{"name": n, "description": t["description"], "inputSchema": t["schema"]}
                     for n, t in ctx.tools.items() if consumer and ctx.allowed(f"tool:{n}", consumer)]
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}}
        if method == "tools/call":
            params = body.get("params", {})
            name = params.get("name")
            tool = ctx.tools.get(name)
            if tool is None:
                return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"tool {name} tidak ada"}}
            if not ctx.allowed(f"tool:{name}", consumer):
                ctx.audit("gateway", "TOOL_DENIED", {"tool": name, "consumer": consumer})
                return {"jsonrpc": "2.0", "id": rid, "error": {"code": 403, "message": f"governance: {consumer} tidak boleh memakai {name}"}}
            import anyio
            out = await anyio.to_thread.run_sync(lambda: tool["fn"](**params.get("arguments", {})))
            ctx.audit("gateway", "TOOL_CALL", {"tool": name, "consumer": consumer})
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, default=str)[:4000]}],
                "structuredContent": out}}
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found"}}

    return app


def register_tool(ctx: EnterpriseContext, name: str, description: str, schema: dict, fn: Callable) -> None:
    ctx.tools[name] = {"description": description, "schema": schema, "fn": fn}


def _short(obj, limit: int = 600):
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return obj if len(s) <= limit else {"summary": s[:limit] + "..."}

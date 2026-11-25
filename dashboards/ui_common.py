"""Komponen UI bersama untuk dashboard Streamlit tiap perusahaan & Control Center."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import requests  # noqa: E402
import streamlit as st  # noqa: E402

from common import schema_mapping as sm  # noqa: E402
from common import timeutil as tu  # noqa: E402
from common.config import API_KEYS, CONTROL_CENTER, ENTERPRISES, api_url, dash_url  # noqa: E402

META = dict(ENTERPRISES, control_center=CONTROL_CENTER)


def setup(eid: str) -> dict:
    m = META[eid]
    st.set_page_config(page_title=f"{m['short']} Dashboard", page_icon=m["icon"], layout="wide")
    st.markdown(f"""<style>
    .eco-header {{background:{m['color']};color:#fff;padding:14px 22px;border-radius:10px;margin-bottom:10px}}
    .eco-header h2 {{margin:0;color:#fff}} .eco-header small {{opacity:.85}}
    div[data-testid="stMetric"] {{background:rgba(127,127,127,.06);border:1px solid rgba(127,127,127,.18);
        border-radius:10px;padding:8px 12px}}
    .pill {{display:inline-block;padding:2px 9px;border-radius:12px;font-size:12px;margin-right:4px;
        background:rgba(127,127,127,.15)}}
    </style>""", unsafe_allow_html=True)
    return m


def header(eid: str, user: dict | None = None) -> None:
    m = META[eid]
    who = f" · 👤 {user['name']} ({user['role']})" if user else ""
    st.markdown(f"""<div class="eco-header"><h2>{m['icon']} {m['name']}</h2>
    <small>{m.get('tagline', 'Message bus A2A · decision trace · KPI ekosistem')} · Platform: {m.get('platform', 'FastAPI + SQLite')}{who}</small>
    </div>""", unsafe_allow_html=True)


def login(eid: str) -> dict | None:
    """Login & role simulasi (U_i). Tidak ada kredensial nyata."""
    m = META[eid]
    roles = m.get("roles", ["Ecosystem Admin", "Auditor"])
    key = f"user_{eid}"
    with st.sidebar:
        st.markdown(f"### {m['icon']} {m['short']}")
        if key not in st.session_state:
            with st.form(f"login_{eid}"):
                st.caption("Login simulasi (data & pengguna fiktif)")
                name = st.text_input("Nama pengguna", value=f"demo.{eid}")
                role = st.selectbox("Peran", roles)
                if st.form_submit_button("Masuk", type="primary"):
                    st.session_state[key] = {"name": name, "role": role}
                    st.rerun()
            return None
        user = st.session_state[key]
        st.success(f"👤 {user['name']}\n\n**{user['role']}**")
        if st.button("Keluar"):
            del st.session_state[key]
            st.rerun()
        st.divider()
        st.markdown(f"[🔗 Server API]({api_url(eid)}) · [Swagger]({api_url(eid)}/docs)")
        st.markdown(f"[🌐 Control Center]({dash_url('control_center')})")
        return user


def headers(as_eid: str, role: str | None = None) -> dict:
    h = {"X-API-Key": API_KEYS[as_eid]}
    if role:
        h["X-User-Role"] = role
    return h


def get(eid: str, path: str, as_eid: str | None = None, xml: bool = False, quiet: bool = False):
    try:
        r = requests.get(f"{api_url(eid)}{path}", headers=headers(as_eid or eid), timeout=20)
        r.raise_for_status()
        return r.text if xml else r.json()
    except Exception as exc:
        if not quiet:
            st.error(f"Gagal memanggil {eid}{path}: {exc}")
        return None


def post(eid: str, path: str, body=None, as_eid: str | None = None, role: str | None = None, text: str | None = None):
    try:
        h = headers(as_eid or eid, role)
        if text is not None:
            h["Content-Type"] = "text/plain"
            r = requests.post(f"{api_url(eid)}{path}", data=text.encode("utf-8"), headers=h, timeout=60)
        else:
            r = requests.post(f"{api_url(eid)}{path}", json=body or {}, headers=h, timeout=60)
        if r.status_code >= 400:
            st.error(f"{r.status_code}: {r.text[:300]}")
            return None
        return r.text if r.headers.get("content-type", "").startswith("application/xml") else r.json()
    except Exception as exc:
        st.error(f"Gagal: {exc}")
        return None


def h2dt(series):
    return pd.to_datetime([tu.h2dt(x).replace(tzinfo=None) if x is not None and x == x else None for x in series])


def clock(h):
    return tu.clock(h) if h is not None and h == h else "-"


def agents_panel(eid: str) -> None:
    d = get(eid, "/agents/status", quiet=True)
    if not d:
        st.warning("Server API belum berjalan.")
        return
    cfg = d.get("config", {})
    st.markdown(f"<span class='pill'>strategi: <b>{cfg.get('strategy')}</b></span>"
                f"<span class='pill'>adaptive: <b>{cfg.get('adaptive')}</b></span>"
                f"<span class='pill'>waktu simulasi: <b>{clock(cfg.get('sim_time_h'))}</b></span>",
                unsafe_allow_html=True)
    rows = [{"Agen": a["address"], "Peran": a["role"], "State": a["state"], "Pesan diproses": a["handled"],
             "Pesan dikirim": a["sent"], "Aksi terakhir": a["last_action"], "Keputusan terakhir": a["last_decision"]}
            for a in d["agents"]]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    with st.expander("Beliefs (state internal agen)"):
        for a in d["agents"]:
            st.markdown(f"**{a['address']}** — goal: _{a['goal'] or '-'}_")
            st.json(a["beliefs"], expanded=False)


def messages_panel(eid: str, limit: int = 200) -> None:
    try:
        r = requests.get(f"{api_url('control_center')}/a2a/messages?limit={limit}", headers=headers(eid), timeout=10)
        msgs = r.json()["messages"]
    except Exception as exc:
        st.warning(f"Control Center tidak tersedia: {exc}")
        return
    if not msgs:
        st.info("Belum ada pesan yang melibatkan perusahaan ini.")
        return
    df = pd.DataFrame([{"#": m["id"], "Waktu sim": clock(m["sim_time_h"]), "Tipe": m["message_type"],
                        "Pengirim": m["sender"], "Penerima": m["receiver"], "Task": m["task"],
                        "Status": m["status"], "Latensi (ms)": m["latency_ms"]} for m in msgs][::-1])
    st.dataframe(df, width="stretch", hide_index=True, height=320)
    pick = st.selectbox("Lihat payload pesan", [m["id"] for m in msgs][::-1], key=f"msgpick_{eid}")
    m = next(x for x in msgs if x["id"] == pick)
    st.json({k: m[k] for k in ("message_id", "conversation_id", "in_reply_to", "sender", "receiver", "message_type",
                               "task", "sim_time_h", "payload")}, expanded=False)


def audit_panel(eid: str) -> None:
    d = get(eid, "/audit?limit=300")
    if not d:
        return
    chain = d["chain"]
    (st.success if chain["valid"] else st.error)(
        f"Hash-chain audit {'VALID' if chain['valid'] else 'RUSAK'} · {chain['entries']} entri (tamper-evident)")
    rows = [{"Seq": e["seq"], "Sim": clock(e["sim_time_h"]), "Agen": e["agent"], "Aksi": e["action"],
             "Keputusan": (e["detail"] or {}).get("decision") if isinstance(e["detail"], dict) else str(e["detail"])[:80],
             "Butuh manusia": "✋" if e["human_required"] else "", "Hash": e["hash"]} for e in d["entries"]]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, height=360)
    pick = st.selectbox("Detail entri audit", [e["seq"] for e in d["entries"]], key=f"audit_{eid}")
    st.json(next(e for e in d["entries"] if e["seq"] == pick)["detail"], expanded=False)


def governance_panel(eid: str) -> None:
    g = get(eid, "/governance")
    if not g:
        return
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Kebijakan akses data (siapa boleh membaca apa)**")
        st.dataframe(pd.DataFrame([{"Resource": k, "Konsumen diizinkan": ", ".join(v) or "(hanya internal)"}
                                   for k, v in g["access"].items()]), hide_index=True, width="stretch")
    with c2:
        st.markdown("**Data minimization**")
        for x in g.get("data_minimization", []):
            st.markdown(f"- {x}")
        st.markdown("**Wajib persetujuan manusia**")
        for x in g.get("human_approval_required", []):
            st.markdown(f"- ✋ {x}")
        st.markdown("**Batasan AI**")
        for x in g.get("ai_limits", []):
            st.markdown(f"- ⚠️ {x}")
    st.markdown("**Decision policy**")
    st.json(g.get("decision_policy", {}), expanded=False)


def explorer(eid: str, endpoints: dict) -> None:
    """endpoints: label -> (path, kind) dengan kind 'json' | 'xml'."""
    label = st.selectbox("Endpoint native", list(endpoints), key=f"exp_{eid}")
    path, kind = endpoints[label]
    st.code(f"GET {api_url(eid)}{path}   (header X-API-Key)", language="http")
    raw = get(eid, path, xml=(kind == "xml"))
    if raw is None:
        return
    c1, c2 = st.columns([1, 1])
    with c1:
        st.markdown("**Respons native (format asli perusahaan)**")
        if kind == "xml":
            st.code(raw[:6000], language="xml")
        else:
            st.json(raw, expanded=False)
    with c2:
        st.markdown("**Tabel**")
        try:
            if kind == "xml":
                _, rows = sm.xml_rows(raw)
            else:
                data = raw.get("Data", raw) if isinstance(raw, dict) else raw
                rows = next((v for v in data.values() if isinstance(v, list)), [data]) if isinstance(data, dict) else data
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        except Exception as exc:
            st.caption(f"tidak dapat ditabelkan: {exc}")


def gantt(df: pd.DataFrame, start: str, end: str, y: str, color: str, text: str | None = None, title: str = "",
          height: int = 330, now_h: float | None = None):
    d = df.copy()
    d["_start"] = h2dt(d[start])
    d["_end"] = h2dt(d[end])
    fig = px.timeline(d, x_start="_start", x_end="_end", y=y, color=color, text=text, title=title,
                      hover_data=[c for c in d.columns if not c.startswith("_")])
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=40 if title else 10, b=10), legend_title_text="")
    if now_h is not None:
        x = tu.h2dt(now_h).replace(tzinfo=None)
        fig.add_shape(type="line", x0=x, x1=x, y0=0, y1=1, yref="paper", line=dict(color="red", dash="dot"))
        fig.add_annotation(x=x, y=1, yref="paper", text="sekarang", showarrow=False, font=dict(color="red"))
    return fig


def auto_refresh_toggle(key: str, default: bool = False) -> int | None:
    on = st.sidebar.toggle("🔄 Auto-refresh (3 dtk)", value=default, key=key)
    return 3 if on else None


def j(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)

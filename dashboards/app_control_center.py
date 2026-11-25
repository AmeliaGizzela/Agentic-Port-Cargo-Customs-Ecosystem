"""Global Agentic Control Center dashboard (port 8500)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import requests  # noqa: E402
import streamlit as st  # noqa: E402

from dashboards import ui_common as ui  # noqa: E402
from common import schema_mapping as sm  # noqa: E402
from common.config import ENTERPRISES, RESULTS_DIR, api_url, dash_url  # noqa: E402
from control_center.scenarios import SCENARIOS  # noqa: E402

EID = "control_center"
CC = api_url(EID)
ui.setup(EID)
user = ui.login(EID)
ui.header(EID, user)
if not user:
    st.info("Silakan masuk melalui panel kiri (login simulasi). Role 'Ecosystem Admin' dapat menjalankan skenario.")
    st.stop()
admin = user["role"] == "Ecosystem Admin"
refresh = ui.auto_refresh_toggle("rf_cc", default=False)
LANES = ["environment"] + list(ENTERPRISES) + ["control_center"]
COLORS = {e: m["color"] for e, m in ENTERPRISES.items()} | {"environment": "#7F8C8D", "control_center": "#5B2C6F"}


def cc_get(path):
    try:
        return requests.get(f"{CC}{path}", headers=ui.headers(EID), timeout=30).json()
    except Exception as exc:
        st.error(f"Control Center API tidak tersedia: {exc}")
        return None


def cc_post(path, body=None):
    try:
        r = requests.post(f"{CC}{path}", json=body or {}, headers=ui.headers(EID), timeout=120)
        if r.status_code >= 400:
            st.error(r.text[:300])
            return None
        return r.json()
    except Exception as exc:
        st.error(str(exc))
        return None


tabs = st.tabs(["🌐 Ekosistem", "🎬 Skenario & Lingkungan", "💬 Komunikasi Agen", "🤝 Negosiasi", "🧠 Decision Trace",
                "📈 KPI & Evaluasi", "🗂️ Data Kanonik & Schema Mapping", "🏗️ Arsitektur & Registry"])


# ---------------------------------------------------------------- ekosistem
@st.fragment(run_every=refresh)
def ecosystem():
    cfg = cc_get("/sim/config") or {}
    run = (cc_get("/scenario/status") or {}).get("run") or {}
    st.markdown(f"<span class='pill'>strategi <b>{cfg.get('strategy')}</b></span>"
                f"<span class='pill'>adaptive <b>{cfg.get('adaptive')}</b></span>"
                f"<span class='pill'>auto_officer <b>{cfg.get('auto_officer')}</b></span>"
                f"<span class='pill'>waktu simulasi <b>{ui.clock(cfg.get('sim_time_h'))}</b></span>"
                f"<span class='pill'>skenario <b>{run.get('scenario', '-')}</b> ({run.get('status', '-')})</span>",
                unsafe_allow_html=True)
    k = cc_get("/kpi")
    if not k:
        return
    e = k["ecosystem"]
    c = st.columns(5)
    c[0].metric("Objektif global J ↓", e["J_global_objective"])
    c[1].metric("C · biaya ekosistem", f"{e['C_total_cost_jt']:.0f} jt", help="juta Rupiah")
    c[2].metric("T · tunggu kapal + dwelling", f"{e['T_time_h']} j")
    c[3].metric("R · risiko", e["R_risk"])
    c[4].metric("Q · tepat waktu", f"{e['Q_on_time_rate']:.0%}")
    c = st.columns(6)
    c[0].metric("Rata tunggu kapal", f"{e['avg_vessel_wait_h']} j")
    c[1].metric("Dwelling kontainer", f"{e['avg_container_dwell_h']} j")
    c[2].metric("Negosiasi (ronde)", f"{e['negotiations']} ({e['negotiation_rounds']})")
    c[3].metric("Menunggu petugas BC", e["awaiting_officer"])
    c[4].metric("Kontrak express", int(e["express_contracts"] or 0))
    c[5].metric("Pelanggaran kepatuhan", e["compliance_violations"])
    if e.get("awaiting_officer"):
        st.warning(f"✋ {e['awaiting_officer']} deklarasi menunggu keputusan petugas Bea Cukai → buka "
                   f"[dashboard Customs]({dash_url('customs')}) (AI tidak dapat menyetujui).")
    reg = cc_get("/registry")
    if reg:
        cols = st.columns(len(ENTERPRISES))
        for col, item in zip(cols, [r for r in reg["registry"] if r["enterprise"] in ENTERPRISES]):
            with col.container(border=True):
                up = item["health"] == "UP"
                st.markdown(f"**{item['icon']} {item['name']}**  \n{'🟢 UP' if up else '🔴 ' + item['health']}")
                if up:
                    for a in item["status"]["agents"]:
                        st.caption(f"`{a['address'].split('.')[1]}` · {a['state']} · {a['handled']} pesan")
                st.markdown(f"[API]({item['api']}) · [Dashboard]({item['dashboard']})")
    u = k["local_utilities"]
    st.plotly_chart(px.bar(pd.DataFrame([{"Perusahaan": a, "U_i (juta Rp)": b} for a, b in u.items() if b is not None]),
                           x="Perusahaan", y="U_i (juta Rp)", color="Perusahaan", height=260,
                           color_discrete_map=COLORS, title="Utilitas lokal U_i = Revenue − Cost − RiskPenalty"),
                    width="stretch")
    if k["berth_plan"]:
        df = pd.DataFrame(k["berth_plan"]).rename(columns={"vessel_id": "Kapal", "berth_id": "Dermaga"})
        st.plotly_chart(ui.gantt(df, "start_h", "end_h", "Dermaga", "Kapal", text="Kapal", title="Rencana sandar aktif",
                                 height=260, now_h=cfg.get("sim_time_h")), width="stretch")


with tabs[0]:
    ecosystem()

# ------------------------------------------------------------------ skenario
with tabs[1]:
    sc = cc_get("/scenarios")
    if sc:
        c1, c2 = st.columns([2, 1])
        with c1:
            sid = st.selectbox("Skenario", list(sc["scenarios"]), format_func=lambda s: sc["scenarios"][s]["title"])
            st.caption(sc["scenarios"][sid]["description"])
            for ev in sc["scenarios"][sid]["events"]:
                st.markdown(f"- ⏱️ {ui.clock(ev['at'])} · `{ev['type']}` → `{ev['target']}` — {ev['payload'].get('description', ev['payload'].get('note', ''))}")
        with c2:
            strategy = st.radio("Strategi", list(sc["strategies"]), index=2,
                                format_func=lambda s: {"static": "Static (tanpa adaptasi)", "local": "Local intelligence",
                                                       "collaborative": "Collaborative (ekosistem)"}[s])
            st.caption(sc["strategies"][strategy])
            auto = st.toggle("Proxy petugas Bea Cukai tersimulasi", value=True,
                             help="Matikan untuk mendemokan human-in-the-loop: deklarasi menunggu keputusan di dashboard Customs.")
            delay = st.slider("Jeda antar pesan (detik, agar alur terlihat)", 0.0, 1.5, 0.4, 0.1)
        if st.button("▶️ Reset & jalankan skenario", type="primary", disabled=not admin):
            r = cc_post("/scenario/run", {"scenario_id": sid, "strategy": strategy, "auto_officer": auto,
                                          "bus_delay_s": delay})
            if r:
                st.success(f"Skenario berjalan: {r['run_id']}. Buka tab 💬 Komunikasi Agen (aktifkan auto-refresh).")

    @st.fragment(run_every=2)
    def run_status():
        s = cc_get("/scenario/status")
        if s and s.get("run"):
            r = s["run"]
            st.info(f"**{r['run_id']}** · {r['scenario']} · {r['strategy']} · status **{r['status']}** · langkah: "
                    f"{r.get('step')} · antrean bus {s['pending']} · waktu simulasi {ui.clock(s['sim_time_h'])}")
            if r.get("status") == "DONE":
                b, a = r["baseline"], r["after"]
                keys = ["J_global_objective", "C_total_cost_jt", "avg_vessel_wait_h", "avg_container_dwell_h",
                        "Q_on_time_rate", "importer_penalty_jt", "late_departures", "negotiations", "berth_plan_versions",
                        "truck_plan_versions", "compliance_violations"]
                st.dataframe(pd.DataFrame([{"KPI": k_, "Setelah baseline (normal)": b.get(k_),
                                            "Setelah gangguan + adaptasi": a.get(k_)} for k_ in keys]),
                             hide_index=True, width="stretch")
            if r.get("error"):
                st.error(r["error"])

    run_status()
    st.divider()
    st.markdown("#### Injeksi perubahan lingkungan manual (tanpa reset)")
    c = st.columns(4)
    if c[0].button("🔄 Reset ekosistem", disabled=not admin):
        cc_post("/sim/reset")
        st.toast("Semua perusahaan di-reset")
    if c[1].button("🌅 Mulai hari (start_of_day)", disabled=not admin):
        cc_post("/sim/config", {"strategy": "collaborative", "adaptive": True})
        cc_post("/sim/start-day")
    events = {ev["type"]: ev for s in SCENARIOS.values() for ev in s["events"]}
    pick = c[2].selectbox("Event", list(events), label_visibility="collapsed")
    if c[3].button("⚡ Injeksi event", disabled=not admin):
        ev = events[pick]
        cc_post("/env/inject", {"event_type": ev["type"], "target": ev["target"], "payload": ev["payload"], "at": ev["at"]})
        st.toast(f"Event {pick} dikirim ke {ev['target']}")
    evs = cc_get("/env/events")
    if evs and evs["events"]:
        st.dataframe(pd.DataFrame([{"#": e["id"], "Waktu sim": ui.clock(e["sim_time_h"]), "Event": e["event_type"],
                                    "Agen penerima": e["target"], "Deskripsi": e["payload"].get("description", e["payload"].get("note", ""))}
                                   for e in evs["events"]]), hide_index=True, width="stretch")

# ------------------------------------------------------------- komunikasi
with tabs[2]:
    @st.fragment(run_every=refresh)
    def comm():
        d = cc_get("/a2a/messages?limit=400")
        if not d or not d["messages"]:
            st.info("Belum ada pesan. Jalankan skenario.")
            return
        msgs = d["messages"]
        st.caption(f"{len(msgs)} pesan · antrean {d['pending']}")
        df = pd.DataFrame([{"#": m["id"], "sim": m["sim_time_h"], "Tipe": m["message_type"],
                            "Dari": m["sender"].split(".")[0], "Ke": m["receiver"].split(".")[0],
                            "Pengirim": m["sender"], "Penerima": m["receiver"], "Task": m["task"],
                            "Status": m["status"], "Konversasi": m["conversation_id"]} for m in msgs])
        # diagram sekuens: tiap pesan = panah dari lane pengirim ke lane penerima
        fig = go.Figure()
        y = {lane: i for i, lane in enumerate(LANES)}
        tail = df.tail(80)
        for _, r in tail.iterrows():
            fig.add_annotation(x=r["#"], y=y.get(r["Ke"], 0), ax=r["#"], ay=y.get(r["Dari"], 0), xref="x", yref="y",
                               axref="x", ayref="y", showarrow=True, arrowhead=2, arrowsize=1, arrowwidth=1.5,
                               arrowcolor=COLORS.get(r["Dari"], "#888"))
        fig.add_trace(go.Scatter(x=tail["#"], y=[y.get(v, 0) for v in tail["Dari"]], mode="markers",
                                 marker=dict(size=6, color=[COLORS.get(v, "#888") for v in tail["Dari"]]),
                                 text=[f"{a} → {b}<br>{t} ({mt})" for a, b, t, mt in
                                       zip(tail["Pengirim"], tail["Penerima"], tail["Task"], tail["Tipe"])],
                                 hoverinfo="text", showlegend=False))
        fig.update_yaxes(tickvals=list(y.values()), ticktext=list(y.keys()))
        fig.update_layout(height=380, margin=dict(l=10, r=10, t=30, b=10), title="Diagram sekuens 80 pesan terakhir",
                          xaxis_title="nomor pesan")
        st.plotly_chart(fig, width="stretch")
        c1, c2 = st.columns([3, 2])
        with c1:
            types = st.multiselect("Filter tipe", sorted(df["Tipe"].unique()), key="ftype")
            show = df[df["Tipe"].isin(types)] if types else df
            st.dataframe(show.iloc[::-1].drop(columns=["Dari", "Ke"]), hide_index=True, width="stretch", height=360)
        with c2:
            mat = pd.crosstab(df["Dari"], df["Ke"]).reindex(index=LANES, columns=LANES, fill_value=0)
            mat = mat.loc[(mat.sum(axis=1) > 0), (mat.sum(axis=0) > 0)]
            fig2 = px.imshow(mat, text_auto=True, color_continuous_scale="Purples", aspect="auto",
                             labels=dict(x="Penerima", y="Pengirim", color="Pesan"),
                             title="Matriks aliran pesan antar-perusahaan")
            fig2.update_layout(height=360, margin=dict(l=5, r=5, t=40, b=5))
            st.plotly_chart(fig2, width="stretch")
        pick = st.selectbox("Payload pesan", df["#"].iloc[::-1])
        st.json(next(m for m in msgs if m["id"] == pick), expanded=False)

    comm()

# --------------------------------------------------------------- negosiasi
with tabs[3]:
    conv = cc_get("/a2a/conversations")
    if conv and conv["conversations"]:
        st.markdown("**Percakapan A2A (multi-pesan)**")
        st.dataframe(pd.DataFrame(conv["conversations"]), hide_index=True, width="stretch")
    neg = ui.get("smartport", "/api/negotiations", as_eid=EID, quiet=True)
    if neg and neg["negotiations"]:
        st.markdown("#### Negosiasi jadwal sandar SmartPort ↔ OceanLine (alternating offers + mediasi)")
        for n in neg["negotiations"][::-1]:
            with st.expander(f"{n['neg_id']} · pemicu {n['trigger']} · {n['status']} · {n['round']} ronde · "
                             f"opsi disepakati {n['agreed_option']}", expanded=True):
                st.markdown(" → ".join(f"**R{x['round']}** `{x['by']}` {x['action']} **{x['option']}**" for x in n["log"]))
                st.dataframe(pd.DataFrame([{"Opsi": o["option_id"], "Keterangan": o.get("label", ""),
                                            "U_SP (jt)": o["sp_utility"], "Objektif global": o["objective"],
                                            "Rata tunggu": o["metrics"]["avg_wait_h"],
                                            "Jendela kapal mitra": "; ".join(f"{v['vessel_id']} {v['berth_id']} "
                                                                             f"{ui.clock(v['start_h'])}" for v in o["ol_view"])}
                                           for o in n["options"]]), hide_index=True, width="stretch")
    tr = cc_get("/trace?limit=1000")
    if tr:
        rows = [t for t in tr["trace"] if t["action"] in ("NEGOTIATE", "QUOTE", "QUOTE_EXPRESS", "EVALUATE_EXPRESS",
                                                          "REQUEST_EXPRESS", "ACCEPT_TRUCKING", "MEDIATE",
                                                          "REQUEST_MEDIATION", "CONTRACT_EXPRESS")]
        if rows:
            st.markdown("#### Langkah negosiasi (Contract Net & express trucking, alternating offers dermaga)")
            for t in rows:
                with st.expander(f"{ui.clock(t['sim_time_h'])} · {t['agent']} · {t['action']} → {t['decision']}"):
                    for r in t["reasoning"]:
                        st.markdown(f"- {r}")

# ------------------------------------------------------------------ trace
with tabs[4]:
    @st.fragment(run_every=refresh)
    def trace():
        tr = cc_get("/trace?limit=1500")
        if not tr or not tr["trace"]:
            st.info("Belum ada keputusan.")
            return
        df = pd.DataFrame(tr["trace"])
        c1, c2, c3 = st.columns(3)
        ents = c1.multiselect("Perusahaan", sorted(df["enterprise"].unique()))
        acts = c2.multiselect("Aksi", sorted(df["action"].unique()))
        human = c3.toggle("Hanya yang butuh manusia ✋")
        sel = df
        if ents:
            sel = sel[sel["enterprise"].isin(ents)]
        if acts:
            sel = sel[sel["action"].isin(acts)]
        if human:
            sel = sel[sel["human_required"] == 1]
        fig = px.scatter(sel, x="id", y="enterprise", color="action", hover_data=["agent", "decision"], height=300,
                         title="Timeline keputusan agen (perceive → reason → decide → act)")
        st.plotly_chart(fig, width="stretch")
        for _, t in sel.iloc[::-1].head(60).iterrows():
            flag = " ✋" if t["human_required"] else ""
            with st.expander(f"#{t['id']} · {ui.clock(t['sim_time_h'])} · {t['agent']} · {t['action']}{flag} → {t['decision']}"):
                st.markdown(f"**Persepsi:** {t['perception']}")
                st.markdown("**Penalaran:**\n" + "\n".join(f"- {r}" for r in (t["reasoning"] or [])))
                if t["alternatives"]:
                    st.markdown(f"**Alternatif:** {t['alternatives']}")
                if t["kpi"]:
                    st.json(t["kpi"], expanded=False)
                st.caption(f"strategi {t['strategy']} · konversasi {t['conversation_id'] or '-'}")

    trace()

# --------------------------------------------------------------------- KPI
with tabs[5]:
    hist = cc_get("/kpi/history")
    if hist and hist["history"]:
        st.markdown("#### Snapshot KPI run terakhir")
        rows = [{"label": h["label"], "strategi": h["strategy"], "skenario": h["scenario"], **h["kpi"]["ecosystem"]}
                for h in hist["history"]]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.markdown("#### Evaluasi strategi (static vs local vs collaborative) pada semua skenario")
    ev = cc_get("/evaluation")
    if ev and ev["rows"]:
        df = pd.DataFrame(ev["rows"])
        for col in df.columns:
            if col not in ("scenario", "strategy"):
                df[col] = pd.to_numeric(df[col], errors="coerce")
        metric = st.selectbox("KPI", ["J_global_objective", "C_total_cost_jt", "avg_vessel_wait_h", "avg_container_dwell_h",
                                      "Q_on_time_rate", "importer_penalty_jt", "late_container_hours", "T_time_h"])
        fig = px.bar(df, x="scenario", y=metric, color="strategy", barmode="group", height=380,
                     color_discrete_map={"static": "#9AA5B1", "local": "#E3A33B", "collaborative": "#2E7D6B"})
        st.plotly_chart(fig, width="stretch")
        piv = df.pivot_table(index="scenario", columns="strategy", values="J_global_objective")
        if {"static", "collaborative"} <= set(piv.columns):
            piv["perbaikan vs static (%)"] = ((piv["static"] - piv["collaborative"]) / piv["static"] * 100).round(1)
        if {"local", "collaborative"} <= set(piv.columns):
            piv["perbaikan vs local (%)"] = ((piv["local"] - piv["collaborative"]) / piv["local"] * 100).round(1)
        st.markdown("**Objektif global J per skenario** (positif = kolaboratif lebih baik)")
        st.dataframe(piv.round(4), width="stretch")
        with st.expander("Tabel lengkap"):
            st.dataframe(df, hide_index=True, width="stretch")
    else:
        st.info("Belum ada hasil evaluasi. Jalankan: `python -m simulation.run_evaluation`")
    for p in sorted(RESULTS_DIR.glob("chart_*.png")):
        st.image(str(p), caption=p.name)

# --------------------------------------------------------- data kanonik
with tabs[6]:
    s = cc_get("/ecosystem/state")
    if s:
        if s["errors"]:
            st.warning(s["errors"])
        st.caption("Control Center hanya membaca endpoint 'shared' sesuai izin governance, lalu memetakan ke model kanonik.")
        for key, title in [("fleet_eta", "OceanLine → ETA kapal"), ("berth_plan", "SmartPort → rencana sandar"),
                           ("deliveries", "GlobalCargo → status pengiriman"), ("shipments", "ImportExport (XML) → shipment"),
                           ("declarations", "Customs → status deklarasi")]:
            st.markdown(f"**{title}**")
            st.dataframe(pd.DataFrame(s.get(key, [])), hide_index=True, width="stretch")
    m = cc_get("/schema-mapping")
    if m:
        st.markdown("#### Pemetaan field native → kanonik")
        for eid, fm in m["field_map"].items():
            with st.expander(f"{ENTERPRISES[eid]['icon']} {eid} — {fm['format']}"):
                st.dataframe(pd.DataFrame(fm["fields"].items(), columns=["Field native", "Field kanonik"]),
                             hide_index=True, width="stretch")
        st.markdown("**Contoh satu waktu (08:00 WIB) dalam 5 format native**")
        st.json(m["time_example"])

# ------------------------------------------------------------- arsitektur
with tabs[7]:
    st.graphviz_chart("""
digraph G { rankdir=LR; node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10];
 env [label="Environment\\n(cuaca, crane, jalan,\\ntruk, intelijen)", fillcolor="#D5D8DC"];
 cc [label="Global Agentic Control Center :8000\\nmessage bus A2A · registry · mediator\\ndecision trace · KPI · skenario", fillcolor="#D7BDE2"];
 ol [label="OceanLine :8001\\nDocStore JSON · UTC\\neta_agent (GBR) · negotiation · manifest", fillcolor="#AED6F1"];
 sp [label="SmartPort :8002\\nSQL relasional · WIB\\nberth MIP · yard · ops monitor", fillcolor="#A3E4D7"];
 gc [label="GlobalCargo :8003\\nSQL PascalCase · epoch\\nVRPTW · route (Dijkstra) · commercial", fillcolor="#F9E79F"];
 ie [label="ImportExport :8004\\nSQL Indonesia · XML\\ndocument NLP · procurement", fillcolor="#F5B7B1"];
 cu [label="Customs :8005\\nSQL + hash audit · ISO\\nrisk (IsolationForest) · verification\\n+ petugas manusia", fillcolor="#D5DBDB"];
 env -> cc [label="ENV_EVENT"];
 cc -> ol [dir=both]; cc -> sp [dir=both]; cc -> gc [dir=both]; cc -> ie [dir=both]; cc -> cu [dir=both];
 ol -> sp [style=dashed, label="ETA_UPDATE / negosiasi"]; sp -> gc [style=dashed, label="availability"];
 ie -> cu [style=dashed, label="PIB"]; cu -> sp [style=dashed, label="release"]; ie -> gc [style=dashed, label="CFP"];
 cu -> ol [style=dotted, label="MCP get_manifest"];
}""")
    st.caption("Garis putus-putus = pesan A2A logis (fisik melalui message bus). Titik-titik = akses tools gaya MCP.")
    st.markdown("#### Model enterprise $E_i = (U_i, M_i, D_i, A_i, P_i, G_i)$")
    reg = cc_get("/registry")
    if reg:
        rows = []
        for item in reg["registry"]:
            if item["enterprise"] not in ENTERPRISES:
                continue
            m = ENTERPRISES[item["enterprise"]]
            card = item.get("card", {})
            rows.append({"E_i": m["name"], "U_i (peran)": ", ".join(m["roles"]),
                         "M_i (modul)": m["tagline"], "D_i (data)": m["platform"].split("·")[0],
                         "A_i (agen)": ", ".join(a["address"].split(".")[1] for a in card.get("agents", [])),
                         "P_i (protokol/API)": m["platform"].split("·")[1] if "·" in m["platform"] else "",
                         "G_i (governance)": f"{item['api']}/governance"})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        pick = st.selectbox("Agent Card (/.well-known/agent-card.json)", [r["enterprise"] for r in reg["registry"]
                                                                           if r.get("card")])
        st.json(next(r["card"] for r in reg["registry"] if r["enterprise"] == pick), expanded=False)

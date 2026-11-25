"""Dashboard SmartPort Terminal (port 8502)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from dashboards import ui_common as ui  # noqa: E402
from common import timeutil as tu  # noqa: E402

EID = "smartport"
ui.setup(EID)
user = ui.login(EID)
ui.header(EID, user)
if not user:
    st.info("Silakan masuk melalui panel kiri (login simulasi).")
    st.stop()
refresh = ui.auto_refresh_toggle("rf_sp")

tabs = st.tabs(["📊 Ringkasan", "🗓️ Jadwal Dermaga", "🧮 Optimizer MIP (what-if)", "🤝 Negosiasi", "📦 Yard & Kontainer",
                "🧠 Agen & Pesan", "🗂️ Data Explorer", "🔒 Audit & Governance"])


def plan_df(rows):
    return pd.DataFrame([{"Kapal": r["vessel_id"], "Nama": r.get("vessel_name", ""), "Dermaga": r["berth_id"],
                          "start_h": tu.from_local(r["start_time"]), "end_h": tu.from_local(r["end_time"]),
                          "ETA": r.get("eta_current", ""), "Mulai": r["start_time"], "Selesai": r["end_time"],
                          "Tunggu (j)": r["wait_h"], "Status": r["status"]} for r in rows])


@st.fragment(run_every=refresh)
def overview():
    k = ui.get(EID, "/api/shared/kpi")
    if not k:
        return
    lk, ut = k["localKpi"], k["utility"]
    if not lk:
        st.info("Belum ada rencana sandar. Jalankan skenario dari Global Control Center.")
        return
    c = st.columns(6)
    c[0].metric("Rata-rata tunggu kapal", f"{lk.get('avg_wait_h', 0)} j")
    c[1].metric("Kapal tunggu > 2 jam", lk.get("vessels_wait_gt_2h"))
    c[2].metric("Utilisasi dermaga (24 j)", f"{(lk.get('berth_utilization_24h') or 0):.0%}")
    c[3].metric("Versi rencana", lk.get("plan_versions"))
    c[4].metric("Negosiasi disepakati", f"{lk.get('negotiations_agreed')}/{lk.get('negotiations')}")
    c[5].metric("Utilitas U_SP", f"{ut['U']} jt")
    bp = ui.get(EID, "/api/berth-plan")
    cfg = ui.get(EID, "/admin/config", quiet=True) or {}
    if bp and bp["rows"]:
        df = plan_df(bp["rows"])
        st.plotly_chart(ui.gantt(df, "start_h", "end_h", "Dermaga", "Kapal", text="Kapal",
                                 title=f"Rencana sandar aktif v{bp['plan_version']}", now_h=cfg.get("sim_time_h")),
                        width="stretch")
        st.dataframe(df.drop(columns=["start_h", "end_h"]), hide_index=True, width="stretch")


with tabs[0]:
    overview()

with tabs[1]:
    h = ui.get(EID, "/api/berth-plan/history")
    if h and h["versions"]:
        metas = pd.DataFrame(h["versions"])
        st.dataframe(metas[["plan_version", "created_sim", "trigger", "strategy", "mode", "solver", "objective",
                            "solve_ms", "sp_utility", "negotiation_id"]], hide_index=True, width="stretch")
        vs = [int(x) for x in metas["plan_version"]]
        c1, c2 = st.columns(2)
        a = c1.selectbox("Bandingkan versi", vs, index=0)
        b = c2.selectbox("dengan versi", vs, index=len(vs) - 1)
        rows = pd.DataFrame(h["rows"])
        for col, ver in ((c1, a), (c2, b)):
            sub = rows[rows["plan_version"] == ver]
            df = pd.DataFrame([{"Kapal": r["vessel_id"], "Dermaga": r["berth_id"],
                                "start_h": tu.from_local(r["start_time"]), "end_h": tu.from_local(r["end_time"]),
                                "Status": r["status"]} for _, r in sub.iterrows()])
            col.plotly_chart(ui.gantt(df, "start_h", "end_h", "Dermaga", "Kapal", text="Kapal", title=f"Versi {ver}",
                                      height=300), width="stretch")
        note = metas[metas["plan_version"] == b]["note"].iloc[0]
        st.caption(f"Catatan versi {b}: {note}")
    else:
        st.info("Belum ada riwayat rencana.")

with tabs[2]:
    st.markdown("Jalankan **Berth Allocation MIP** (SciPy HiGHS) tanpa mempublikasikan rencana — alat bantu Port Planner.")
    vc = ui.get(EID, "/api/vessel-calls")
    if vc:
        vdf = pd.DataFrame(vc["vessel_calls"])
        c1, c2 = st.columns([1, 2])
        mode = c1.radio("Fungsi tujuan", ["global", "local", "static"], format_func={
            "global": "Global (ekosistem)", "local": "Lokal SmartPort", "static": "Statis / FCFS"}.get)
        vid = c2.selectbox("Ubah ETA kapal (skenario what-if)", vdf["vessel_id"])
        cur = tu.from_local(vdf[vdf["vessel_id"] == vid]["eta_current"].iloc[0])
        new_eta = c2.slider("ETA baru (jam sejak 20/11 00:00)", 0.0, 30.0, float(cur), 0.5)
        if st.button("▶️ Jalankan optimizer", type="primary"):
            res = ui.post(EID, "/api/planner/what-if", {"mode": mode, "eta_overrides": {vid: new_eta}})
            if res and res["plan"]:
                m = res["metrics"]
                c = st.columns(5)
                c[0].metric("Status solver", res["status"][:18])
                c[1].metric("Waktu solve", f"{res.get('solve_ms', 0)} ms")
                c[2].metric("Rata tunggu", f"{m['avg_wait_h']} j")
                c[3].metric("Telat berangkat", m["late_departures"])
                c[4].metric("U_SP", f"{res['sp_utility']['utility']} jt")
                df = pd.DataFrame(res["plan"]).rename(columns={"vessel_id": "Kapal", "berth_id": "Dermaga"})
                st.plotly_chart(ui.gantt(df, "start_h", "end_h", "Dermaga", "Kapal", text="Kapal",
                                         title="Hasil what-if"), width="stretch")
                st.caption(f"Variabel biner: {res.get('n_vars', '-')}, kendala: {res.get('n_constraints', '-')}")
    with st.expander("Formulasi MIP"):
        st.latex(r"\min \sum_{v,b,t} c_{vbt}\,x_{vbt}\quad \text{s.t.}\ \sum_{b,t}x_{vbt}=1\ \forall v,\ "
                 r"\sum_{v}\sum_{t'=t-p_{vbt'}+1}^{t}x_{vbt'}\le 1\ \forall b,t,\ x_{vbt}\in\{0,1\}")
        st.markdown("- $c_{vbt}$ global = biaya tunggu kapal + penalti telat berangkat + biaya crane (lembur malam) "
                    "+ biaya stabilitas rencana\n- $c_{vbt}$ lokal = penalti kontrak (tunggu > 2 jam) + biaya crane\n"
                    "- $p_{vbt}$ dihitung dari integrasi produktivitas crane (gangguan crane mengubah durasi)")

with tabs[3]:
    n = ui.get(EID, "/api/negotiations")
    if n and n["negotiations"]:
        for neg in n["negotiations"][::-1]:
            with st.expander(f"{neg['neg_id']} · pemicu {neg['trigger']} · {neg['status']} · ronde {neg['round']} · "
                             f"disepakati {neg['agreed_option'] or '-'}", expanded=True):
                st.dataframe(pd.DataFrame([{"Opsi": o["option_id"], "Keterangan": o.get("label", ""),
                                            "U_SP (jt)": o["sp_utility"], "Objektif global": o["objective"],
                                            "Rata tunggu (j)": o["metrics"]["avg_wait_h"],
                                            "Rencana": "; ".join(f"{p['vessel_id']}->{p['berth_id']} {tu.clock(p['start_h'])}"
                                                                for p in o["plan"])} for o in neg["options"]]),
                             hide_index=True, width="stretch")
                st.markdown(" → ".join(f"**R{x['round']}** {x['by']}: {x['action']} {x['option']}" for x in neg["log"]))
    else:
        st.info("Belum ada negosiasi (negosiasi muncul saat replanning mengubah jendela sandar kapal mitra).")

with tabs[4]:
    y = ui.get(EID, "/api/yard/container-batches")
    av = ui.get(EID, "/api/yard/availability")
    if y:
        c1, c2 = st.columns([2, 1])
        c1.markdown("**Batch kontainer per shipment**")
        c1.dataframe(pd.DataFrame(y["container_batches"]), hide_index=True, width="stretch")
        c2.markdown("**Blok yard**")
        yb = pd.DataFrame(y["yard_blocks"])
        yb["okupansi"] = (yb["used_teu"] / yb["capacity_teu"]).map("{:.0%}".format)
        c2.dataframe(yb, hide_index=True, width="stretch")
    if av and av["availability"]:
        st.markdown("**Ketersediaan kontainer untuk trucker (bongkar + release Bea Cukai)**")
        st.dataframe(pd.DataFrame([{"Shipment": a["shipment_id"], "Kapal": a["vessel_id"], "Dermaga": a["berth_id"],
                                    "Kontainer": a["containers"], "Bongkar pertama": tu.clock(a["discharge_h"][0]),
                                    "Siap pertama": ui.clock(a["ready_h"][0]), "Siap terakhir": ui.clock(a["ready_h"][-1]),
                                    "Status": a["status"], "Bea Cukai": a["customs_status"], "Jalur": a["lane"]}
                                   for a in av["availability"]]), hide_index=True, width="stretch")
        st.caption("Governance: kontainer hanya boleh keluar gate setelah status RELEASED dari Customs Authority.")

with tabs[5]:
    ui.agents_panel(EID)
    st.markdown("#### Pesan A2A yang melibatkan SmartPort")
    ui.messages_panel(EID)

with tabs[6]:
    ui.explorer(EID, {"Dermaga": ("/api/berths", "json"), "Vessel calls": ("/api/vessel-calls", "json"),
                      "Rencana sandar": ("/api/berth-plan", "json"), "Shared berth plan": ("/api/shared/berth-plan", "json"),
                      "Batch kontainer": ("/api/yard/container-batches", "json"),
                      "KPI lokal": ("/api/shared/kpi", "json"), "Agent card (A2A)": ("/.well-known/agent-card.json", "json")})

with tabs[7]:
    ui.governance_panel(EID)
    st.divider()
    ui.audit_panel(EID)

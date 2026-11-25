"""Dashboard OceanLine Shipping (port 8501)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import streamlit as st  # noqa: E402

from dashboards import ui_common as ui  # noqa: E402
from common import timeutil as tu  # noqa: E402

EID = "oceanline"
ui.setup(EID)
user = ui.login(EID)
ui.header(EID, user)
if not user:
    st.info("Silakan masuk melalui panel kiri (login simulasi).")
    st.stop()
refresh = ui.auto_refresh_toggle("rf_ol")

tabs = st.tabs(["📊 Ringkasan", "🗺️ Pelacakan Kapal", "🤖 Prediksi ETA (ML)", "🤝 Negosiasi Sandar", "📄 Manifest",
                "🧠 Agen & Pesan", "🗂️ Data Explorer", "🔒 Audit & Governance"])


@st.fragment(run_every=refresh)
def overview():
    k = ui.get(EID, "/api/v1/shared/kpi")
    v = ui.get(EID, "/api/v1/vessels")
    if not k or not v:
        return
    lk, ut = k["localKpi"], k["utility"]
    c = st.columns(6)
    c[0].metric("Kapal dipantau", lk["vesselsTracked"])
    c[1].metric("Kapal terlambat", lk["vesselsDelayed"])
    c[2].metric("Total jam tunggu", f"{lk['totalWaitingHours']} j")
    c[3].metric("Telat berangkat", f"{lk['lateDepartureHours']} j")
    c[4].metric("Biaya tunggu", f"Rp{lk['waitingCostJt']} jt")
    c[5].metric("Utilitas U_OL", f"{ut['U']} jt", help="U = Revenue - Cost - RiskPenalty (lokal)")
    rows = []
    for x in v["vessels"]:
        bw = x.get("berthWindow") or {}
        rows.append({"Kapal": x["vesselId"], "Nama": x["name"], "Operator": x["operator"],
                     "Armada sendiri": "✅" if x["ownFleet"] else "—",
                     "ETA jadwal (UTC)": x["voyage"]["scheduledEtaUtc"], "ETA terkini (UTC)": x["currentEtaUtc"],
                     "Dermaga": bw.get("berthId", "-"), "Sandar (UTC)": bw.get("startUtc", "-"),
                     "Status sandar": bw.get("status", "-"), "Kontainer impor": x["cargo"]["importContainers"]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("Format native OceanLine: camelCase, waktu UTC (WIB = UTC+7).")


with tabs[0]:
    overview()

with tabs[1]:
    v = ui.get(EID, "/api/v1/vessels")
    if v:
        df = pd.DataFrame([{"Kapal": x["vesselId"], "Nama": x["name"], "lat": x["livePosition"]["lat"],
                            "lon": x["livePosition"]["lon"], "ETA": x["currentEtaUtc"],
                            "Jenis": "OceanLine" if x["ownFleet"] else "Pelayaran lain"} for x in v["vessels"]])
        port = pd.DataFrame([{"Kapal": "PORT", "Nama": "Pelabuhan Nusa Raya", "lat": -6.10, "lon": 106.88, "ETA": "",
                              "Jenis": "Pelabuhan"}])
        map_view = st.radio("Tampilan peta", ["Peta jalan", "Satelit"], horizontal=True)
        fig = px.scatter_map(
            pd.concat([df, port], ignore_index=True), lat="lat", lon="lon", color="Jenis",
            hover_name="Nama", hover_data=["Kapal", "ETA"], zoom=6,
            center={"lat": -5.4, "lon": 107.5}, map_style="open-street-map",
            color_discrete_map={"OceanLine": "#0072ce", "Pelayaran lain": "#45c0f0", "Pelabuhan": "#e63946"},
        )
        if map_view == "Satelit":
            fig.update_layout(map_style="white-bg", map_layers=[{
                "below": "traces",
                "sourcetype": "raster",
                "source": [
                    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
                ],
                "sourceattribution": "Esri, Maxar, Earthstar Geographics, and the GIS User Community",
            }])
        fig.update_traces(marker=dict(size=12))
        fig.update_layout(
            height=520, margin=dict(l=0, r=0, t=10, b=0),
            legend=dict(orientation="h", x=0, y=1.01, yanchor="bottom"),
            uirevision="oceanline-map",
        )
        st.plotly_chart(fig, width="stretch")
        st.caption(f"Posisi diproyeksikan dari ETA & kecepatan rencana pada waktu simulasi {v['simTimeUtc']}.")

with tabs[2]:
    m = ui.get(EID, "/api/v1/model", as_eid="control_center")
    if m:
        c = st.columns(4)
        c[0].metric("MAE model", f"{m['mae_h']} jam")
        c[1].metric("R²", m["r2"])
        c[2].metric("MAE baseline jadwal", f"{m['baseline_schedule_mae_h']} jam")
        c[3].metric("MAE regresi linear", f"{m['linear_regression_mae_h']} jam")
        st.caption(f"{m['model']} · data latih {m['n_train']} voyage historis sintetis, uji {m['n_test']}")
        fi = pd.DataFrame(m["feature_importance"].items(), columns=["Fitur", "Importance"]).sort_values("Importance")
        st.plotly_chart(px.bar(fi, x="Importance", y="Fitur", orientation="h", height=280,
                               title="Feature importance"), width="stretch")
    st.markdown("#### What-if prediksi keterlambatan")
    c1, c2, c3 = st.columns(3)
    body = {"distanceNm": c1.slider("Jarak tersisa (nm)", 20, 600, 126),
            "plannedSpeedKn": c1.slider("Kecepatan rencana (kn)", 10.0, 22.0, 18.0),
            "windKn": c2.slider("Angin (kn)", 0, 60, 35), "waveM": c2.slider("Gelombang (m)", 0.2, 7.0, 3.6),
            "currentKn": c3.slider("Arus (kn, + membantu)", -2.0, 2.0, -0.8),
            "congestionIdx": c3.slider("Indeks kongesti", 0.0, 1.0, 0.3), "vesselTeu": 6500}
    r = ui.post(EID, "/api/v1/eta/predict", body, as_eid="control_center")
    if r:
        p = r["prediction"]
        st.success(f"Prediksi keterlambatan: **{p['delayHours']:.2f} jam** (interval P10–P90: {p['p10']:.2f} – {p['p90']:.2f} jam)")
    preds = ui.get(EID, "/api/v1/etaPredictions", as_eid="control_center", quiet=True)
    if preds and preds["predictions"]:
        st.markdown("#### Prediksi aktif per kapal")
        st.dataframe(pd.DataFrame([{"Kapal": p["vesselId"], "Delay mentah (j)": p["rawDelayHours"],
                                    "Delay diterapkan (j)": p["appliedDelayHours"], "P10": p["p10"], "P90": p["p90"],
                                    "ETA prediksi (UTC)": p["predictedEtaUtc"], "Alasan": p["reason"]}
                                   for p in preds["predictions"]]), hide_index=True, width="stretch")

with tabs[3]:
    n = ui.get(EID, "/api/v1/negotiations", as_eid="control_center")
    if n and n["negotiations"]:
        for neg in n["negotiations"][::-1]:
            with st.expander(f"{neg['negId']} · pemicu {neg.get('trigger')} · status **{neg['status']}** · "
                             f"opsi disepakati {neg.get('agreedOption', '-')}", expanded=True):
                for r in neg["rounds"]:
                    st.markdown(f"Ronde {r['round']}: tawaran terminal **{r['offer']}**, aspirasi OceanLine "
                                f"{r['aspiration']} jt")
                    st.dataframe(pd.DataFrame([{"Opsi": k, "U_OL (jt)": u} for k, u in r["utilities"].items()]),
                                 hide_index=True)
        st.caption("Fungsi utilitas internal tidak dibagikan ke terminal; hanya ACCEPT / COUNTER_PROPOSE yang dikirim.")
    else:
        st.info("Belum ada negosiasi.")
    bw = ui.get(EID, "/api/v1/berthWindows", as_eid="control_center", quiet=True)
    if bw and bw["berthWindows"]:
        st.markdown("#### Jendela sandar diterima dari SmartPort")
        st.dataframe(pd.DataFrame(bw["berthWindows"]), hide_index=True, width="stretch")

with tabs[4]:
    mf = ui.get(EID, "/api/v1/manifests", as_eid="customs")
    if mf:
        st.dataframe(pd.DataFrame(mf["manifests"]), hide_index=True, width="stretch")
        st.caption("Manifest (BC 1.1) hanya boleh diakses Customs Authority — tampilan ini memakai kunci Customs "
                   "untuk demonstrasi kebijakan akses.")

with tabs[5]:
    ui.agents_panel(EID)
    st.markdown("#### Pesan A2A yang melibatkan OceanLine")
    ui.messages_panel(EID)

with tabs[6]:
    ui.explorer(EID, {"Kapal (vessels)": ("/api/v1/vessels", "json"),
                      "Shared fleet ETA": ("/api/v1/shared/fleetEta", "json"),
                      "Prediksi ETA": ("/api/v1/etaPredictions", "json"),
                      "Negosiasi": ("/api/v1/negotiations", "json"),
                      "KPI lokal": ("/api/v1/shared/kpi", "json"),
                      "Agent card (A2A)": ("/.well-known/agent-card.json", "json")})

with tabs[7]:
    ui.governance_panel(EID)
    st.divider()
    ui.audit_panel(EID)

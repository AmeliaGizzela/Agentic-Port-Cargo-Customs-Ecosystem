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
                            "lon": x["livePosition"]["lon"], "ETA": x["currentEtaUtc"], "Kecepatan (kn)": "",
                            "Jenis": "OceanLine" if x["ownFleet"] else "Pelayaran lain (simulasi)"}
                           for x in v["vessels"]])
        port = pd.DataFrame([{"Kapal": "PORT", "Nama": "Pelabuhan Nusa Raya", "lat": -6.10, "lon": 106.88, "ETA": "",
                              "Kecepatan (kn)": "", "Jenis": "Pelabuhan"}])
        frames = [df, port]
        c1, c2 = st.columns([1, 3])
        show_ais = c1.toggle("Kapal nyata (AIS)", value=True, key="ol_ais",
                             help="Posisi AIS real-time dari AISStream.io di sekitar Laut Jawa (bukan armada simulasi).")
        ais = ui.get(EID, "/api/v1/ais/live", quiet=True) if show_ais else None
        if ais:
            if ais["status"] == "connected":
                c2.success(f"AIS nyata: {ais['count']} kapal, {ais['messages']} pesan sejak "
                           f"{ais['connectedSinceUtc']} (sumber: {ais['source']})")
            elif ais["status"] == "disabled":
                c2.info("Umpan AIS nonaktif. Set `AISSTREAM_API_KEY` (env atau file `.env`) lalu restart server "
                        "OceanLine. Kunci gratis: https://aisstream.io")
            else:
                c2.warning(f"Umpan AIS: {ais['status']} - {ais.get('lastError') or ''}")
            if ais["vessels"]:
                frames.append(pd.DataFrame([{"Kapal": a["shipName"], "Nama": f"MMSI {a['mmsi']}", "lat": a["lat"],
                                             "lon": a["lon"], "ETA": a["timeUtc"][:19], "Kecepatan (kn)": a["speedKn"],
                                             "Jenis": "AIS nyata"} for a in ais["vessels"]]))
        allpts = pd.concat(frames)
        fig = px.scatter_geo(allpts, lat="lat", lon="lon", color="Jenis", text="Kapal",
                             hover_name="Nama", hover_data=["ETA", "Kecepatan (kn)"], projection="mercator",
                             color_discrete_map={"OceanLine": "#1f77b4", "Pelayaran lain (simulasi)": "#ff7f0e",
                                                 "Pelabuhan": "#d62728", "AIS nyata": "#7f7f7f"})
        fig.update_geos(fitbounds="locations", showcountries=True, showland=True, landcolor="#e9ecef",
                        showocean=True, oceancolor="#d6eaf8")
        fig.update_traces(marker=dict(size=12), textposition="top center")
        fig.update_traces(selector=dict(name="AIS nyata"), marker=dict(size=6, opacity=0.6), text=None, mode="markers")
        fig.update_layout(height=470, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, width="stretch")
        st.caption(f"Posisi armada simulasi diproyeksikan dari ETA & kecepatan rencana pada waktu simulasi "
                   f"{v['simTimeUtc']}. Titik abu-abu = kapal nyata (AIS), hanya sebagai konteks lingkungan.")
        if ais and ais["vessels"]:
            with st.expander(f"Daftar kapal AIS nyata ({ais['count']})"):
                st.dataframe(pd.DataFrame(ais["vessels"])[["mmsi", "shipName", "lat", "lon", "speedKn", "courseDeg",
                                                            "navStatus", "timeUtc"]], width="stretch", hide_index=True)

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

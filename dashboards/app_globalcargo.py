"""Dashboard GlobalCargo Logistics (port 8503)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from dashboards import ui_common as ui  # noqa: E402
from common import timeutil as tu  # noqa: E402

EID = "globalcargo"
ui.setup(EID)
user = ui.login(EID)
ui.header(EID, user)
if not user:
    st.info("Silakan masuk melalui panel kiri (login simulasi).")
    st.stop()
refresh = ui.auto_refresh_toggle("rf_gc")

tabs = st.tabs(["📊 Ringkasan", "🚚 Jadwal Truk (VRPTW)", "🛣️ Jaringan Jalan", "📑 Job & Kontrak",
                "🧠 Agen & Pesan", "🗂️ Data Explorer", "🔒 Audit & Governance"])


def data(path):
    d = ui.get(EID, path)
    return d["Data"] if d and d.get("Status") == "OK" else None


@st.fragment(run_every=refresh)
def overview():
    k = data("/gc/v2/Shared/Kpi")
    if not k:
        return
    lk, ut = k["LocalKpi"], k["Utility"]
    c = st.columns(6)
    c[0].metric("Trip direncanakan", f"{lk['TripsPlanned']}/{lk['JobsTotal']}")
    c[1].metric("Tepat waktu", f"{(lk['OnTimeRate'] or 0):.0%}")
    c[2].metric("Kontainer-jam terlambat", lk["LateContainerHours"])
    c[3].metric("Rata dwelling", f"{lk['AvgDwellHours']} j")
    c[4].metric("Truk dipakai (subkon trip)", f"{lk['TrucksUsed']} ({lk['SubconTrips']})")
    c[5].metric("Utilitas U_GC", f"{ut['U']} jt")
    ds = data("/gc/v2/Shared/DeliveryStatus")
    if ds:
        st.dataframe(pd.DataFrame([{"Shipment": s["ShipmentRef"], "Pelanggan": s["Customer"],
                                    "Kontainer": s["Containers"], "Terjadwal": s["Scheduled"],
                                    "Menunggu release": s["WaitingRelease"],
                                    "Pickup pertama": ui.clock(tu.from_epoch(s["FirstPickupEpoch"])) if s["FirstPickupEpoch"] else "-",
                                    "Kirim terakhir": ui.clock(tu.from_epoch(s["LastDeliveryEpoch"])) if s["LastDeliveryEpoch"] else "-",
                                    "Deadline": ui.clock(tu.from_epoch(s["DeadlineEpoch"])),
                                    "Terlambat": s["LateContainers"], "Express": "⚡" if s["Express"] else "",
                                    "Status": s["Status"]} for s in ds["Shipments"]]), hide_index=True, width="stretch")
    st.caption("Format native GlobalCargo: envelope {Status, Data}, PascalCase, waktu Unix epoch.")


with tabs[0]:
    overview()

with tabs[1]:
    tp = data("/gc/v2/TripPlan")
    if tp and tp["Trips"]:
        df = pd.DataFrame(tp["Trips"])
        rows = []
        for _, r in df.iterrows():
            p, dl, dn = tu.from_epoch(r["PickupEpoch"]), tu.from_epoch(r["DeliveredEpoch"]), tu.from_epoch(r["DoneEpoch"])
            late = dl > tu.from_epoch(r["DeadlineEpoch"]) + 1e-6
            rows.append({"Truk": f"{r['TruckId']} ({r['TruckType']})", "Shipment": r["ShipmentRef"], "Job": r["JobId"],
                         "start_h": p, "end_h": dl, "Fase": "Antar (bermuatan)", "Telat": late})
            rows.append({"Truk": f"{r['TruckId']} ({r['TruckType']})", "Shipment": r["ShipmentRef"], "Job": r["JobId"],
                         "start_h": dl, "end_h": dn, "Fase": "Bongkar di gudang", "Telat": late})
        g = pd.DataFrame(rows)
        st.plotly_chart(ui.gantt(g[g["Fase"] == "Antar (bermuatan)"], "start_h", "end_h", "Truk", "Shipment",
                                 title=f"Rencana trip v{tp['PlanVersion']} (pickup -> tiba di gudang)", height=420),
                        width="stretch")
        st.markdown("**Riwayat versi rencana (adaptive replanning)**")
        st.dataframe(pd.DataFrame(tp["Versions"]), hide_index=True, width="stretch")
    else:
        st.info("Belum ada rencana trip.")
    with st.expander("Algoritma"):
        st.markdown("""- **Graf jalan + Dijkstra (networkx)** menghitung waktu tempuh Port→Gudang; berubah saat ruas ditutup / macet.
- **Multi-trip VRPTW (full-truckload)**: setiap kontainer = job dengan *ready time* (bongkar + release Bea Cukai) dan *deadline*.
- **Konstruksi greedy** (EDD berbobot prioritas untuk mode kolaboratif, FCFS untuk mode lokal) + **local search relocate**.
- Fungsi tujuan kolaboratif: Σ bobot prioritas importir × keterlambatan shipment + SLA + lembur + kongesti yard (dwelling).
- Fungsi tujuan lokal: hanya biaya GlobalCargo (SLA kecil, lembur, jumlah truk).""")

with tabs[2]:
    rn = data("/gc/v2/RoadNetwork")
    if rn:
        nodes = {n["NodeId"]: n for n in rn["Nodes"]}
        fig = go.Figure()
        for e in rn["Edges"]:
            a, b = nodes[e["FromNode"]], nodes[e["ToNode"]]
            closed = e["Status"] != "OPEN"
            color = "#C0392B" if closed else ("#E67E22" if e["CongestionFactor"] > 1 else "#7F8C8D")
            fig.add_trace(go.Scatter(x=[a["Lon"], b["Lon"]], y=[a["Lat"], b["Lat"]], mode="lines",
                                     line=dict(color=color, width=4 if closed else 2, dash="dot" if closed else "solid"),
                                     hoverinfo="text", text=f"{e['FromNode']}–{e['ToNode']}: {e['Minutes']} mnt × "
                                                            f"{e['CongestionFactor']} ({e['Status']})", showlegend=False))
        kinds = {"port": "#1F618D", "depot": "#7D3C98", "warehouse": "#B9770E"}
        fig.add_trace(go.Scatter(x=[n["Lon"] for n in nodes.values()], y=[n["Lat"] for n in nodes.values()],
                                 mode="markers+text", text=list(nodes), textposition="top center",
                                 marker=dict(size=11, color=[kinds.get(n["Kind"], "#95A5A6") for n in nodes.values()]),
                                 hovertext=[n["Name"] for n in nodes.values()], showlegend=False))
        fig.update_layout(height=470, margin=dict(l=0, r=0, t=10, b=0), xaxis_title="Lon", yaxis_title="Lat")
        st.plotly_chart(fig, width="stretch")
        st.caption("Merah putus-putus = ruas ditutup; oranye = kongesti. Rute tercepat saat ini:")
        st.dataframe(pd.DataFrame([{"Tujuan": k, "Menit": v["Minutes"], "Jalur": " → ".join(v["Path"] or [])}
                                   for k, v in rn["Routes"].items()]), hide_index=True, width="stretch")
        c1, c2 = st.columns(2)
        a = c1.selectbox("Dari", list(nodes), index=list(nodes).index("PORT"))
        b = c2.selectbox("Ke", list(nodes), index=list(nodes).index("WH_KARAWANG"))
        r = data(f"/gc/v2/Route?From={a}&To={b}")
        if r:
            st.info(f"Dijkstra: {r['Minutes']:.0f} menit via {' → '.join(r['Path'])}")

with tabs[3]:
    jb = data("/gc/v2/Jobs")
    ct = data("/gc/v2/Contracts")
    fl = data("/gc/v2/Fleet")
    if fl:
        st.markdown("**Armada**")
        st.dataframe(pd.DataFrame(fl["Trucks"]), hide_index=True, width="stretch")
    if ct:
        st.markdown("**Kontrak & penawaran (Contract Net)**")
        st.dataframe(pd.DataFrame(ct["Contracts"]), hide_index=True, width="stretch")
        if ct["Quotes"]:
            st.dataframe(pd.DataFrame(ct["Quotes"]), hide_index=True, width="stretch")
    if jb:
        st.markdown("**Job transport (1 kontainer = 1 job)**")
        st.dataframe(pd.DataFrame(jb["Jobs"]), hide_index=True, width="stretch", height=300)

with tabs[4]:
    ui.agents_panel(EID)
    st.markdown("#### Pesan A2A yang melibatkan GlobalCargo")
    ui.messages_panel(EID)

with tabs[5]:
    ui.explorer(EID, {"Fleet": ("/gc/v2/Fleet", "json"), "Jobs": ("/gc/v2/Jobs", "json"),
                      "TripPlan": ("/gc/v2/TripPlan", "json"), "RoadNetwork": ("/gc/v2/RoadNetwork", "json"),
                      "Contracts": ("/gc/v2/Contracts", "json"), "DeliveryStatus (shared)": ("/gc/v2/Shared/DeliveryStatus", "json"),
                      "Kpi (shared)": ("/gc/v2/Shared/Kpi", "json"), "Agent card (A2A)": ("/.well-known/agent-card.json", "json")})

with tabs[6]:
    ui.governance_panel(EID)
    st.divider()
    ui.audit_panel(EID)

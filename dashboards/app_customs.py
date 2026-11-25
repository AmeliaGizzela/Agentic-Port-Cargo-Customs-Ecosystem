"""Dashboard Customs Authority (port 8505) - human-in-the-loop: AI merekomendasikan, petugas memutuskan."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from dashboards import ui_common as ui  # noqa: E402
from common import timeutil as tu  # noqa: E402

EID = "customs"
ui.setup(EID)
user = ui.login(EID)
ui.header(EID, user)
if not user:
    st.info("Silakan masuk melalui panel kiri (login simulasi). Role 'Customs Officer' dapat memberi keputusan.")
    st.stop()
refresh = ui.auto_refresh_toggle("rf_cu")
can_decide = user["role"] in ("Customs Officer", "Supervisor")
LANE = {"GREEN": "🟢 HIJAU", "YELLOW": "🟡 KUNING", "RED": "🔴 MERAH", None: "-"}

tabs = st.tabs(["✋ Antrian Petugas", "📋 Deklarasi", "🧠 Model Risiko", "📜 Aturan Kebijakan", "🤖 Agen & Pesan",
                "🗂️ Data Explorer", "🔒 Audit & Governance"])

with st.sidebar:
    cfg = ui.get(EID, "/admin/config", quiet=True) or {}
    st.markdown("**Mode petugas**")
    auto = st.toggle("Proxy petugas tersimulasi (auto_officer)", value=bool(cfg.get("auto_officer")),
                     disabled=user["role"] != "Supervisor",
                     help="Hanya Supervisor. Jika aktif, keputusan dicatat sebagai keputusan manusia TERSIMULASI "
                          "(untuk evaluasi KPI). AI tetap tidak pernah menyetujui sendiri.")
    if auto != bool(cfg.get("auto_officer")) and user["role"] == "Supervisor":
        ui.post(EID, "/admin/config", {"auto_officer": auto})
        st.rerun()


@st.fragment(run_every=refresh)
def queue():
    k = ui.get(EID, "/customs/api/shared/kpi")
    if k:
        lk = k["local_kpi"]
        c = st.columns(6)
        c[0].metric("Deklarasi", lk["declarations"])
        c[1].metric("Menunggu petugas", lk["awaiting_officer"])
        c[2].metric("Dirilis (SPPB)", lk["released"])
        c[3].metric("Ditahan", lk["on_hold"])
        c[4].metric("Jalur H/K/M", f"{lk['lane_green']}/{lk['lane_yellow']}/{lk['lane_red']}")
        c[5].metric("Rilis tanpa persetujuan manusia", lk["released_without_human_approval"],
                    help="Harus selalu 0 (kepatuhan governance)")
    q = ui.get(EID, "/customs/api/review-queue")
    if not q:
        return
    if not q["queue"]:
        st.success("Tidak ada deklarasi yang menunggu keputusan.")
        return
    if not can_decide:
        st.warning(f"Role {user['role']} hanya dapat melihat. Keputusan hanya oleh Customs Officer / Supervisor.")
    for d in q["queue"]:
        a = d.get("latest_assessment") or {}
        with st.container(border=True):
            c1, c2 = st.columns([3, 2])
            c1.markdown(f"### {d['declaration_no']} · {d['shipment_ref']}")
            c1.markdown(f"HS **{d['hs_code']}** {d['description']} · asal **{d['origin_country']}** · "
                        f"nilai USD {d['customs_value_usd']:,.0f} · {d['gross_weight_kg']:,.0f} kg · "
                        f"{d['containers']} kontainer · kapal {d['vessel_id']} · status **{d['status']}**")
            c2.markdown(f"#### Rekomendasi AI: {LANE.get(d['recommended_lane'])}")
            c2.caption(f"Skor anomali {a.get('anomaly_score')} (persentil {float(a.get('anomaly_percentile') or 0):.0%})"
                       f" · rasio nilai/referensi {a.get('features', {}).get('value_ratio')}")
            with c1.expander("Penjelasan (aturan yang terpicu & verifikasi manifest)", expanded=True):
                for hit in a.get("rule_hits", []):
                    st.markdown(f"- {hit}")
            notes = c2.text_input("Catatan petugas", key=f"n_{d['declaration_no']}")
            lane = c2.selectbox("Jalur final", ["GREEN", "YELLOW", "RED"],
                                index=["GREEN", "YELLOW", "RED"].index(d["recommended_lane"] or "GREEN"),
                                key=f"l_{d['declaration_no']}", disabled=not can_decide)
            b1, b2, b3 = c2.columns(3)
            for col, dec, label in ((b1, "APPROVE", "✅ Setujui"), (b2, "HOLD", "⏸️ Tahan"), (b3, "REJECT", "⛔ Tolak")):
                if col.button(label, key=f"{dec}_{d['declaration_no']}", disabled=not can_decide):
                    res = ui.post(EID, "/customs/api/officer/decisions",
                                  {"declaration_no": d["declaration_no"], "decision": dec, "officer": user["name"],
                                   "final_lane": lane, "notes": notes}, role=user["role"])
                    if res:
                        st.toast(f"{dec} {d['declaration_no']} → release {ui.clock(res.get('release_h'))}")
                        st.rerun()


with tabs[0]:
    queue()

with tabs[1]:
    ds = ui.get(EID, "/customs/api/declarations")
    if ds and ds["declarations"]:
        st.dataframe(pd.DataFrame([{"No. aju": d["declaration_no"], "Shipment": d["shipment_ref"], "HS": d["hs_code"],
                                    "Asal": d["origin_country"], "Nilai USD": d["customs_value_usd"],
                                    "Rekomendasi AI": LANE.get(d["recommended_lane"]), "Jalur final": LANE.get(d["final_lane"]),
                                    "Status": d["status"], "Diajukan": d["submitted_at"], "Release": d["release_at"],
                                    "Cocok manifest": "✅" if d["manifest_match"] else "❌"} for d in ds["declarations"]]),
                     hide_index=True, width="stretch")
        log = ui.get(EID, "/customs/api/officer/decisions")
        if log and log["decisions"]:
            st.markdown("**Log keputusan petugas (manusia)**")
            st.dataframe(pd.DataFrame(log["decisions"]), hide_index=True, width="stretch")
    else:
        st.info("Belum ada deklarasi.")

with tabs[2]:
    m = ui.get(EID, "/customs/api/risk-model")
    if m:
        c = st.columns(5)
        c[0].metric("ROC-AUC skor anomali", m["roc_auc_anomaly_score"])
        c[1].metric("Recall flag (K+M)", f"{m['flag_recall']:.0%}")
        c[2].metric("Precision flag", f"{m['flag_precision']:.0%}")
        c[3].metric("Precision jalur merah", f"{m['red_precision']:.0%}")
        c[4].metric("Tingkat pelanggaran historis", f"{m['violation_rate']:.1%}")
        st.caption(f"{m['model']} · {m['n_history']} deklarasi historis sintetis. Distribusi jalur: {m['lane_share']}")
    st.markdown("#### What-if penilaian risiko")
    c1, c2, c3 = st.columns(3)
    body = {"hs_code": c1.selectbox("HS code", ["8517.13", "8542.31", "2915.12", "7208.51", "5208.52", "8483.40"]),
            "origin_country": c1.selectbox("Negara asal", ["China", "India", "Vietnam", "Japan", "South Korea", "UAE"]),
            "gross_weight_kg": c2.number_input("Berat kotor (kg)", 1000, 300000, 16000),
            "customs_value_usd": c2.number_input("Nilai pabean (USD)", 1000, 5_000_000, 210000),
            "containers": c3.number_input("Kontainer", 1, 20, 4), "has_permit": c3.checkbox("Ada izin impor", True),
            "doc_consistency": c3.slider("Konsistensi dokumen", 0.5, 1.0, 1.0), "importer_score": 0.85}
    r = ui.post(EID, "/customs/api/risk/what-if", body)
    if r:
        st.markdown(f"**Rekomendasi: {LANE.get(r['recommended_lane'])}** · skor {r['anomaly_score']} "
                    f"(persentil {r['anomaly_percentile']:.0%})")
        for h in r["rule_hits"]:
            st.markdown(f"- {h}")
        st.json(r["features"], expanded=False)

with tabs[3]:
    pr = ui.get(EID, "/customs/api/policy-rules")
    if pr:
        st.dataframe(pd.DataFrame(pr["rules"]), hide_index=True, width="stretch")
        st.markdown(f"Durasi proses per jalur (jam): `{pr['lane_hours']}`  \n⚠️ {pr['note']}")
    intel = ui.get(EID, "/customs/api/intel")
    if intel and intel["alerts"]:
        st.markdown("**Nota hasil intelijen aktif (rahasia, tidak dibagikan)**")
        st.dataframe(pd.DataFrame(intel["alerts"]), hide_index=True, width="stretch")

with tabs[4]:
    ui.agents_panel(EID)
    st.markdown("#### Pesan A2A yang melibatkan Customs")
    ui.messages_panel(EID)

with tabs[5]:
    ui.explorer(EID, {"Deklarasi": ("/customs/api/declarations", "json"), "Antrian review": ("/customs/api/review-queue", "json"),
                      "Manifest (BC 1.1)": ("/customs/api/manifests", "json"),
                      "Status deklarasi (shared)": ("/customs/api/shared/declaration-status", "json"),
                      "KPI (shared)": ("/customs/api/shared/kpi", "json"),
                      "Agent card (A2A)": ("/.well-known/agent-card.json", "json")})

with tabs[6]:
    ui.governance_panel(EID)
    st.divider()
    ui.audit_panel(EID)

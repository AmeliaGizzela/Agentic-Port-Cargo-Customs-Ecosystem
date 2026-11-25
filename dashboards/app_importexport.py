"""Dashboard ImportExport Corp (port 8504). API native perusahaan ini berformat XML."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import streamlit as st  # noqa: E402

from dashboards import ui_common as ui  # noqa: E402
from common import schema_mapping as sm  # noqa: E402

EID = "importexport"
ui.setup(EID)
user = ui.login(EID)
ui.header(EID, user)
if not user:
    st.info("Silakan masuk melalui panel kiri (login simulasi).")
    st.stop()
refresh = ui.auto_refresh_toggle("rf_ie")

tabs = st.tabs(["📊 Ringkasan", "📑 Pengiriman & Kesiapan Dokumen", "🔎 Analisis Dokumen (NLP)", "🧠 Model NLP",
                "🤝 Kontrak Angkutan", "🤖 Agen & Pesan", "🗂️ Data Explorer (XML)", "🔒 Audit & Governance"])


def xml(path):
    raw = ui.get(EID, path, xml=True)
    return sm.xml_rows(raw) if raw else (None, [])


@st.fragment(run_every=refresh)
def overview():
    _, rows = xml("/api/xml/kpi")
    if not rows:
        return
    k = {kk: sm._num(v) for kk, v in rows[0].items()}
    c = st.columns(6)
    c[0].metric("Dokumen lengkap", f"{int(k['dokumen_lengkap'])}/{int(k['jumlah_pengiriman'])}")
    c[1].metric("Deklarasi diajukan", int(k["deklarasi_diajukan"]))
    c[2].metric("Dirilis Bea Cukai", int(k["dirilis_pabean"]))
    c[3].metric("Total jam terlambat", k["total_jam_terlambat"])
    c[4].metric("Denda produksi", f"Rp{k['denda_produksi_juta']} jt")
    c[5].metric("Utilitas U_IE", f"{k['utilitas_U']} jt")
    _, ship = xml("/api/xml/shared/status-pengiriman")
    if ship:
        st.dataframe(pd.DataFrame(ship), hide_index=True, width="stretch")
    st.caption("Sistem ERP ImportExport memakai skema berbahasa Indonesia & API XML; mitra melakukan schema mapping.")


with tabs[0]:
    overview()

with tabs[1]:
    _, ready = xml("/api/xml/kesiapan")
    if ready:
        df = pd.DataFrame([{"Pengiriman": r["id_pengiriman"], "Status": r["status"], "Skor": r["skor"],
                            "Wajib": ", ".join(json.loads(r["wajib"])), "Kurang": ", ".join(json.loads(r["kurang"])) or "-"}
                           for r in ready])
        st.dataframe(df, hide_index=True, width="stretch")
        sid = st.selectbox("Detail rule engine untuk", [r["id_pengiriman"] for r in ready])
        r = next(x for x in ready if x["id_pengiriman"] == sid)
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Hasil aturan R1–R7**")
            for chk in json.loads(r["aturan"]):
                st.markdown(f"{'✅' if chk['passed'] else '❌'} {chk['rule']}")
        with c2:
            for i in json.loads(r["isu"]):
                st.error(i)
            for w in json.loads(r["peringatan"]):
                st.warning(w)
            if not json.loads(r["isu"]):
                st.success("Tidak ada isu blocking")
        st.markdown(f"**Dokumen {sid} & hasil ekstraksi NLP**")
        _, docs = xml(f"/api/xml/dokumen?id_pengiriman={sid}")
        if docs:
            st.dataframe(pd.DataFrame(docs), hide_index=True, width="stretch")

with tabs[2]:
    st.markdown("Tempel teks dokumen perdagangan untuk diklasifikasi (TF-IDF + Logistic Regression) & diekstrak (regex).")
    sample = ("COMMERCIAL INVOICE\nInvoice No: INV-0451-001\nSeller / Exporter: Shenzhen Microtech Co., Ltd\n"
              "Buyer / Consignee: PT ImportExport Corp\nHS Code: 8542.31\nQuantity: 120,000 pcs\n"
              "Total Amount: USD 480,000.00\nCountry of Origin: China")
    text = st.text_area("Teks dokumen", sample, height=200)
    if st.button("🔎 Analisis", type="primary"):
        res = ui.post(EID, "/api/xml/dokumen/analisis", text=text)
        if res:
            _, rows = sm.xml_rows(res)
            r = rows[0]
            st.success(f"Jenis dokumen: **{r['jenis_prediksi']}** (keyakinan {float(r['keyakinan']):.1%})")
            st.json(json.loads(r["hasil_ekstraksi"]))
            with st.expander("Respons XML mentah"):
                st.code(res, language="xml")

with tabs[3]:
    _, rows = xml("/api/xml/model")
    if rows:
        m = rows[0]
        st.metric("Akurasi uji", f"{float(m['accuracy']):.1%}")
        st.caption(f"{m['model']} · latih {m['n_train']} · uji {m['n_test']} dokumen sintetis (termasuk fragmen & derau OCR)")
        labels = json.loads(m["labels"])
        cm = json.loads(m["confusion_matrix"])
        fig = px.imshow(cm, x=labels, y=labels, text_auto=True, color_continuous_scale="Blues",
                        labels=dict(x="Prediksi", y="Aktual"), title="Confusion matrix")
        st.plotly_chart(fig, width="stretch")
        rep = json.loads(m["report"])
        st.dataframe(pd.DataFrame({k: v for k, v in rep.items() if isinstance(v, dict)}).T.round(3), width="stretch")

with tabs[4]:
    _, kt = xml("/api/xml/kontrak")
    if kt:
        st.dataframe(pd.DataFrame(kt), hide_index=True, width="stretch")
    else:
        st.info("Belum ada kontrak.")
    _, pib = xml("/api/xml/pemberitahuan-impor")
    if pib:
        st.markdown("**Pemberitahuan Impor (PIB) yang diajukan ke Customs**")
        st.dataframe(pd.DataFrame(pib), hide_index=True, width="stretch")

with tabs[5]:
    ui.agents_panel(EID)
    st.markdown("#### Pesan A2A yang melibatkan ImportExport")
    ui.messages_panel(EID)

with tabs[6]:
    ui.explorer(EID, {"Pesanan pembelian": ("/api/xml/pesanan", "xml"), "Pengiriman": ("/api/xml/pengiriman", "xml"),
                      "Kesiapan dokumen": ("/api/xml/kesiapan", "xml"), "Kontrak angkutan": ("/api/xml/kontrak", "xml"),
                      "Status pengiriman (shared)": ("/api/xml/shared/status-pengiriman", "xml"),
                      "KPI (shared)": ("/api/xml/kpi", "xml")})
    st.markdown("**Hasil schema mapping (XML → model kanonik ekosistem)**")
    raw = ui.get(EID, "/api/xml/shared/status-pengiriman", xml=True)
    if raw:
        st.dataframe(pd.DataFrame(sm.importexport_status(raw)), hide_index=True, width="stretch")

with tabs[7]:
    ui.governance_panel(EID)
    st.divider()
    ui.audit_panel(EID)

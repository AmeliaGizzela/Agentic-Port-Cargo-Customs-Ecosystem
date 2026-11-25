"""ImportExport Corp - server API (port 8004).

Model data: SQL dengan nama tabel/kolom berbahasa Indonesia; tanggal dd/mm/yyyy HH:MM.
Format API native: XML (sistem ERP lama) -> mitra harus melakukan schema mapping.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from fastapi import Body, Depends
from fastapi.responses import Response

from common import timeutil as tu
from common.agent_base import EnterpriseContext
from common.config import DATA_DIR, enterprise_db_path, model_dir
from common.db import Database
from common.enterprise_base import create_enterprise_app, make_guard, register_tool
from enterprises.importexport.agents import DocumentAgent, IERepo, ProcurementAgent
from enterprises.importexport.doc_nlp import DocClassifier, check_readiness, extract_fields

EID = "importexport"
db = Database(enterprise_db_path(EID))
ctx = EnterpriseContext(EID, db, audit_table="log_audit")
repo = IERepo(db)
clf = DocClassifier(model_dir(EID), DATA_DIR / "importexport" / "doc_training.csv").load_or_train()


def reset() -> None:
    db.drop_all()
    repo.__init__(db)
    repo.seed(clf)


if not db.scalar("SELECT COUNT(*) FROM pesanan_pembelian"):
    reset()

agents = [DocumentAgent(ctx, repo, clf), ProcurementAgent(ctx, repo)]
app = create_enterprise_app(ctx, agents, reset,
                            description="ImportExport Corp - Purchase order, document NLP & readiness, trucking "
                                        "contracts. Format native: XML + skema bahasa Indonesia.")


def to_xml(root_tag: str, item_tag: str, rows: list[dict], attrs: dict | None = None) -> Response:
    root = ET.Element(root_tag, attrib={k: str(v) for k, v in (attrs or {}).items()})
    for r in rows:
        el = ET.SubElement(root, item_tag)
        for k, v in r.items():
            child = ET.SubElement(el, k)
            if isinstance(v, (list, dict)):
                child.text = json.dumps(v, ensure_ascii=False)
            else:
                child.text = "" if v is None else str(v)
    ET.indent(root)
    return Response(content=ET.tostring(root, encoding="unicode", xml_declaration=True), media_type="application/xml")


@app.get("/api/xml/pesanan", tags=["native"])
def pesanan(consumer: str = Depends(make_guard(ctx, "pesanan"))):
    return to_xml("DaftarPesananPembelian", "Pesanan", db.query("SELECT * FROM pesanan_pembelian"))


@app.get("/api/xml/pengiriman", tags=["native"])
def pengiriman(consumer: str = Depends(make_guard(ctx, "pengiriman"))):
    return to_xml("DaftarPengiriman", "Pengiriman", db.query("SELECT * FROM pengiriman ORDER BY id_pengiriman"),
                  {"waktu_simulasi": tu.to_id(ctx.now())})


@app.get("/api/xml/dokumen", tags=["native"])
def dokumen(id_pengiriman: str | None = None, consumer: str = Depends(make_guard(ctx, "dokumen"))):
    rows = db.query("SELECT id_dokumen,id_pengiriman,nama_file,jenis_prediksi,keyakinan,hasil_ekstraksi,diterima_pada "
                    "FROM dokumen ORDER BY id_dokumen")
    rows = [r for r in rows if id_pengiriman in (None, r["id_pengiriman"])]
    return to_xml("DaftarDokumen", "Dokumen", rows)


@app.get("/api/xml/dokumen/teks", tags=["native"])
def dokumen_teks(id_dokumen: str, consumer: str = Depends(make_guard(ctx, "analisis"))):
    r = db.one("SELECT * FROM dokumen WHERE id_dokumen=?", (id_dokumen,))
    return to_xml("Dokumen", "Isi", [r] if r else [])


@app.get("/api/xml/kesiapan", tags=["native"])
def kesiapan(consumer: str = Depends(make_guard(ctx, "pengiriman"))):
    rows = []
    for s in db.query("SELECT * FROM pengiriman ORDER BY id_pengiriman"):
        res = check_readiness(repo.po(s["nomor_po"]), s, repo.docs(s["id_pengiriman"]), s["nama_kapal"])
        rows.append({"id_pengiriman": s["id_pengiriman"], "status": res["status"], "skor": res["score"],
                     "wajib": res["required"], "tersedia": res["present"], "kurang": res["missing"],
                     "isu": res["issues"], "peringatan": res["warnings"], "aturan": res["checks"]})
    return to_xml("KesiapanDokumen", "Pengiriman", rows)


@app.post("/api/xml/dokumen/analisis", tags=["native"])
def analisis(teks: str = Body(..., media_type="text/plain"), consumer: str = Depends(make_guard(ctx, "analisis"))):
    jenis, p = clf.predict(teks)
    return to_xml("HasilAnalisis", "Dokumen", [{"jenis_prediksi": jenis, "keyakinan": p,
                                                "hasil_ekstraksi": extract_fields(teks)}])


@app.get("/api/xml/model", tags=["native"])
def model(consumer: str = Depends(make_guard(ctx, "analisis"))):
    """Metrik model klasifikasi dokumen (dalam XML; confusion matrix & report sebagai JSON di dalam elemen)."""
    m = dict(clf.metrics)
    return to_xml("MetrikModel", "Model", [m], {"akurasi": m.get("accuracy")})


@app.get("/api/xml/kontrak", tags=["native"])
def kontrak(consumer: str = Depends(make_guard(ctx, "kontrak"))):
    return to_xml("DaftarKontrakAngkutan", "Kontrak", db.query("SELECT * FROM kontrak_angkutan"))


@app.get("/api/xml/pemberitahuan-impor", tags=["native"])
def pib(consumer: str = Depends(make_guard(ctx, "pengiriman"))):
    return to_xml("DaftarPIB", "PIB", db.query("SELECT nomor_aju,id_pengiriman,dikirim_pada,status,catatan "
                                               "FROM pemberitahuan_impor"))


@app.get("/api/xml/shared/status-pengiriman", tags=["shared"])
def status_pengiriman(consumer: str = Depends(make_guard(ctx, "status_pengiriman"))):
    rows = db.query("SELECT p.id_pengiriman, p.kapal_id, p.jumlah_kontainer, p.status_dokumen, p.skor_kesiapan, "
                    "p.status_pabean, p.jalur, p.waktu_rilis, p.status_angkutan, p.eta_gudang_terakhir, p.terlambat_jam, "
                    "p.ekspres, o.batas_waktu FROM pengiriman p JOIN pesanan_pembelian o ON o.nomor_po=p.nomor_po "
                    "ORDER BY p.id_pengiriman")
    return to_xml("StatusPengiriman", "Pengiriman", rows, {"waktu_simulasi": tu.to_id(ctx.now())})


@app.get("/api/xml/kpi", tags=["shared"])
def kpi(consumer: str = Depends(make_guard(ctx, "kpi"))):
    rows = db.query("SELECT p.*, o.denda_per_jam_juta, o.batas_waktu FROM pengiriman p "
                    "JOIN pesanan_pembelian o ON o.nomor_po=p.nomor_po")
    trucking = db.query("SELECT * FROM kontrak_angkutan")
    trip_cost = sum(k["tarif_per_trip"] * next(r["jumlah_kontainer"] for r in rows if r["id_pengiriman"] == k["id_pengiriman"])
                    for k in trucking if k["jenis"] == "STANDAR")
    express_cost = sum(k["biaya_tambahan_total"] for k in trucking if k["jenis"] == "EKSPRES")
    penalty = sum((r["terlambat_jam"] or 0) * r["denda_per_jam_juta"] for r in rows)
    on_time = sum(1 for r in rows if r["status_angkutan"] in ("ON_TIME", "AT_RISK"))
    vals = {"jumlah_pengiriman": len(rows),
            "dokumen_lengkap": sum(r["status_dokumen"] == "COMPLETE" for r in rows),
            "rata_skor_kesiapan": round(sum((r["skor_kesiapan"] or 0) for r in rows) / len(rows), 3),
            "deklarasi_diajukan": sum(1 for r in rows if r["nomor_aju"]),
            "dirilis_pabean": sum(r["status_pabean"] == "RILIS" for r in rows),
            "ditahan_pabean": sum(r["status_pabean"] == "DITAHAN" for r in rows),
            "pengiriman_tepat_waktu": on_time, "total_jam_terlambat": round(sum((r["terlambat_jam"] or 0) for r in rows), 2),
            "denda_produksi_juta": round(penalty, 2), "biaya_trucking_juta": round(trip_cost, 2),
            "biaya_ekspres_juta": round(express_cost, 2), "kontrak_ekspres": sum(r["ekspres"] for r in rows),
            "utilitas_U": round(-(trip_cost + express_cost + penalty), 2),
            "pendapatan_juta": 0.0, "biaya_juta": round(trip_cost + express_cost, 2), "penalti_risiko_juta": round(penalty, 2)}
    return to_xml("KpiLokal", "Kpi", [vals], {"perusahaan": EID})


register_tool(ctx, "get_document_readiness", "Status kesiapan dokumen per pengiriman",
              {"type": "object", "properties": {"shipment_id": {"type": "string"}}},
              lambda shipment_id=None: {"readiness": [
                  {"shipment_id": s["id_pengiriman"], "status": s["status_dokumen"], "score": s["skor_kesiapan"]}
                  for s in db.query("SELECT * FROM pengiriman") if shipment_id in (None, s["id_pengiriman"])]})

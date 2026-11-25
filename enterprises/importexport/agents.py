"""AI Agents ImportExport Corp (A_4) + repositori (skema berbahasa Indonesia).

- document_agent    : Document NLP (klasifikasi + ekstraksi) + rule engine kesiapan -> deklarasi impor ke Customs
- procurement_agent : kontrak trucking (Contract Net), evaluasi rencana pengiriman vs batas waktu PO,
                      negosiasi express berbasis utilitas (denda produksi vs surcharge)
"""
from __future__ import annotations

import csv
import json
import uuid
from pathlib import Path

from common import timeutil as tu
from common.agent_base import Agent
from common.config import DATA_DIR
from enterprises.importexport.doc_nlp import DocClassifier, check_readiness, extract_fields

SCHEMA = """
CREATE TABLE IF NOT EXISTS pesanan_pembelian(nomor_po TEXT PRIMARY KEY, pemasok TEXT, negara_asal TEXT, kode_hs TEXT,
  uraian_barang TEXT, jumlah INTEGER, satuan TEXT, nilai_usd REAL, gudang_tujuan TEXT, batas_waktu TEXT,
  denda_per_jam_juta REAL, klaim_fta TEXT, wajib_izin_impor INTEGER);
CREATE TABLE IF NOT EXISTS pengiriman(id_pengiriman TEXT PRIMARY KEY, nomor_po TEXT, nomor_bl TEXT, kapal_id TEXT,
  nama_kapal TEXT, jumlah_kontainer INTEGER, berat_kotor_kg REAL, eta_kapal TEXT, status_dokumen TEXT,
  skor_kesiapan REAL, isu_dokumen TEXT, nomor_aju TEXT, status_pabean TEXT, jalur TEXT, waktu_rilis TEXT,
  status_angkutan TEXT, eta_gudang_terakhir TEXT, terlambat_jam REAL DEFAULT 0, ekspres INTEGER DEFAULT 0,
  permintaan_ekspres INTEGER DEFAULT 0, siap_angkut_mulai TEXT, status_kontainer TEXT);
CREATE TABLE IF NOT EXISTS dokumen(id_dokumen TEXT PRIMARY KEY, id_pengiriman TEXT, nama_file TEXT, jenis_prediksi TEXT,
  keyakinan REAL, teks TEXT, hasil_ekstraksi TEXT, diterima_pada TEXT);
CREATE TABLE IF NOT EXISTS pemberitahuan_impor(nomor_aju TEXT PRIMARY KEY, id_pengiriman TEXT, dikirim_pada TEXT,
  status TEXT, catatan TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS kontrak_angkutan(id_kontrak TEXT PRIMARY KEY, id_permintaan TEXT, id_pengiriman TEXT,
  penyedia TEXT, jenis TEXT, tarif_per_trip REAL, biaya_tambahan_total REAL, status TEXT, dibuat_pada TEXT);
"""


class IERepo:
    def __init__(self, db):
        self.db = db
        db.script(SCHEMA)

    def seed(self, clf: DocClassifier) -> None:
        d = DATA_DIR / "importexport"
        with open(d / "purchase_orders.csv", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                self.db.execute("INSERT INTO pesanan_pembelian VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    r["nomor_po"], r["pemasok"], r["negara_asal"], r["kode_hs"], r["uraian_barang"], int(r["jumlah"]),
                    r["satuan"], float(r["nilai_usd"]), r["gudang_tujuan"], r["batas_waktu"],
                    float(r["denda_per_jam_juta"]), r["klaim_fta"], int(r["wajib_izin_impor"])))
        with open(d / "shipments.csv", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                self.db.execute("INSERT INTO pengiriman(id_pengiriman,nomor_po,nomor_bl,kapal_id,jumlah_kontainer,"
                                "berat_kotor_kg,status_dokumen,status_pabean,status_angkutan) VALUES (?,?,?,?,?,?,?,?,?)",
                                (r["id_pengiriman"], r["nomor_po"], r["nomor_bl"], r["kapal_id"],
                                 int(r["jumlah_kontainer"]), float(r["berat_kotor_kg"]), "BELUM_DIPERIKSA",
                                 "BELUM_DIAJUKAN", "BELUM_DIKONTRAK"))
        for f in sorted((d / "documents").glob("*.txt")):
            self.ingest(f, clf, "awal hari")

    def ingest(self, path: Path, clf: DocClassifier, when: str) -> dict:
        text = path.read_text(encoding="utf-8")
        jenis, conf = clf.predict(text)
        fields = extract_fields(text)
        sid = path.stem.split("_")[0]
        did = path.stem
        self.db.execute("INSERT OR REPLACE INTO dokumen VALUES (?,?,?,?,?,?,?,?)",
                        (did, sid, path.name, jenis, conf, text, json.dumps(fields), when))
        return {"id_dokumen": did, "jenis": jenis, "keyakinan": conf, "fields": fields}

    def shipment(self, sid: str) -> dict:
        return self.db.one("SELECT * FROM pengiriman WHERE id_pengiriman=?", (sid,))

    def po(self, nomor_po: str) -> dict:
        return self.db.one("SELECT * FROM pesanan_pembelian WHERE nomor_po=?", (nomor_po,))

    def docs(self, sid: str) -> list[dict]:
        rows = self.db.query("SELECT * FROM dokumen WHERE id_pengiriman=?", (sid,))
        return [{"jenis": r["jenis_prediksi"], "fields": json.loads(r["hasil_ekstraksi"]), "keyakinan": r["keyakinan"],
                 "nama_file": r["nama_file"]} for r in rows]


class DocumentAgent(Agent):
    name = "document_agent"
    role = "Document Intelligence Agent (NLP + Rule Engine)"
    description = "Mengklasifikasi & mengekstrak dokumen, memeriksa kelengkapan, mengajukan deklarasi impor (PIB)."
    skills = [{"id": "doc_classification", "name": "Klasifikasi dokumen", "description": "TF-IDF + Logistic Regression",
               "tags": ["nlp", "ml"]},
              {"id": "readiness_rules", "name": "Rule engine kesiapan", "description": "R1-R7 kelengkapan & konsistensi",
               "tags": ["rules", "compliance"]}]

    def __init__(self, ctx, repo: IERepo, clf: DocClassifier):
        self.repo, self.clf = repo, clf
        super().__init__(ctx)

    def evaluate(self, sid: str) -> dict:
        s = self.repo.shipment(sid)
        po = self.repo.po(s["nomor_po"])
        res = check_readiness(po, s, self.repo.docs(sid), s["nama_kapal"])
        self.repo.db.execute("UPDATE pengiriman SET status_dokumen=?, skor_kesiapan=?, isu_dokumen=? WHERE id_pengiriman=?",
                             (res["status"], res["score"], json.dumps(res["issues"] + res["warnings"]), sid))
        return res

    def on_arrival_notice(self, msg: dict) -> dict:
        self.goal = "Dokumen lengkap & deklarasi diajukan sebelum kapal tiba"
        touched_vessels, new_shipments = [], []
        for v in msg["payload"]["vessels"]:
            for bl in v["bl"]:
                self.repo.db.execute("UPDATE pengiriman SET kapal_id=?, nama_kapal=?, eta_kapal=? WHERE id_pengiriman=?",
                                     (v["vessel_id"], v["name"], tu.to_id(v["eta_h"]), bl["shipment_ref"]))
                new_shipments.append(bl["shipment_ref"])
            touched_vessels.append(v["vessel_id"])
        results = {sid: self.evaluate(sid) for sid in new_shipments}
        reasoning = []
        for sid, r in results.items():
            reasoning.append(f"{sid}: {r['status']} (skor {r['score']:.2f}); dokumen {r['present']}"
                             + (f"; isu: {r['issues']}" if r["issues"] else "") + (f"; peringatan: {r['warnings']}" if r["warnings"] else ""))
        self.decide("CHECK_DOCUMENTS", f"Arrival notice {len(touched_vessels)} kapal", reasoning,
                    f"{sum(r['status'] == 'COMPLETE' for r in results.values())}/{len(results)} shipment siap deklarasi")
        for sid, r in results.items():
            if r["status"] == "COMPLETE":
                self.submit_declaration(sid, r)
        self.send_doc_status(touched_vessels)
        self.ctx.agents["procurement_agent"].request_trucking(new_shipments)
        return {"checked": len(results)}

    def send_doc_status(self, vessel_ids: list[str]) -> None:
        out = []
        for vid in vessel_ids:
            rows = self.repo.db.query("SELECT id_pengiriman, status_dokumen FROM pengiriman WHERE kapal_id=?", (vid,))
            st = "COMPLETE" if all(r["status_dokumen"] == "COMPLETE" for r in rows) else "INCOMPLETE"
            out.append({"vessel_id": vid, "status": st,
                        "shipments": [{"shipment_id": r["id_pengiriman"], "status": r["status_dokumen"]} for r in rows]})
        self.send("smartport.berth_planning_agent", "INFORM", "document_status", {"vessels": out})

    def submit_declaration(self, sid: str, readiness: dict) -> None:
        s = self.repo.shipment(sid)
        if s["nomor_aju"]:
            return
        po = self.repo.po(s["nomor_po"])
        docs = {d["jenis"]: d["fields"] for d in self.repo.docs(sid)}
        aju = f"AJU-{sid}-{uuid.uuid4().hex[:4].upper()}"
        payload = {"declaration_no": aju, "shipment_ref": sid, "importer": "PT ImportExport Corp",
                   "importer_id": "NPWP-01.234.567.8-901.000", "hs_code": po["kode_hs"],
                   "description": po["uraian_barang"], "origin_country": po["negara_asal"], "quantity": po["jumlah"],
                   "unit": po["satuan"], "gross_weight_kg": s["berat_kotor_kg"], "customs_value_usd": po["nilai_usd"],
                   "containers": s["jumlah_kontainer"], "vessel_id": s["kapal_id"], "bl_no": s["nomor_bl"],
                   "has_permit": "IMPORT_PERMIT" in docs, "permit_no": docs.get("IMPORT_PERMIT", {}).get("permit_no"),
                   "fta_scheme": po["klaim_fta"] or None, "doc_consistency": readiness["consistency"],
                   "submitted_h": self.ctx.now()}
        self.repo.db.execute("INSERT INTO pemberitahuan_impor VALUES (?,?,?,?,?,?)",
                             (aju, sid, tu.to_id(self.ctx.now()), "TERKIRIM", "", json.dumps(payload)))
        self.repo.db.execute("UPDATE pengiriman SET nomor_aju=?, status_pabean='DIAJUKAN' WHERE id_pengiriman=?", (aju, sid))
        self.send("customs.risk_agent", "REQUEST", "customs_declaration", payload, reply_required=True)

    def on_documents_received(self, msg: dict) -> dict:
        p = msg["payload"]
        sid = p["shipment_id"]
        files = sorted((DATA_DIR / "importexport" / "documents_pending").glob(f"{sid}_*.txt"))
        got = [self.repo.ingest(f, self.clf, tu.to_id(self.ctx.now())) for f in files]
        r = self.evaluate(sid)
        self.decide("CHECK_DOCUMENTS", f"Dokumen susulan dari pemasok untuk {sid}: {[g['id_dokumen'] for g in got]}",
                    [f"{g['nama_file'] if 'nama_file' in g else g['id_dokumen']}: klasifikasi {g['jenis']} "
                     f"(p={g['keyakinan']}), ekstraksi {list(g['fields'])}" for g in got] +
                    [f"Hasil rule engine: {r['status']} skor {r['score']}, isu {r['issues'] or '-'}"],
                    "Ajukan deklarasi & perbarui status dokumen kapal" if r["status"] == "COMPLETE" else "Masih kurang")
        if r["status"] == "COMPLETE":
            self.submit_declaration(sid, r)
        self.send_doc_status([self.repo.shipment(sid)["kapal_id"]])
        return {"status": r["status"]}

    def on_declaration_received(self, msg: dict) -> dict:
        p = msg["payload"]
        self.repo.db.execute("UPDATE pengiriman SET status_pabean='DALAM_PEMERIKSAAN' WHERE id_pengiriman=?",
                             (p["shipment_ref"],))
        return {}

    def on_customs_release(self, msg: dict) -> dict:
        p = msg["payload"]
        status = {"RELEASED": "RILIS", "HOLD": "DITAHAN", "INSPECTION": "DITAHAN"}.get(p["status"], p["status"])
        self.repo.db.execute("UPDATE pengiriman SET status_pabean=?, jalur=?, waktu_rilis=? WHERE id_pengiriman=?",
                             (status, p.get("lane"), tu.to_id(p["release_h"]) if p.get("release_h") is not None else None,
                              p["shipment_ref"]))
        self.decide("UPDATE_CUSTOMS_STATUS", f"Bea Cukai: {p['shipment_ref']} {p['status']} (jalur {p.get('lane')})",
                    [p.get("note", "")], f"Status pabean {status}", share=False)
        return {}


class ProcurementAgent(Agent):
    name = "procurement_agent"
    role = "Procurement & Negotiation Agent"
    description = "Kontrak trucking, memantau ETA pengiriman vs batas waktu PO, negosiasi express berbasis utilitas."
    skills = [{"id": "trucking_contract", "name": "Kontrak trucking", "description": "Contract Net (initiator)",
               "tags": ["negotiation"]},
              {"id": "deadline_risk", "name": "Risiko keterlambatan PO", "description": "Utility-based express decision",
               "tags": ["decision"]}]

    def __init__(self, ctx, repo: IERepo):
        self.repo = repo
        super().__init__(ctx)

    @property
    def policy(self) -> dict:
        return self.ctx.governance["decision_policy"]

    def request_trucking(self, shipment_ids: list[str]) -> None:
        items = []
        for sid in shipment_ids:
            s = self.repo.shipment(sid)
            if s["status_angkutan"] != "BELUM_DIKONTRAK":
                continue
            po = self.repo.po(s["nomor_po"])
            share = self.ctx.strategy != "local"  # strategi lokal: informasi prioritas tidak dibagikan
            items.append({"shipment_id": sid, "containers": s["jumlah_kontainer"], "destination": po["gudang_tujuan"],
                          "deadline_h": tu.from_id(po["batas_waktu"]),
                          "priority_weight": po["denda_per_jam_juta"] if share else 0.0})
            self.repo.db.execute("UPDATE pengiriman SET status_angkutan='CFP_DIKIRIM' WHERE id_pengiriman=?", (sid,))
        if items:
            rid = f"REQ-{uuid.uuid4().hex[:6].upper()}"
            self.send("globalcargo.commercial_agent", "CFP", "trucking_request", {"request_id": rid, "shipments": items},
                      reply_required=True)

    def on_trucking_request(self, msg: dict) -> dict:
        p = msg["payload"]
        if msg["message_type"] != "PROPOSE":
            return {}
        acc = [o for o in p["offers"] if o["price_per_trip_jt"] <= self.policy["max_standard_trip_price_jt"]]
        for o in acc:
            self.repo.db.execute("INSERT OR REPLACE INTO kontrak_angkutan VALUES (?,?,?,?,?,?,?,?,?)", (
                f"KA-{o['shipment_id']}", p["request_id"], o["shipment_id"], "GlobalCargo Logistics", "STANDAR",
                o["price_per_trip_jt"], 0.0, "AKTIF", tu.to_id(self.ctx.now())))
            self.repo.db.execute("UPDATE pengiriman SET status_angkutan='DIKONTRAK' WHERE id_pengiriman=?",
                                 (o["shipment_id"],))
        self.decide("ACCEPT_TRUCKING", "Penawaran trucking GlobalCargo",
                    [f"{o['shipment_id']}: Rp{o['price_per_trip_jt']} jt/trip x {o['containers']}" for o in p["offers"]] +
                    [f"Batas harga kebijakan: Rp{self.policy['max_standard_trip_price_jt']} jt/trip"],
                    f"ACCEPT {len(acc)}/{len(p['offers'])} penawaran", conversation_id=msg.get("conversation_id"))
        self.reply(msg, "ACCEPT", {"request_id": p["request_id"], "accepted": [o["shipment_id"] for o in acc]})
        return {"accepted": len(acc)}

    def on_container_status(self, msg: dict) -> dict:
        for b in msg["payload"]["batches"]:
            self.repo.db.execute("UPDATE pengiriman SET siap_angkut_mulai=?, status_kontainer=? WHERE id_pengiriman=?", (
                tu.to_id(b["ready_from_h"]) if b["ready_from_h"] is not None else None, b["status"], b["shipment_id"]))
        return {}

    def on_delivery_plan(self, msg: dict) -> dict:
        p = msg["payload"]
        at_risk = []
        for s in p["shipments"]:
            self.repo.db.execute("UPDATE pengiriman SET status_angkutan=?, eta_gudang_terakhir=?, terlambat_jam=? "
                                 "WHERE id_pengiriman=?", (s["status"], tu.to_id(s["last_delivery_h"]) if s["last_delivery_h"]
                                                           else None, s["late_h"], s["shipment_id"]))
            row = self.repo.shipment(s["shipment_id"])
            if s["late_h"] > 0 and row["status_pabean"] == "RILIS" and not row["ekspres"] and not row["permintaan_ekspres"]:
                at_risk.append((s, row))
        if not at_risk:
            return {"at_risk": 0}
        if self.ctx.strategy != "collaborative" or not self.ctx.adaptive:
            self.decide("ASSESS_DELIVERY", "Rencana pengiriman menunjukkan keterlambatan",
                        [f"{s['shipment_id']}: telat {s['late_h']} j" for s, _ in at_risk],
                        f"Tidak ada negosiasi express (strategi {self.ctx.strategy})", share=False)
            return {"at_risk": len(at_risk)}
        items, reasoning = [], []
        for s, row in at_risk:
            po = self.repo.po(row["nomor_po"])
            penalty = po["denda_per_jam_juta"] * s["late_h"]
            max_sur = round(penalty / s["containers"] / self.policy["express_requires_benefit_ratio"], 2)
            items.append({"shipment_id": s["shipment_id"], "containers": s["containers"],
                          "late_containers": s["late_containers"], "late_h": s["late_h"], "deadline_h": s["deadline_h"],
                          "max_surcharge_per_container_jt": max_sur})
            reasoning.append(f"{s['shipment_id']}: telat {s['late_h']} j x denda Rp{po['denda_per_jam_juta']} jt/j = "
                             f"Rp{penalty:.1f} jt -> bersedia surcharge s.d. Rp{max_sur} jt/kontainer")
            self.repo.db.execute("UPDATE pengiriman SET permintaan_ekspres=1 WHERE id_pengiriman=?", (s["shipment_id"],))
        rid = f"EXP-{uuid.uuid4().hex[:6].upper()}"
        self.decide("REQUEST_EXPRESS", "Risiko denda produksi akibat keterlambatan", reasoning,
                    f"CFP express ke GlobalCargo untuk {[i['shipment_id'] for i in items]}")
        self.send("globalcargo.commercial_agent", "CFP", "express_request", {"request_id": rid, "shipments": items},
                  reply_required=True)
        return {"cfp": rid}

    def on_express_request(self, msg: dict) -> dict:
        p, mt = msg["payload"], msg["message_type"]
        if mt == "REFUSE":
            self.decide("EVALUATE_EXPRESS", "GlobalCargo menolak CFP express", [p.get("reason", "")],
                        "Terima risiko keterlambatan; informasikan produksi", share=True,
                        conversation_id=msg.get("conversation_id"))
            return {}
        if mt != "PROPOSE":
            return {}
        accepted, reasoning, human = [], [], False
        for pr in p["proposals"]:
            row = self.repo.shipment(pr["shipment_id"])
            po = self.repo.po(row["nomor_po"])
            benefit = po["denda_per_jam_juta"] * (pr["late_h_before"] - pr["late_h_after"])
            cost = pr["surcharge_total_jt"]
            ok = benefit >= self.policy["express_requires_benefit_ratio"] * cost
            human = human or cost > 50
            reasoning.append(f"{pr['shipment_id']}: manfaat (denda dihindari) Rp{benefit:.1f} jt vs surcharge Rp{cost} jt "
                             f"-> {'ACCEPT' if ok else 'REJECT'}")
            if ok:
                accepted.append(pr["shipment_id"])
                self.repo.db.execute("UPDATE pengiriman SET ekspres=1 WHERE id_pengiriman=?", (pr["shipment_id"],))
                self.repo.db.execute("INSERT OR REPLACE INTO kontrak_angkutan VALUES (?,?,?,?,?,?,?,?,?)", (
                    f"KX-{pr['shipment_id']}", p["request_id"], pr["shipment_id"], "GlobalCargo Logistics", "EKSPRES",
                    0.0, cost, "AKTIF", tu.to_id(self.ctx.now())))
        self.decide("EVALUATE_EXPRESS", "Proposal express GlobalCargo", reasoning,
                    f"ACCEPT express {accepted}" if accepted else "REJECT semua proposal", human_required=human,
                    conversation_id=msg.get("conversation_id"))
        if accepted:
            self.reply(msg, "ACCEPT", {"request_id": p["request_id"], "accepted": accepted})
        else:
            self.reply(msg, "REJECT", {"request_id": p["request_id"], "reason": "biaya > manfaat"})
        return {"accepted": accepted}

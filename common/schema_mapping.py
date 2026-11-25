"""Schema mapping: adapter dari format native tiap perusahaan ke model kanonik ekosistem.

Model kanonik (dipakai Control Center & pesan A2A):
  waktu    -> t_h (float, jam sejak T0 20/11/2026 00:00 WIB)
  id       -> snake_case
  uang     -> juta Rupiah (jt)
Konsep diadopsi dari pendekatan data-sharing lintas pelaku (mis. GS1 EPCIS event / IATA ONE Record):
setiap pihak tetap memakai sistemnya sendiri, interoperabilitas melalui kontrak data bersama.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from common import timeutil as tu

# Dokumentasi pemetaan field (ditampilkan di dashboard & laporan)
FIELD_MAP = {
    "oceanline": {
        "format": "JSON camelCase, waktu UTC 'Z', document store",
        "fields": {"vesselId": "vessel_id", "predictedEtaUtc": "eta_h (UTC->WIB->jam)",
                   "scheduledEtaUtc": "scheduled_eta_h", "delayHours": "delay_h", "loaM": "loa_m", "draftM": "draft_m",
                   "cargo.importContainers": "containers"}},
    "smartport": {
        "format": "JSON snake_case, waktu lokal 'YYYY-MM-DD HH:MM' (WIB), relasional",
        "fields": {"vessel_id": "vessel_id", "berth_id": "berth_id", "start_time": "start_h", "end_time": "end_h",
                   "eta": "eta_h", "wait_h": "wait_h", "status": "berth_status"}},
    "globalcargo": {
        "format": "Envelope {Status, Data}, PascalCase, Unix epoch",
        "fields": {"ShipmentRef": "shipment_id", "LastDeliveryEpoch": "last_delivery_h",
                   "FirstPickupEpoch": "first_pickup_h", "DeadlineEpoch": "deadline_h", "LateContainers": "late_containers",
                   "Status": "delivery_status"}},
    "importexport": {
        "format": "XML, tag berbahasa Indonesia, tanggal 'dd/mm/yyyy HH:MM'",
        "fields": {"id_pengiriman": "shipment_id", "kapal_id": "vessel_id", "status_dokumen": "doc_status",
                   "status_pabean": "customs_status (RILIS->RELEASED, DITAHAN->HOLD)", "eta_gudang_terakhir": "last_delivery_h",
                   "batas_waktu": "deadline_h", "terlambat_jam": "late_h", "ekspres": "express"}},
    "customs": {
        "format": "JSON, ISO-8601 +07:00, otorisasi ketat",
        "fields": {"shipment_ref": "shipment_id", "status": "customs_status", "final_lane": "lane",
                   "release_at": "release_h", "submitted_at": "submitted_h"}},
}

_IE_CUSTOMS = {"RILIS": "RELEASED", "DITAHAN": "HOLD", "DIAJUKAN": "SUBMITTED", "DALAM_PEMERIKSAAN": "UNDER_REVIEW",
               "BELUM_DIAJUKAN": "NOT_SUBMITTED"}


def _num(s):
    if s in (None, ""):
        return None
    try:
        return float(s)
    except ValueError:
        return s


def oceanline_fleet(doc: dict) -> list[dict]:
    return [{"vessel_id": v["vesselId"], "name": v["name"], "eta_h": tu.from_utc_z(v["predictedEtaUtc"]),
             "scheduled_eta_h": tu.from_utc_z(v["scheduledEtaUtc"]), "delay_h": v.get("delayHours") or 0.0,
             "p10": v.get("p10"), "p90": v.get("p90")} for v in doc.get("fleetEta", [])]


def smartport_plan(doc: dict) -> list[dict]:
    return [{"vessel_id": a["vessel_id"], "name": a["vessel_name"], "carrier": a["carrier"], "berth_id": a["berth_id"],
             "eta_h": tu.from_local(a["eta"]), "start_h": tu.from_local(a["start_time"]),
             "end_h": tu.from_local(a["end_time"]), "wait_h": a["wait_h"], "berth_status": a["status"]}
            for a in doc.get("assignments", [])]


def globalcargo_unwrap(doc: dict) -> dict:
    if doc.get("Status") != "OK":
        raise ValueError(f"GlobalCargo envelope error: {doc}")
    return doc["Data"]


def globalcargo_delivery(doc: dict) -> list[dict]:
    data = globalcargo_unwrap(doc)
    out = []
    for s in data.get("Shipments", []):
        out.append({"shipment_id": s["ShipmentRef"], "customer": s["Customer"], "containers": s["Containers"],
                    "first_pickup_h": tu.from_epoch(s["FirstPickupEpoch"]) if s["FirstPickupEpoch"] else None,
                    "last_delivery_h": tu.from_epoch(s["LastDeliveryEpoch"]) if s["LastDeliveryEpoch"] else None,
                    "deadline_h": tu.from_epoch(s["DeadlineEpoch"]), "late_containers": s["LateContainers"],
                    "waiting_release": s["WaitingRelease"], "express": s["Express"], "delivery_status": s["Status"]})
    return out


def xml_rows(xml_text: str) -> tuple[dict, list[dict]]:
    root = ET.fromstring(xml_text)
    rows = []
    for el in root:
        rows.append({child.tag: child.text for child in el})
    return dict(root.attrib), rows


def importexport_status(xml_text: str) -> list[dict]:
    _, rows = xml_rows(xml_text)
    out = []
    for r in rows:
        out.append({"shipment_id": r["id_pengiriman"], "vessel_id": r["kapal_id"],
                    "containers": int(float(r["jumlah_kontainer"])), "doc_status": r["status_dokumen"],
                    "doc_score": _num(r["skor_kesiapan"]),
                    "customs_status": _IE_CUSTOMS.get(r["status_pabean"], r["status_pabean"]), "lane": r["jalur"] or None,
                    "release_h": tu.from_id(r["waktu_rilis"]) if r.get("waktu_rilis") else None,
                    "delivery_status": r["status_angkutan"],
                    "last_delivery_h": tu.from_id(r["eta_gudang_terakhir"]) if r.get("eta_gudang_terakhir") else None,
                    "deadline_h": tu.from_id(r["batas_waktu"]), "late_h": _num(r["terlambat_jam"]) or 0.0,
                    "express": r["ekspres"] == "1"})
    return out


def importexport_kpi(xml_text: str) -> dict:
    _, rows = xml_rows(xml_text)
    r = rows[0] if rows else {}
    return {k: _num(v) for k, v in r.items()}


def customs_status(doc: dict) -> list[dict]:
    return [{"declaration_no": d.get("declaration_no"), "shipment_id": d["shipment_ref"], "customs_status": d["status"],
             "lane": d.get("final_lane"), "submitted_h": tu.from_iso(d["submitted_at"]) if d.get("submitted_at") else None,
             "release_h": tu.from_iso(d["release_at"]) if d.get("release_at") else None}
            for d in doc.get("declarations", [])]


def example_roundtrip() -> dict:
    """Contoh satu waktu (08:00 WIB) dalam lima format native."""
    h = 8.0
    return {"canonical_t_h": h, "oceanline": tu.to_utc_z(h), "smartport": tu.to_local(h), "globalcargo": tu.to_epoch(h),
            "importexport": tu.to_id(h), "customs": tu.to_iso(h)}


def pretty(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)

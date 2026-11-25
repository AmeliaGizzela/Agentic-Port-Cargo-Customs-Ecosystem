"""Generator data sintetis untuk seluruh perusahaan (seed tetap -> hasil dapat direproduksi).

Jalankan:  python -m data.generate_synthetic
Setiap perusahaan mendapatkan file dalam format/skema nativenya sendiri (heterogen):
  data/oceanline/*.json|csv     camelCase, UTC Z
  data/smartport/*.csv          snake_case, waktu lokal WIB
  data/globalcargo/*.csv        PascalCase, epoch
  data/importexport/*.csv|txt   kolom bahasa Indonesia, dd/mm/yyyy
  data/customs/*.csv            ISO +07:00, kode HS
"""
from __future__ import annotations

import csv
import json
import math
import random
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import timeutil as tu  # noqa: E402
from data import master_data as M  # noqa: E402
from data.doc_templates import render, training_corpus  # noqa: E402

DATA = ROOT / "data"
SEED = 42


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def handling_hours(containers: int, cranes: int, rate: float) -> float:
    return containers / (cranes * rate) + 1.0  # +1 jam sandar/lepas sandar


# ------------------------------------------------------------------ OceanLine
def eta_true_delay(dist, speed, wind, wave, current, congestion, teu, noise=0.0):
    """Fungsi 'dunia nyata' sintetis untuk keterlambatan kedatangan (jam)."""
    loss = 0.035 * max(0.0, wave - 1.5) ** 1.6 + 0.006 * max(0.0, wind - 18.0) + 0.000004 * max(0, teu - 4000)
    eff = max(6.0, speed * (1.0 - min(loss, 0.6)) + current)
    sail_delay = dist / eff - dist / speed
    port_delay = congestion * 1.5 + (0.8 if wave > 3.0 else 0.0)  # pembatasan pandu saat gelombang tinggi
    return sail_delay + port_delay + noise


def gen_voyage_history(n: int = 6000, seed: int = SEED) -> list[dict]:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        dist = float(rng.uniform(40, 650))
        speed = float(rng.uniform(12, 21))
        wind = float(np.clip(rng.gamma(2.2, 7.0), 0, 60))
        wave = float(np.clip(0.4 + wind * 0.075 + rng.normal(0, 0.45), 0.2, 7.0))
        current = float(rng.normal(0, 0.7))
        cong = float(rng.beta(2, 4))
        teu = int(rng.choice([2000, 2500, 4800, 5200, 6000, 6500, 8000]))
        delay = eta_true_delay(dist, speed, wind, wave, current, cong, teu, noise=float(rng.normal(0, 0.18)))
        rows.append({"voyageRef": f"HIST-{i:05d}", "distanceNm": round(dist, 1), "plannedSpeedKn": round(speed, 2),
                     "windKn": round(wind, 1), "waveM": round(wave, 2), "currentKn": round(current, 2),
                     "congestionIdx": round(cong, 3), "vesselTeu": teu, "delayHours": round(delay, 3)})
    return rows


def gen_oceanline() -> None:
    d = DATA / "oceanline"
    berth = {b["berth_id"]: b for b in M.BERTHS}
    fleet = []
    for v in M.VESSELS:
        b = berth[v["pref_berth"]]
        planned_dep = v["eta_h"] + handling_hours(v["containers"], b["cranes"], b["crane_rate"]) + 4.0
        dist0 = v["eta_h"] * v["speed_kn"]
        lat0, lon0 = M.PORT_LATLON
        br = math.radians(v["bearing"])
        lat = lat0 + math.cos(br) * dist0 / 60.0
        lon = lon0 + math.sin(br) * dist0 / 60.0
        fleet.append({
            "vesselId": v["vessel_id"], "imo": v["imo"], "name": v["name"], "operator": v["carrier"],
            "ownFleet": v["agent"], "loaM": v["loa_m"], "draftM": v["draft_m"], "capacityTeu": v["teu"],
            "voyage": {"voyageNo": v["voyage"], "from": v["origin_port"], "to": M.PORT_NAME,
                       "scheduledEtaUtc": tu.to_utc_z(v["eta_h"]), "plannedDepartureUtc": tu.to_utc_z(planned_dep),
                       "plannedSpeedKn": v["speed_kn"], "distanceRemainingNm": round(dist0, 1),
                       "preferredBerth": v["pref_berth"]},
            "position": {"lat": round(lat, 4), "lon": round(lon, 4), "reportedAtUtc": tu.to_utc_z(0.0)},
            "cargo": {"importContainers": v["containers"]},
            "status": "UNDERWAY",
        })
    d.mkdir(parents=True, exist_ok=True)
    (d / "fleet.json").write_text(json.dumps(fleet, indent=2), encoding="utf-8")
    manifests = []
    for s in M.SHIPMENTS:
        v = next(x for x in M.VESSELS if x["vessel_id"] == s["vessel_id"])
        if not v["agent"]:
            continue
        manifests.append({"vesselId": s["vessel_id"], "blNo": s["bl_no"], "consignee": s["consignee"],
                          "shipmentRef": s["shipment_id"], "hsCode": s["hs"], "cargoDescription": s["desc"],
                          "containers": s["containers"], "grossWeightKg": s["weight_kg"], "portOfLoading": v["origin_port"]})
    (d / "manifests.json").write_text(json.dumps(manifests, indent=2), encoding="utf-8")
    _write_csv(d / "voyage_history.csv", gen_voyage_history())


# ------------------------------------------------------------------ SmartPort
def gen_smartport() -> None:
    d = DATA / "smartport"
    _write_csv(d / "berths.csv", [dict(b) for b in M.BERTHS])
    berth = {b["berth_id"]: b for b in M.BERTHS}
    calls = []
    for v in M.VESSELS:
        b = berth[v["pref_berth"]]
        hh = handling_hours(v["containers"], b["cranes"], b["crane_rate"])
        calls.append({
            "call_id": f"CALL-{v['vessel_id']}", "vessel_id": v["vessel_id"], "vessel_name": v["name"],
            "carrier": v["carrier"], "is_partner_agent": int(v["agent"]), "loa_m": v["loa_m"], "draft_m": v["draft_m"],
            "containers": v["containers"], "eta_sched": tu.to_local(v["eta_h"]), "eta_current": tu.to_local(v["eta_h"]),
            "planned_departure": tu.to_local(v["eta_h"] + hh + 4.0), "doc_status": "UNKNOWN",
            "pref_berth": v["pref_berth"], "status": "EXPECTED",
        })
    _write_csv(d / "vessel_calls.csv", calls)
    batches = []
    for s in M.SHIPMENTS:
        pre_cleared = s["consignee"] != "PT ImportExport Corp"
        batches.append({"batch_id": f"BT-{s['shipment_id']}", "shipment_id": s["shipment_id"], "vessel_id": s["vessel_id"],
                        "consignee": s["consignee"], "trucker": s["trucker"], "containers": s["containers"],
                        "stow_from": s["stow"][0], "stow_to": s["stow"][1],
                        "customs_status": "RELEASED_MITA" if pre_cleared else "AWAITING",
                        "release_time": tu.to_local(0.0) if pre_cleared else "", "hold": 0})
    # muatan umum lain per kapal (tidak diangkut GlobalCargo) agar volume yard realistis
    for v in M.VESSELS:
        used = sum(s["containers"] for s in M.SHIPMENTS if s["vessel_id"] == v["vessel_id"])
        batches.append({"batch_id": f"BT-GEN-{v['vessel_id']}", "shipment_id": f"GEN-{v['vessel_id']}",
                        "vessel_id": v["vessel_id"], "consignee": "Various consignees", "trucker": "OTHER",
                        "containers": v["containers"] - used, "stow_from": 0.0, "stow_to": 1.0,
                        "customs_status": "RELEASED_MITA", "release_time": tu.to_local(0.0), "hold": 0})
    _write_csv(d / "container_batches.csv", batches)
    _write_csv(d / "yard_blocks.csv", [{"block_id": b, "capacity_teu": c, "used_teu": u}
                                       for b, c, u in [("YA", 2400, 1450), ("YB", 2000, 1120), ("YC", 1600, 610),
                                                       ("INSPECT", 120, 18)]])


# ---------------------------------------------------------------- GlobalCargo
def gen_globalcargo() -> None:
    d = DATA / "globalcargo"
    _write_csv(d / "road_nodes.csv", [{"NodeId": n, "Name": nm, "Lat": la, "Lon": lo, "Kind": k}
                                      for n, nm, la, lo, k in M.ROAD_NODES])
    _write_csv(d / "road_edges.csv", [{"EdgeId": i + 1, "FromNode": a, "ToNode": b, "Minutes": m, "RoadType": t,
                                       "Status": "OPEN", "CongestionFactor": 1.0}
                                      for i, (a, b, m, t) in enumerate(M.ROAD_EDGES)])
    rng = random.Random(SEED)
    drivers = ["Budi", "Agus", "Slamet", "Joko", "Dedi", "Rudi", "Hendra", "Yanto", "Eko", "Wawan", "Tono", "Asep"]
    _write_csv(d / "trucks.csv", [{"TruckId": f"T{i + 1:02d}", "PlateNo": f"B {9100 + rng.randint(0, 899)} UIX",
                                   "Type": "OWN", "Status": "AVAILABLE", "HomeNode": "DEPOT",
                                   "AvailableFromEpoch": tu.to_epoch(5.0), "CostPerTrip": M.COSTS["truck_trip_cost_jt"],
                                   "DriverName": drivers[i]} for i in range(M.TRUCK_COUNT)])
    contracts = []
    for s in M.SHIPMENTS:
        if s["consignee"] != "PT ImportExport Corp":
            contracts.append({"ContractId": f"CT-{s['shipment_id']}", "Customer": s["consignee"],
                              "ShipmentRef": s["shipment_id"], "DestinationNode": s["dest"],
                              "DeadlineEpoch": tu.to_epoch(s["deadline_h"]), "Containers": s["containers"],
                              "Type": "STANDARD", "PricePerTrip": M.TRIP_PRICE_JT[s["dest"]], "Surcharge": 0.0,
                              "PenaltyRate": s["penalty_jt_per_h"], "Status": "ACTIVE"})
    _write_csv(d / "contracts.csv", contracts)


# --------------------------------------------------------------- ImportExport
def _doc_fields(s: dict) -> dict:
    v = next(x for x in M.VESSELS if x["vessel_id"] == s["vessel_id"])
    return {"inv_no": f"INV-{s['po'][-4:]}-{s['shipment_id'][-3:]}", "supplier": s["supplier"],
            "consignee": s["consignee"], "hs": s["hs"], "desc": s["desc"], "qty": s["qty"], "unit": s["unit"],
            "value_usd": float(s["value_usd"]), "weight_kg": s["weight_kg"], "containers": s["containers"],
            "vessel": v["name"], "voyage": v["voyage"], "bl_no": s["bl_no"], "origin": s["origin"],
            "pol": v["origin_port"], "fta": s["fta"], "permit_no": f"PI-{s['hs'][:2]}.{s['po'][-4:]}/2026",
            "date": "28/10/2026"}


def gen_importexport() -> None:
    d = DATA / "importexport"
    ie = [s for s in M.SHIPMENTS if s["consignee"] == "PT ImportExport Corp"]
    hs_restricted = {h for h, _, _, r, _ in M.HS_REFERENCE if r}
    _write_csv(d / "purchase_orders.csv", [{
        "nomor_po": s["po"], "pemasok": s["supplier"], "negara_asal": s["origin"], "kode_hs": s["hs"],
        "uraian_barang": s["desc"], "jumlah": s["qty"], "satuan": s["unit"], "nilai_usd": s["value_usd"],
        "gudang_tujuan": s["dest"], "batas_waktu": tu.to_id(s["deadline_h"]), "denda_per_jam_juta": s["penalty_jt_per_h"],
        "klaim_fta": s["fta"] or "", "wajib_izin_impor": int(s["hs"] in hs_restricted)} for s in ie])
    _write_csv(d / "shipments.csv", [{
        "id_pengiriman": s["shipment_id"], "nomor_po": s["po"], "nomor_bl": s["bl_no"], "kapal_id": s["vessel_id"],
        "jumlah_kontainer": s["containers"], "berat_kotor_kg": s["weight_kg"]} for s in ie])
    rng = random.Random(SEED)
    for sub in ("documents", "documents_pending"):
        (d / sub).mkdir(parents=True, exist_ok=True)
        for p in (d / sub).glob("*.txt"):
            p.unlink()
    for s in ie:
        f = _doc_fields(s)
        for dt in M.DOCS_AVAILABLE[s["shipment_id"]]:
            ff = dict(f)
            if s["shipment_id"] == "SHP-005" and dt == "PACKING_LIST":
                ff["weight_kg"] = int(s["weight_kg"] * 1.008)  # selisih kecil -> peringatan (bukan blocking)
            (d / "documents" / f"{s['shipment_id']}_{dt}.txt").write_text(render(dt, ff, rng), encoding="utf-8")
        for dt in M.DOCS_PENDING.get(s["shipment_id"], []):
            (d / "documents_pending" / f"{s['shipment_id']}_{dt}.txt").write_text(render(dt, f, rng), encoding="utf-8")
    _write_csv(d / "doc_training.csv", [{"teks": t, "jenis": y} for t, y in training_corpus()])


# --------------------------------------------------------------------- Customs
def gen_customs(n: int = 3000, seed: int = SEED) -> None:
    d = DATA / "customs"
    _write_csv(d / "hs_reference.csv", [{"hs_code": h, "description": ds, "ref_unit_value_usd_per_kg": r,
                                         "restricted": rs, "tariff_pct": t} for h, ds, r, rs, t in M.HS_REFERENCE])
    _write_csv(d / "origin_risk.csv", [{"country": c, "risk_index": r} for c, r in M.ORIGIN_RISK.items()])
    rng = np.random.default_rng(seed)
    countries = list(M.ORIGIN_RISK.keys())
    rows = []
    for i in range(n):
        hs, desc, ref, restricted, _t = M.HS_REFERENCE[int(rng.integers(0, len(M.HS_REFERENCE)))]
        origin = countries[int(rng.integers(0, len(countries)))]
        importer_score = float(np.clip(rng.beta(6, 2), 0.05, 1.0))
        containers = int(rng.integers(1, 12))
        weight = float(containers * rng.uniform(8000, 22000))
        risk = M.ORIGIN_RISK[origin]
        p_violate = 0.03 + 0.12 * risk + 0.10 * (1 - importer_score)
        violation = rng.random() < p_violate
        has_permit = 1
        doc_consistency = float(np.clip(rng.normal(0.97, 0.03), 0.6, 1.0))
        if violation:
            kind = rng.choice(["undervalue", "no_permit", "misdeclare"], p=[0.6, 0.2, 0.2])
            ratio = float(rng.uniform(0.12, 0.55)) if kind == "undervalue" else float(rng.lognormal(0, 0.15))
            if kind == "no_permit" and restricted:
                has_permit = 0
            if kind == "misdeclare":
                weight *= float(rng.uniform(1.4, 2.2))
                doc_consistency = float(rng.uniform(0.6, 0.85))
        else:
            ratio = float(rng.lognormal(0, 0.15))
        value = ref * weight * ratio
        rows.append({"nomor_aju": f"HIST-{i:05d}", "hs_code": hs, "origin_country": origin,
                     "gross_weight_kg": round(weight, 1), "containers": containers,
                     "customs_value_usd": round(value, 2), "importer_score": round(importer_score, 3),
                     "doc_consistency": round(doc_consistency, 3), "restricted": restricted,
                     "has_permit": has_permit if restricted else 1, "violation": int(violation)})
    _write_csv(d / "declaration_history.csv", rows)


def main() -> None:
    gen_oceanline()
    gen_smartport()
    gen_globalcargo()
    gen_importexport()
    gen_customs()
    print("Data sintetis dibuat di", DATA)


if __name__ == "__main__":
    main()

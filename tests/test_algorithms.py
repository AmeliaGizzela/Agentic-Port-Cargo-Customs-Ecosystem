"""Unit test model AI, optimasi, dan schema mapping (tidak membutuhkan server berjalan).

Jalankan: python -m pytest tests -q
"""
import csv
import math

import pytest

from common import schema_mapping as sm
from common import timeutil as tu
from common.config import DATA_DIR
from data import master_data as M


# ------------------------------------------------------------------ waktu
def test_time_formats_roundtrip():
    for h in (0.0, 8.0, 13.5, 30.25):
        assert tu.from_utc_z(tu.to_utc_z(h)) == pytest.approx(h)
        assert tu.from_local(tu.to_local(h)) == pytest.approx(h)
        assert tu.from_epoch(tu.to_epoch(h)) == pytest.approx(h)
        assert tu.from_id(tu.to_id(h)) == pytest.approx(h)
        assert tu.from_iso(tu.to_iso(h)) == pytest.approx(h)
    assert tu.to_utc_z(8.0) == "2026-11-20T01:00:00Z"  # 08:00 WIB = 01:00 UTC


# --------------------------------------------------------------- berth MIP
def _vessels():
    from data.generate_synthetic import handling_hours
    berth = {b["berth_id"]: b for b in M.BERTHS}
    out = []
    for v in M.VESSELS:
        b = berth[v["pref_berth"]]
        out.append(dict(vessel_id=v["vessel_id"], eta_h=v["eta_h"], containers=v["containers"], loa_m=v["loa_m"],
                        draft_m=v["draft_m"], is_partner_agent=v["agent"], doc_status="COMPLETE",
                        planned_departure_h=v["eta_h"] + handling_hours(v["containers"], b["cranes"], b["crane_rate"]) + 4))
    return out


def test_mip_feasible_no_overlap_and_compatible():
    from enterprises.smartport import berth_mip as bm
    vs, bs = _vessels(), [dict(b) for b in M.BERTHS]
    res = bm.solve(vs, bs, 0.0, "global")
    assert res["status"] == "optimal" and len(res["plan"]) == len(vs)
    bmap, vmap = {b["berth_id"]: b for b in bs}, {v["vessel_id"]: v for v in vs}
    for p in res["plan"]:
        assert bm.compatible(vmap[p["vessel_id"]], bmap[p["berth_id"]])
        assert p["start_h"] >= vmap[p["vessel_id"]]["eta_h"] - 1e-9
    for b in bs:
        seq = sorted((p for p in res["plan"] if p["berth_id"] == b["berth_id"]), key=lambda p: p["start_h"])
        for a, c in zip(seq, seq[1:]):
            assert a["end_h"] <= c["start_h"] + 1e-9, "dua kapal tumpang tindih di dermaga yang sama"


def test_adaptive_beats_static_after_delay():
    from enterprises.smartport import berth_mip as bm
    vs, bs = _vessels(), [dict(b) for b in M.BERTHS]
    base = bm.solve(vs, bs, 0.0, "global")
    prev = {p["vessel_id"]: p for p in base["plan"]}
    vs2 = [dict(v) for v in vs]
    next(v for v in vs2 if v["vessel_id"] == "V001")["eta_h"] = 12.0
    adaptive = bm.plan_metrics(bm.solve(vs2, bs, 1.0, "global", prev)["plan"], vs2, bs)
    static = bm.plan_metrics(bm.fcfs_shift(vs2, bs, prev, 1.0)["plan"], vs2, bs)
    assert adaptive["avg_wait_h"] <= static["avg_wait_h"]
    assert adaptive["max_wait_h"] < static["max_wait_h"]


def test_crane_degradation_extends_processing():
    from enterprises.smartport import berth_mip as bm
    b = dict(M.BERTHS[0])
    normal = bm.processing_hours(400, b, 8.0)
    b["degradations"] = [{"from_h": 9.0, "to_h": 22.0, "factor": 0.34}]
    assert bm.processing_hours(400, b, 8.0) > normal * 1.8


# ---------------------------------------------------------------- VRP
def _graph(closed=False):
    from enterprises.globalcargo import vrp
    nodes = list(csv.DictReader(open(DATA_DIR / "globalcargo" / "road_nodes.csv", encoding="utf-8")))
    edges = list(csv.DictReader(open(DATA_DIR / "globalcargo" / "road_edges.csv", encoding="utf-8")))
    for e in edges:
        e["CongestionFactor"] = float(e["CongestionFactor"])
        if closed and {e["FromNode"], e["ToNode"]} == {"BEKASI_X", "CIKARANG_X"}:
            e["Status"] = "CLOSED"
    return vrp.build_graph(nodes, edges)


def test_dijkstra_reroutes_when_road_closed():
    from enterprises.globalcargo import vrp
    m0, p0 = vrp.shortest(_graph(), "PORT", "WH_CIKARANG")
    m1, p1 = vrp.shortest(_graph(closed=True), "PORT", "WH_CIKARANG")
    assert m0 == pytest.approx(84.0)
    assert m1 > m0 and "ARTERI_KALIMALANG" in p1


def test_vrp_respects_ready_times_and_one_job_per_truck_at_a_time():
    from enterprises.globalcargo import vrp
    G = _graph()
    tt = vrp.travel_table(G, {"WH_CIKARANG", "WH_KARAWANG"})
    jobs = [{"job_id": f"J{i:02d}", "shipment": "S1" if i < 6 else "S2", "dest": "WH_CIKARANG" if i < 6 else "WH_KARAWANG",
             "ready_h": 8 + 0.25 * i, "deadline_h": 14.0, "priority_weight": 10.0} for i in range(12)]
    trucks = [{"truck_id": f"T{i}", "type": "OWN", "available_from_h": 5.0, "start_node": "DEPOT"} for i in range(4)]
    res = vrp.Planner(jobs, trucks, tt, "collaborative", 0.0).solve()
    assert len(res["trips"]) == 12
    by_truck = {}
    for jid, x in res["trips"].items():
        j = next(j for j in jobs if j["job_id"] == jid)
        assert x["pickup_h"] >= j["ready_h"] - 1e-9
        by_truck.setdefault(x["truck_id"], []).append(x)
    for trips in by_truck.values():
        trips.sort(key=lambda x: x["pickup_h"])
        for a, b in zip(trips, trips[1:]):
            assert b["pickup_h"] >= a["done_h"] - 1e-9


# ---------------------------------------------------------------- ML models
def test_eta_model_beats_schedule_baseline(tmp_path):
    from enterprises.oceanline.eta_model import EtaModel
    m = EtaModel(tmp_path, DATA_DIR / "oceanline" / "voyage_history.csv")
    met = m.train()
    assert met["mae_h"] < 0.5 * met["baseline_schedule_mae_h"]
    assert met["r2"] > 0.9
    storm = m.predict(dict(distanceNm=126, plannedSpeedKn=18, windKn=35, waveM=3.6, currentKn=-0.8, congestionIdx=0.3,
                           vesselTeu=6500))
    assert 3.5 <= storm["delayHours"] <= 4.5  # skenario studi kasus: V001 terlambat ±4 jam


def test_document_classifier_and_rules(tmp_path):
    from enterprises.importexport.doc_nlp import DocClassifier, check_readiness, extract_fields
    clf = DocClassifier(tmp_path, DATA_DIR / "importexport" / "doc_training.csv")
    assert clf.train()["accuracy"] > 0.9
    text = (DATA_DIR / "importexport" / "documents" / "SHP-001_INVOICE.txt").read_text(encoding="utf-8")
    assert clf.predict(text)[0] == "INVOICE"
    f = extract_fields(text)
    assert f["hs_code"] == "8542.31" and f["total_value_usd"] == 480000.0
    po = {"kode_hs": "2915.12", "nilai_usd": 95000, "klaim_fta": "AIFTA", "wajib_izin_impor": 1}
    docs = [{"jenis": "INVOICE", "fields": {"hs_code": "2915.12", "total_value_usd": 95000.0, "quantity": 1}}]
    res = check_readiness(po, {}, docs, None)
    assert res["status"] == "INCOMPLETE" and "IMPORT_PERMIT" in res["missing"] and "CERT_ORIGIN" in res["missing"]


def test_customs_risk_flags_undervaluation(tmp_path):
    from enterprises.customs.risk_model import RiskModel
    ref = {h: {"ref_unit_value_usd_per_kg": r, "restricted": rs, "tariff_pct": t} for h, _, r, rs, t in M.HS_REFERENCE}
    m = RiskModel(tmp_path, DATA_DIR / "customs" / "declaration_history.csv", ref, M.ORIGIN_RISK)
    met = m.train()
    assert met["roc_auc_anomaly_score"] > 0.8
    base = dict(hs_code="8517.13", origin_country="China", gross_weight_kg=16000, containers=4, has_permit=True,
                doc_consistency=1.0, importer_score=0.85)
    assert m.assess({**base, "customs_value_usd": 210000})["recommended_lane"] == "RED"      # 29% dari referensi
    assert m.assess({**base, "customs_value_usd": 720000})["recommended_lane"] != "RED"
    no_permit = m.assess({**base, "customs_value_usd": 720000, "has_permit": False})
    assert no_permit["recommended_lane"] == "RED"


# ------------------------------------------------------------ schema mapping
def test_importexport_xml_mapping():
    xml = ("<?xml version='1.0' encoding='utf-8'?><StatusPengiriman><Pengiriman><id_pengiriman>SHP-001</id_pengiriman>"
           "<kapal_id>V001</kapal_id><jumlah_kontainer>8</jumlah_kontainer><status_dokumen>COMPLETE</status_dokumen>"
           "<skor_kesiapan>1.0</skor_kesiapan><status_pabean>RILIS</status_pabean><jalur>GREEN</jalur>"
           "<waktu_rilis>20/11/2026 00:30</waktu_rilis><status_angkutan>ON_TIME</status_angkutan>"
           "<eta_gudang_terakhir>20/11/2026 12:00</eta_gudang_terakhir><terlambat_jam>0</terlambat_jam><ekspres>0</ekspres>"
           "<batas_waktu>20/11/2026 20:00</batas_waktu></Pengiriman></StatusPengiriman>")
    row = sm.importexport_status(xml)[0]
    assert row["customs_status"] == "RELEASED" and row["release_h"] == pytest.approx(0.5)
    assert row["deadline_h"] == pytest.approx(20.0) and row["containers"] == 8


def test_globalcargo_envelope_mapping():
    doc = {"Status": "OK", "Data": {"Shipments": [{"ShipmentRef": "SHP-001", "Customer": "X", "Containers": 2,
                                                   "FirstPickupEpoch": tu.to_epoch(9.0), "LastDeliveryEpoch": tu.to_epoch(11.5),
                                                   "DeadlineEpoch": tu.to_epoch(20.0), "LateContainers": 0,
                                                   "WaitingRelease": 0, "Express": False, "Status": "ON_TIME"}]}}
    row = sm.globalcargo_delivery(doc)[0]
    assert row["last_delivery_h"] == pytest.approx(11.5) and not math.isnan(row["first_pickup_h"])


# ---------------------------------------------------------------------------
# Umpan AIS nyata (AISStream.io) - parser pesan, tanpa jaringan
# ---------------------------------------------------------------------------
def test_ais_parse_position_report():
    from enterprises.oceanline.ais_feed import AisFeed, parse_position
    msg = {"MessageType": "PositionReport",
           "MetaData": {"MMSI": 525001234, "ShipName": "KM NUSANTARA  ", "latitude": -6.02, "longitude": 106.9,
                        "time_utc": "2026-11-25 03:00:00.123 +0000 UTC"},
           "Message": {"PositionReport": {"Sog": 11.5, "Cog": 270.0, "TrueHeading": 268, "NavigationalStatus": 0}}}
    doc = parse_position(msg)
    assert doc["mmsi"] == "525001234" and doc["shipName"] == "KM NUSANTARA"
    assert doc["lat"] == -6.02 and doc["speedKn"] == 11.5 and doc["courseDeg"] == 270.0
    assert parse_position({"MessageType": "ShipStaticData"}) is None
    assert parse_position({"MessageType": "PositionReport", "MetaData": {}}) is None
    feed = AisFeed()
    snap = feed.snapshot()
    assert snap["status"] == "disabled" and snap["count"] == 0 and "boundingBox" in snap

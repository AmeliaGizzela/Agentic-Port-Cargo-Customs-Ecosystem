"""Master data skenario (sintetis, fiktif). Generator menulis data ini ke file native tiap perusahaan.

Satuan waktu di sini: jam sejak T0 = 20/11/2026 00:00 WIB.
"""

PORT_NAME = "Pelabuhan Nusa Raya (fiktif)"
PORT_LATLON = (-6.10, 106.88)

BERTHS = [
    # berth_id, panjang (m), draft maks (m), jumlah crane, produktivitas (moves/jam/crane)
    {"berth_id": "B1", "length_m": 350, "max_draft_m": 14.5, "cranes": 3, "crane_rate": 25},
    {"berth_id": "B2", "length_m": 300, "max_draft_m": 13.0, "cranes": 3, "crane_rate": 25},
    {"berth_id": "B3", "length_m": 220, "max_draft_m": 11.0, "cranes": 2, "crane_rate": 22},
]

VESSELS = [
    {"vessel_id": "V001", "name": "MV Ocean Pioneer", "imo": "IMO9700011", "carrier": "OceanLine Shipping",
     "agent": True, "containers": 400, "loa_m": 300, "draft_m": 13.5, "eta_h": 8.0, "pref_berth": "B1",
     "speed_kn": 18.0, "teu": 6500, "origin_port": "Shenzhen", "voyage": "026E", "bearing": 0},
    {"vessel_id": "V002", "name": "MV Ocean Mercury", "imo": "IMO9700022", "carrier": "OceanLine Shipping",
     "agent": True, "containers": 250, "loa_m": 260, "draft_m": 12.0, "eta_h": 9.0, "pref_berth": "B1",
     "speed_kn": 17.0, "teu": 4800, "origin_port": "Chennai", "voyage": "114W", "bearing": 265},
    {"vessel_id": "V003", "name": "MV Ocean Kartika", "imo": "IMO9700033", "carrier": "OceanLine Shipping",
     "agent": True, "containers": 300, "loa_m": 280, "draft_m": 12.5, "eta_h": 11.0, "pref_berth": "B2",
     "speed_kn": 16.0, "teu": 5200, "origin_port": "Ho Chi Minh City", "voyage": "087S", "bearing": 20},
    {"vessel_id": "V004", "name": "MV Selat Biru", "imo": "IMO9611044", "carrier": "PT Pelayaran Mitra",
     "agent": False, "containers": 180, "loa_m": 200, "draft_m": 10.5, "eta_h": 6.0, "pref_berth": "B3",
     "speed_kn": 14.0, "teu": 2500, "origin_port": "Singapore", "voyage": "S12", "bearing": 315},
    {"vessel_id": "V005", "name": "MV Ocean Rinjani", "imo": "IMO9700055", "carrier": "OceanLine Shipping",
     "agent": True, "containers": 350, "loa_m": 290, "draft_m": 13.0, "eta_h": 14.0, "pref_berth": "B2",
     "speed_kn": 17.0, "teu": 6000, "origin_port": "Busan", "voyage": "045S", "bearing": 45},
    {"vessel_id": "V006", "name": "MV Bahari Lestari", "imo": "IMO9522066", "carrier": "Samudera Kargo Nusantara",
     "agent": False, "containers": 150, "loa_m": 190, "draft_m": 10.0, "eta_h": 16.0, "pref_berth": "B3",
     "speed_kn": 13.0, "teu": 2000, "origin_port": "Port Klang", "voyage": "K77", "bearing": 30},
]

# Shipment (Bill of Lading) yang relevan untuk rantai importir & trucking.
# stow = posisi relatif urutan bongkar di kapal (0 = dibongkar pertama).
SHIPMENTS = [
    {"shipment_id": "SHP-001", "bl_no": "OLSZ260451", "vessel_id": "V001", "consignee": "PT ImportExport Corp",
     "trucker": "GlobalCargo", "containers": 8, "dest": "WH_CIKARANG", "deadline_h": 20.0, "po": "PO-2026-0451",
     "hs": "8542.31", "desc": "Integrated circuits (microcontroller units)", "origin": "China",
     "supplier": "Shenzhen Microtech Co., Ltd", "value_usd": 480000, "weight_kg": 64000, "qty": 120000,
     "unit": "pcs", "penalty_jt_per_h": 25, "stow": (0.0, 0.3), "fta": "ACFTA Form E"},
    {"shipment_id": "SHP-002", "bl_no": "OLSZ260452", "vessel_id": "V001", "consignee": "PT ImportExport Corp",
     "trucker": "GlobalCargo", "containers": 6, "dest": "WH_KARAWANG", "deadline_h": 36.0, "po": "PO-2026-0452",
     "hs": "8483.40", "desc": "Gear boxes and transmission parts", "origin": "Japan",
     "supplier": "Nagoya Precision Gear K.K.", "value_usd": 210000, "weight_kg": 96000, "qty": 1200,
     "unit": "units", "penalty_jt_per_h": 6, "stow": (0.5, 0.7), "fta": "IJEPA"},
    {"shipment_id": "SHP-003", "bl_no": "OLCH260460", "vessel_id": "V002", "consignee": "PT ImportExport Corp",
     "trucker": "GlobalCargo", "containers": 5, "dest": "WH_KARAWANG", "deadline_h": 42.0, "po": "PO-2026-0460",
     "hs": "2915.12", "desc": "Salts of formic acid (sodium formate) - industrial chemical", "origin": "India",
     "supplier": "Gujarat Chemicals Pvt Ltd", "value_usd": 95000, "weight_kg": 110000, "qty": 110000,
     "unit": "kg", "penalty_jt_per_h": 4, "stow": (0.2, 0.5), "fta": "AIFTA"},
    {"shipment_id": "SHP-004", "bl_no": "OLHC260463", "vessel_id": "V003", "consignee": "PT ImportExport Corp",
     "trucker": "GlobalCargo", "containers": 7, "dest": "WH_CIKARANG", "deadline_h": 30.0, "po": "PO-2026-0463",
     "hs": "5208.52", "desc": "Printed plain-weave cotton fabric", "origin": "Vietnam",
     "supplier": "Saigon Textile JSC", "value_usd": 150000, "weight_kg": 120000, "qty": 98000,
     "unit": "m", "penalty_jt_per_h": 5, "stow": (0.1, 0.4), "fta": "ATIGA"},
    {"shipment_id": "SHP-005", "bl_no": "OLBS260470", "vessel_id": "V005", "consignee": "PT ImportExport Corp",
     "trucker": "GlobalCargo", "containers": 6, "dest": "WH_KARAWANG", "deadline_h": 44.0, "po": "PO-2026-0470",
     "hs": "7208.51", "desc": "Hot-rolled flat steel plates", "origin": "South Korea",
     "supplier": "Pohang Steel Trading Co.", "value_usd": 260000, "weight_kg": 150000, "qty": 150,
     "unit": "plates", "penalty_jt_per_h": 6, "stow": (0.6, 0.9), "fta": "AKFTA"},
    {"shipment_id": "SHP-006", "bl_no": "OLHC260471", "vessel_id": "V003", "consignee": "PT ImportExport Corp",
     "trucker": "GlobalCargo", "containers": 4, "dest": "WH_CIKARANG", "deadline_h": 34.0, "po": "PO-2026-0471",
     "hs": "8517.13", "desc": "Smartphones (cellular handsets)", "origin": "China",
     "supplier": "Dongguan Mobile Tech Ltd", "value_usd": 210000, "weight_kg": 16000, "qty": 16000,
     "unit": "pcs", "penalty_jt_per_h": 10, "stow": (0.5, 0.65), "fta": None},
    # konsinyi lain milik pelanggan GlobalCargo (bukan ImportExport) - sudah pre-cleared (jalur MITA)
    {"shipment_id": "OTH-001", "bl_no": "PMSG2611", "vessel_id": "V004", "consignee": "PT Mitra Retail",
     "trucker": "GlobalCargo", "containers": 5, "dest": "WH_BEKASI", "deadline_h": 20.0, "po": None,
     "hs": "9503.00", "desc": "Toys", "origin": "Singapore", "supplier": "-", "value_usd": 60000,
     "weight_kg": 40000, "qty": 5000, "unit": "ctn", "penalty_jt_per_h": 2, "stow": (0.2, 0.5), "fta": None},
    {"shipment_id": "OTH-002", "bl_no": "SKPK2677", "vessel_id": "V006", "consignee": "CV Sunter Jaya",
     "trucker": "GlobalCargo", "containers": 5, "dest": "WH_SUNTER", "deadline_h": 30.0, "po": None,
     "hs": "3901.10", "desc": "Polyethylene granules", "origin": "Malaysia", "supplier": "-", "value_usd": 70000,
     "weight_kg": 100000, "qty": 100000, "unit": "kg", "penalty_jt_per_h": 2, "stow": (0.1, 0.4), "fta": None},
]

# Dokumen yang sudah tersedia saat awal hari; SHP-003 kekurangan izin impor & COO.
DOCS_AVAILABLE = {
    "SHP-001": ["INVOICE", "PACKING_LIST", "BILL_OF_LADING", "CERT_ORIGIN"],
    "SHP-002": ["INVOICE", "PACKING_LIST", "BILL_OF_LADING", "CERT_ORIGIN"],
    "SHP-003": ["INVOICE", "PACKING_LIST", "BILL_OF_LADING"],
    "SHP-004": ["INVOICE", "PACKING_LIST", "BILL_OF_LADING", "CERT_ORIGIN", "INSURANCE"],
    "SHP-005": ["INVOICE", "PACKING_LIST", "BILL_OF_LADING", "CERT_ORIGIN", "IMPORT_PERMIT"],
    "SHP-006": ["INVOICE", "PACKING_LIST", "BILL_OF_LADING", "IMPORT_PERMIT"],
}
DOCS_PENDING = {"SHP-003": ["IMPORT_PERMIT", "CERT_ORIGIN"]}

HS_REFERENCE = [
    # hs, uraian, nilai referensi USD/kg, restricted (lartas), tarif bea masuk %
    ("8542.31", "Electronic integrated circuits", 7.5, 0, 0),
    ("8483.40", "Gears and gear boxes", 2.2, 0, 5),
    ("2915.12", "Salts of formic acid", 0.9, 1, 5),
    ("5208.52", "Printed cotton woven fabric", 1.3, 0, 10),
    ("7208.51", "Hot-rolled flat steel", 1.7, 1, 12.5),
    ("8517.13", "Smartphones", 45.0, 1, 10),
    ("8471.30", "Portable computers", 35.0, 0, 0),
    ("3004.90", "Medicaments", 20.0, 1, 5),
    ("0402.21", "Milk powder", 3.5, 1, 5),
    ("8708.29", "Motor vehicle body parts", 4.0, 0, 10),
    ("3901.10", "Polyethylene", 1.2, 0, 5),
    ("6403.99", "Footwear", 12.0, 0, 25),
    ("9503.00", "Toys", 6.0, 0, 10),
    ("8703.23", "Passenger motor cars", 9.0, 1, 50),
    ("1006.30", "Semi/wholly milled rice", 0.5, 1, 0),
    ("2710.19", "Lubricating oils", 1.1, 0, 5),
]

ORIGIN_RISK = {
    "China": 0.45, "India": 0.40, "Vietnam": 0.30, "Japan": 0.10, "South Korea": 0.15, "Singapore": 0.20,
    "Malaysia": 0.25, "Thailand": 0.25, "USA": 0.15, "Germany": 0.10, "UAE": 0.50, "Hong Kong": 0.55,
}

ROAD_NODES = [
    # id, nama, lat, lon, jenis
    ("PORT", "Gerbang Terminal Nusa Raya", -6.105, 106.885, "port"),
    ("DEPOT", "Depo GlobalCargo Cakung", -6.180, 106.945, "depot"),
    ("J_CILINCING", "Simpang Cilincing", -6.120, 106.940, "junction"),
    ("TOL_CAKUNG", "Gerbang Tol Cakung", -6.165, 106.960, "toll"),
    ("TOL_JORR_E", "JORR Timur", -6.230, 106.975, "toll"),
    ("TOL_CIKAMPEK_KM10", "Tol Cikampek KM10", -6.250, 107.010, "toll"),
    ("BEKASI_X", "Simpang Bekasi Timur", -6.260, 107.050, "junction"),
    ("CIKARANG_X", "Simpang Cikarang", -6.290, 107.160, "junction"),
    ("KARAWANG_X", "Simpang Karawang Barat", -6.330, 107.290, "junction"),
    ("ARTERI_KALIMALANG", "Jalan Arteri Kalimalang", -6.240, 106.990, "arterial"),
    ("WH_SUNTER", "Gudang CV Sunter Jaya", -6.140, 106.870, "warehouse"),
    ("WH_BEKASI", "Gudang PT Mitra Retail Bekasi", -6.250, 107.070, "warehouse"),
    ("WH_CIKARANG", "Gudang ImportExport Cikarang", -6.300, 107.180, "warehouse"),
    ("WH_KARAWANG", "Gudang ImportExport Karawang", -6.340, 107.310, "warehouse"),
]

ROAD_EDGES = [
    # from, to, menit free-flow, jenis jalan
    ("PORT", "J_CILINCING", 10, "port_road"),
    ("J_CILINCING", "TOL_CAKUNG", 12, "arterial"),
    ("DEPOT", "TOL_CAKUNG", 8, "local"),
    ("DEPOT", "J_CILINCING", 15, "local"),
    ("J_CILINCING", "WH_SUNTER", 18, "arterial"),
    ("TOL_CAKUNG", "TOL_JORR_E", 10, "toll"),
    ("TOL_JORR_E", "TOL_CIKAMPEK_KM10", 12, "toll"),
    ("TOL_CIKAMPEK_KM10", "BEKASI_X", 10, "toll"),
    ("BEKASI_X", "WH_BEKASI", 8, "local"),
    ("BEKASI_X", "CIKARANG_X", 20, "toll"),
    ("CIKARANG_X", "WH_CIKARANG", 10, "local"),
    ("CIKARANG_X", "KARAWANG_X", 25, "toll"),
    ("KARAWANG_X", "WH_KARAWANG", 10, "local"),
    ("J_CILINCING", "ARTERI_KALIMALANG", 25, "arterial"),
    ("ARTERI_KALIMALANG", "BEKASI_X", 35, "arterial"),
    ("ARTERI_KALIMALANG", "CIKARANG_X", 60, "arterial"),
]

TRUCK_COUNT = 8
TRIP_PRICE_JT = {"WH_CIKARANG": 3.0, "WH_KARAWANG": 3.6, "WH_BEKASI": 2.4, "WH_SUNTER": 1.8}

# Biaya (juta Rupiah) yang dipakai fungsi utilitas & KPI.
COSTS = {
    "vessel_wait_jt_per_h": 12.0,        # biaya kapal menunggu di anchorage (OceanLine)
    "vessel_wait_jit_jt_per_h": 6.0,     # bila diberitahu >= 6 jam sebelumnya (slow steaming)
    "external_wait_jt_per_h": 10.0,
    "late_departure_jt_per_h": 10.0,     # penalti terlambat berangkat (jadwal pelabuhan berikutnya)
    "crane_jt_per_crane_h": 2.5,         # biaya operasi crane (siang)
    "night_factor": 1.5,                 # 22:00-06:00
    "thc_jt_per_container": 1.2,         # pendapatan terminal handling charge
    "berth_contract_penalty_jt_per_h": 5.0,  # SmartPort -> kapal jika tunggu > 2 jam
    "replan_disruption_jt": 2.0,         # biaya operasional pindah dermaga
    "extra_gang_jt": 18.0,               # mobilisasi mobile crane + gang lembur (ditanggung SmartPort)
    "truck_trip_cost_jt": 1.6,           # biaya truk sendiri per trip
    "subcon_trip_cost_jt": 3.0,          # biaya truk subkon per trip
    "truck_overtime_jt_per_h": 0.15,     # biaya lembur sopir 22:00-06:00
    "gc_sla_penalty_jt_per_container_h": 0.3,
    "express_surcharge_jt_per_container": 1.0,
}

"""Definisi skenario simulasi: kondisi normal, gangguan, negosiasi, adaptive replanning.

Setiap skenario = baseline hari normal (start_of_day pada 00:00) + daftar perubahan lingkungan berwaktu.
Event dikirim sebagai pesan ENV_EVENT dari 'environment' kepada agen yang mempersepsinya.
"""

WEATHER_V001 = {"at": 1.0, "type": "weather_update", "target": "oceanline.eta_agent",
                "payload": {"vessels": ["V001"], "windKn": 35, "waveM": 3.6, "currentKn": -0.8, "congestionIdx": 0.3,
                            "description": "Badai lokal di rute MV Ocean Pioneer (angin 35 kn, gelombang 3,6 m)"}}
DOCS_SHP003 = {"at": 7.0, "type": "documents_received", "target": "importexport.document_agent",
               "payload": {"shipment_id": "SHP-003", "description": "Pemasok mengirim Persetujuan Impor & COO AIFTA"}}
CRANE_B1 = {"at": 10.0, "type": "crane_breakdown", "target": "smartport.operations_monitor_agent",
            "payload": {"berth_id": "B1", "factor": 0.34, "from_h": 10.0, "to_h": 22.0,
                        "description": "2 dari 3 quay crane B1 rusak (hydraulic failure)"}}
ROAD_FLOOD = {"at": 12.0, "type": "road_closure", "target": "globalcargo.route_agent",
              "payload": {"close_edges": [["BEKASI_X", "CIKARANG_X"]], "congestion": {"arterial": 1.6},
                          "description": "Banjir menutup tol Bekasi-Cikarang; arteri padat (x1,6)"}}
TRUCKS_DOWN = {"at": 9.0, "type": "truck_breakdown", "target": "globalcargo.fleet_planning_agent",
               "payload": {"count": 4, "from_h": 9.0, "to_h": 19.0,
                           "description": "4 truk ditarik (inspeksi rem massal) 09:00-19:00"}}
CUSTOMS_NHI = {"at": 10.0, "type": "risk_intel", "target": "customs.risk_agent",
               "payload": {"hs_prefix": "8542", "origin": "China", "level": "HIGH",
                           "note": "NHI: indikasi misdeclaration integrated circuits asal China"}}

SCENARIOS = {
    "S0_normal": {"title": "S0 - Kondisi normal", "events": [],
                  "description": "Hari operasi normal: prediksi ETA, MIP dermaga, dokumen, Bea Cukai, trucking."},
    "S1_vessel_delay": {"title": "S1 - Cuaca buruk: V001 terlambat ±4 jam", "events": [WEATHER_V001],
                        "description": "Studi kasus utama: kapal V001 terlambat, dermaga sudah dialokasikan ke kapal lain."},
    "S2_documents": {"title": "S2 - Dokumen susulan V002 (SHP-003) lengkap", "events": [DOCS_SHP003],
                     "description": "Dokumen yang kurang tiba; status V002 berubah dari Pending menjadi Scheduled."},
    "S3_crane_breakdown": {"title": "S3 - Crane B1 rusak (produktivitas 34%)", "events": [CRANE_B1],
                           "description": "Kapasitas dermaga turun saat kapal sedang dibongkar."},
    "S4_road_closure": {"title": "S4 - Banjir tol Bekasi-Cikarang", "events": [ROAD_FLOOD],
                        "description": "Graf jalan berubah; rute & jadwal truk dihitung ulang."},
    "S5_truck_shortage": {"title": "S5 - 4 truk ditarik 09:00-19:00", "events": [TRUCKS_DOWN],
                          "description": "Kapasitas armada turun; negosiasi express dengan truk subkon."},
    "S6_customs_alert": {"title": "S6 - Nota intelijen Bea Cukai (HS 8542 China)", "events": [CUSTOMS_NHI],
                         "description": "Rekomendasi HOLD -> keputusan petugas -> pemeriksaan ulang -> replanning truk."},
    "S7_combined": {"title": "S7 - Hari kacau (cuaca + dokumen + crane + banjir)",
                    "events": [WEATHER_V001, DOCS_SHP003, CRANE_B1, ROAD_FLOOD],
                    "description": "Beberapa gangguan beruntun untuk menguji ketahanan adaptasi."},
    "S8_hard_negotiation": {"title": "S8 - V001 terlambat + negosiasi alot -> mediasi Control Center",
                            "events": [WEATHER_V001],
                            "policy_overrides": {"smartport": {"max_concession_jt": 5},
                                                 "oceanline": {"max_acceptable_cost_jt": 20}},
                            "description": "Konsesi SmartPort maks 5 jt & toleransi biaya OceanLine 20 jt: negosiasi "
                                           "buntu 3 ronde lalu dimediasi koordinator global."},
}

STRATEGIES = {
    "static": "Tanpa adaptasi: rencana awal dibekukan, gangguan ditangani FCFS (baseline non-agentic)",
    "local": "Local enterprise intelligence: tiap perusahaan re-optimasi untuk dirinya sendiri, tanpa negosiasi/berbagi prioritas",
    "collaborative": "Global ecosystem intelligence: objektif ekosistem, negosiasi A2A, mediasi, berbagi prioritas",
}

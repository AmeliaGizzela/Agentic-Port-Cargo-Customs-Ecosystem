# 🌐 Agentic Port, Cargo & Customs Ecosystem

**Kelompok 1 — Case Study 10: International Port, Shipping and Customs Ecosystem**
Mata kuliah *AI Agentic Technology Systems for Digital Enterprise Ecosystem* (S2)

Prototipe sistem multi-agen heterogen lintas 5 organisasi (fiktif, data sintetis) yang berkolaborasi menangani
**kapal terlambat, konflik dermaga, dokumen tidak lengkap, pemeriksaan Bea Cukai, dan penjemputan kontainer**,
dengan **Global Agentic Control Center** untuk memantau komunikasi agen, negosiasi, keputusan, dan perubahan lingkungan.

| Organisasi (E_i) | Server API | Dashboard | AI / algoritma utama |
|---|---|---|---|
| 🌐 Global Agentic Control Center | http://127.0.0.1:8000 | http://127.0.0.1:8500 | Message bus A2A, mediator Nash bargaining, objektif global J |
| 🚢 OceanLine Shipping | http://127.0.0.1:8001 | http://127.0.0.1:8501 | **Gradient Boosting** prediksi ETA, negosiasi berbasis utilitas |
| ⚓ SmartPort Terminal | http://127.0.0.1:8002 | http://127.0.0.1:8502 | **Mixed-Integer Programming** berth allocation (SciPy/HiGHS) |
| 🚚 GlobalCargo Logistics | http://127.0.0.1:8003 | http://127.0.0.1:8503 | **VRPTW** multi-trip + local search, Dijkstra (networkx) |
| 📦 ImportExport Corp | http://127.0.0.1:8004 | http://127.0.0.1:8504 | **Document NLP** (TF-IDF + Logistic Regression) + rule engine |
| 🛃 Customs Authority | http://127.0.0.1:8005 | http://127.0.0.1:8505 | **Isolation Forest** + policy rules, *human-in-the-loop* |

Setiap perusahaan memiliki **database SQLite sendiri**, **skema & format API berbeda** (JSON camelCase/UTC,
SQL snake_case/WIB, envelope PascalCase/epoch, **XML** berbahasa Indonesia, JSON ISO+07:00), **kebijakan governance
sendiri** (`enterprises/<id>/governance.json`), dan agen-agen yang berkomunikasi lewat pesan bergaya **A2A**
(Agent Card di `/.well-known/agent-card.json`) serta akses tools bergaya **MCP** (`POST /mcp`, JSON-RPC `tools/list`, `tools/call`).

---

## 1. Menjalankan di VS Code (Windows / macOS / Linux)

1. Install **Python 3.11+** (diuji di Python 3.14) dan VS Code + ekstensi *Python*.
2. `File → Open Folder…` → pilih folder `agentic_port_ecosystem`.
3. Buka terminal VS Code (`Ctrl+``), lalu:

```bash
python -m pip install -r requirements.txt
```

```bash
python -m data.generate_synthetic
```

```bash
python run_all.py --open
```

`run_all.py` menyalakan 6 server FastAPI + 6 dashboard Streamlit sebagai proses terpisah (log di `logs/`).
Pada run pertama model ML dilatih otomatis (±30 detik). Hentikan dengan `Ctrl+C`.

Alternatif: menu **Run and Debug** (`F5`) → pilih *"Ekosistem lengkap (run_all.py)"*, atau
**Terminal → Run Task…** → task 1–5. Di Windows juga bisa klik ganda `run_all.bat`.

## 2. Demo cepat (±5 menit)

1. Buka **Control Center** http://127.0.0.1:8500 → *Masuk* (role **Ecosystem Admin**).
2. Tab **🎬 Skenario & Lingkungan** → pilih **S1 – Cuaca buruk: V001 terlambat ±4 jam**, strategi **Collaborative**,
   jeda pesan 0,4 dtk → **Reset & jalankan skenario**.
3. Tab **💬 Komunikasi Agen** (aktifkan *Auto-refresh*): lihat diagram sekuens pesan
   `ETA_UPDATE → PROPOSE → COUNTER_PROPOSE → ACCEPT → CONFIRM → container_availability → delivery_plan`.
4. Tab **🤝 Negosiasi**: SmartPort menawarkan opsi A, OceanLine membalas dengan opsi C (+1 mobile crane),
   SmartPort berkonsesi di ronde 2 → opsi dengan objektif global terbaik.
5. Tab **🧠 Decision Trace**: alasan setiap keputusan (perceive → reason → decide → act).
6. Ulangi dengan strategi **Static** dan **Local** → bandingkan KPI (tab **📈 KPI & Evaluasi**).
7. *Human-in-the-loop*: matikan *"Proxy petugas Bea Cukai tersimulasi"*, jalankan skenario, lalu buka
   dashboard **Customs** http://127.0.0.1:8505 (role **Customs Officer**) → *Setujui / Tahan / Tolak*. AI tidak
   pernah menyetujui sendiri; keputusan petugas memicu release → replanning truk.

## 2b. Opsional: kapal nyata dari AISStream.io

Peta **Pelacakan Kapal** di dashboard OceanLine dapat menampilkan posisi AIS real-time kapal nyata di Laut Jawa
sebagai layer konteks lingkungan (armada OceanLine sendiri tetap simulasi, agar skenario tetap bisa dikendalikan).

1. Daftar kunci API gratis di https://aisstream.io.
2. Salin `.env.example` menjadi `.env` dan isi `AISSTREAM_API_KEY=...` (atau set sebagai environment variable).
3. Restart `python run_all.py`. Server OceanLine membuka WebSocket ke AISStream di thread latar; data tersedia di
   `GET /api/v1/ais/live` (resource governance `ais`) dan muncul di peta sebagai titik abu-abu.

Tanpa kunci, fitur ini nonaktif secara otomatis dan sistem berjalan seperti biasa.

## 3. Evaluasi & pengujian

```bash
python -m simulation.run_evaluation
```
Menjalankan **9 skenario × 3 strategi** (static / local / collaborative) melalui simulasi multi-agen sungguhan
dan menulis `results/evaluation_results.csv`, `results/evaluation_summary.md`, `results/chart_*.png`.

```bash
python -m pytest tests -v
```
`tests/test_algorithms.py` (tanpa server) menguji MIP, VRP, Dijkstra, model ETA, NLP dokumen, model risiko, schema
mapping. `tests/test_integration.py` (butuh `python run_all.py --no-dash`) menguji governance (403/401), negosiasi,
perubahan keputusan terukur, dan persetujuan manusia Bea Cukai.

## 4. Struktur folder

```
agentic_port_ecosystem/
├─ common/              config, kontrak pesan A2A, agent base, gateway FastAPI, schema mapping, waktu
├─ data/                master data skenario + generator data sintetis (output per perusahaan, format native)
├─ enterprises/
│  ├─ oceanline/        eta_model.py (GBR) · agents.py · server.py · governance.json
│  ├─ smartport/        berth_mip.py (MIP) · repo.py · agents.py · server.py · governance.json
│  ├─ globalcargo/      vrp.py (Dijkstra + VRPTW) · agents.py · server.py · governance.json
│  ├─ importexport/     doc_nlp.py (TF-IDF+LR + rules) · agents.py · server.py (XML) · governance.json
│  └─ customs/          risk_model.py (Isolation Forest) · agents.py · server.py · governance.json
├─ control_center/      bus.py · kpi.py · scenarios.py · server.py
├─ dashboards/          app_*.py (Streamlit) + ui_common.py
├─ simulation/          run_evaluation.py
├─ tests/               unit & integration tests
├─ docs/                LAPORAN.md (laporan lengkap) · screenshots/
└─ results/             hasil evaluasi KPI
```

## 5. Catatan etika & batasan
Semua nama perusahaan **fiktif** dan data **sintetis**. Simulator tidak mengendalikan kapal, crane, truk, atau
memberikan persetujuan kepabeanan nyata; keputusan Bea Cukai selalu oleh petugas (atau *proxy manusia tersimulasi*
yang dicatat eksplisit untuk evaluasi KPI). Format pesan adalah kontrak JSON simulasi yang terinspirasi A2A/MCP,
bukan implementasi lengkap kedua spesifikasi.

"""Konfigurasi global ekosistem (port, URL, kunci API sintetis, horizon simulasi).

Semua nama perusahaan bersifat fiktif dan seluruh data bersifat sintetis.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"
LOG_DIR = ROOT / "logs"
HOST = os.environ.get("ECO_HOST", "127.0.0.1")

# ---------------------------------------------------------------------------
# Waktu simulasi. Semua algoritma bekerja dalam "jam sejak T0" (float).
# Setiap perusahaan menyimpan waktu dalam format nativenya sendiri.
# ---------------------------------------------------------------------------
WIB = timezone(timedelta(hours=7), "WIB")
T0 = datetime(2026, 11, 20, 0, 0, tzinfo=WIB)
SLOT_H = 0.5          # resolusi slot penjadwalan dermaga (jam)
HORIZON_H = 48.0      # horizon perencanaan (jam)

CONTROL_CENTER = {
    "id": "control_center",
    "name": "Global Agentic Control Center",
    "short": "Control Center",
    "api_port": int(os.environ.get("CC_API_PORT", 8000)),
    "dash_port": int(os.environ.get("CC_DASH_PORT", 8500)),
    "color": "#5B2C6F",
    "icon": "🌐",
}

ENTERPRISES: dict[str, dict] = {
    "oceanline": {
        "name": "OceanLine Shipping",
        "short": "OceanLine",
        "api_port": 8001,
        "dash_port": 8501,
        "color": "#0B5394",
        "icon": "🚢",
        "tagline": "Vessel tracking, ETA prediction & cargo manifest",
        "platform": "Document store (JSON-in-SQLite) · REST camelCase · waktu UTC (Z)",
        "dashboard": "dashboards/app_oceanline.py",
        "module": "enterprises.oceanline.server:app",
        "roles": ["Fleet Operator", "Commercial Manager", "Viewer"],
    },
    "smartport": {
        "name": "SmartPort Terminal",
        "short": "SmartPort",
        "api_port": 8002,
        "dash_port": 8502,
        "color": "#117A65",
        "icon": "⚓",
        "tagline": "Berth scheduling (MIP), yard & container release",
        "platform": "Relational SQL (normalized) · REST snake_case · waktu lokal WIB",
        "dashboard": "dashboards/app_smartport.py",
        "module": "enterprises.smartport.server:app",
        "roles": ["Port Planner", "Yard Supervisor", "Viewer"],
    },
    "globalcargo": {
        "name": "GlobalCargo Logistics",
        "short": "GlobalCargo",
        "api_port": 8003,
        "dash_port": 8503,
        "color": "#B9770E",
        "icon": "🚚",
        "tagline": "Truck allocation (VRPTW), route graph & container pickup",
        "platform": "SQL PascalCase · REST envelope {Status,Data} · Unix epoch",
        "dashboard": "dashboards/app_globalcargo.py",
        "module": "enterprises.globalcargo.server:app",
        "roles": ["Logistics Manager", "Dispatcher", "Viewer"],
    },
    "importexport": {
        "name": "ImportExport Corp",
        "short": "ImportExport",
        "api_port": 8004,
        "dash_port": 8504,
        "color": "#A93226",
        "icon": "📦",
        "tagline": "Purchase order, document NLP & readiness, trucking contracts",
        "platform": "SQL (skema berbahasa Indonesia) · API XML · format dd/mm/yyyy",
        "dashboard": "dashboards/app_importexport.py",
        "module": "enterprises.importexport.server:app",
        "roles": ["Trade Officer", "Procurement Manager", "Viewer"],
    },
    "customs": {
        "name": "Customs Authority",
        "short": "Customs",
        "api_port": 8005,
        "dash_port": 8505,
        "color": "#34495E",
        "icon": "🛃",
        "tagline": "Document verification, customs risk review (human approval)",
        "platform": "SQL + hash-chained audit · REST JSON + API key ketat · ISO-8601 +07:00",
        "dashboard": "dashboards/app_customs.py",
        "module": "enterprises.customs.server:app",
        "roles": ["Customs Officer", "Risk Analyst", "Supervisor"],
    },
}

# Kunci API sintetis untuk simulasi otorisasi antar-perusahaan (BUKAN rahasia nyata).
API_KEYS: dict[str, str] = {
    "control_center": "cc-demo-key",
    "oceanline": "ol-demo-key",
    "smartport": "sp-demo-key",
    "globalcargo": "gc-demo-key",
    "importexport": "ie-demo-key",
    "customs": "cu-demo-key",
}
KEY_TO_ID = {v: k for k, v in API_KEYS.items()}


def api_url(eid: str) -> str:
    port = CONTROL_CENTER["api_port"] if eid == "control_center" else ENTERPRISES[eid]["api_port"]
    return f"http://{HOST}:{port}"


def dash_url(eid: str) -> str:
    port = CONTROL_CENTER["dash_port"] if eid == "control_center" else ENTERPRISES[eid]["dash_port"]
    return f"http://{HOST}:{port}"


def headers_for(eid: str) -> dict:
    return {"X-API-Key": API_KEYS[eid], "X-Enterprise-Id": eid}


def enterprise_db_path(eid: str) -> Path:
    p = ROOT / "enterprises" / eid / "runtime"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{eid}.db"


def model_dir(eid: str) -> Path:
    p = ROOT / "enterprises" / eid / "models"
    p.mkdir(parents=True, exist_ok=True)
    return p

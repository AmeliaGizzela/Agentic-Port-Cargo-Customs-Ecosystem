"""Umpan AIS nyata dari AISStream.io (https://aisstream.io) - layer "kapal lain di sekitar pelabuhan".

Berjalan sebagai thread latar di server OceanLine. Posisi terakhir setiap kapal (per MMSI) disimpan di memori.
Armada OceanLine tetap simulasi; umpan ini hanya memperkaya persepsi lingkungan (kapal nyata di Laut Jawa).

Konfigurasi:
  AISSTREAM_API_KEY   kunci API gratis dari aisstream.io (env var, atau baris di file .env di root proyek)
  AISSTREAM_BBOX      opsional, "lat_sw,lon_sw,lat_ne,lon_ne" (default: Laut Jawa sekitar Tanjung Priok)
Tanpa kunci, umpan nonaktif dan endpoint /api/v1/ais/live mengembalikan status "disabled".
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"
DEFAULT_BBOX = (-7.0, 105.0, -4.5, 108.5)   # lat_sw, lon_sw, lat_ne, lon_ne
MAX_AGE_S = 30 * 60                           # kapal tanpa laporan > 30 menit dibuang


def _load_dotenv() -> None:
    """Pembaca .env minimal (KEY=VALUE per baris) tanpa dependensi tambahan."""
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def api_key() -> str | None:
    _load_dotenv()
    return os.environ.get("AISSTREAM_API_KEY") or None


def bbox() -> tuple[float, float, float, float]:
    raw = os.environ.get("AISSTREAM_BBOX")
    if raw:
        try:
            a = tuple(float(x) for x in raw.split(","))
            if len(a) == 4:
                return a  # type: ignore[return-value]
        except ValueError:
            pass
    return DEFAULT_BBOX


def parse_position(msg: dict) -> dict | None:
    """Ubah pesan AISStream 'PositionReport' menjadi dokumen ringkas (camelCase, UTC). None bila bukan posisi."""
    if msg.get("MessageType") != "PositionReport":
        return None
    meta = msg.get("MetaData") or {}
    pr = (msg.get("Message") or {}).get("PositionReport") or {}
    try:
        lat, lon = float(meta["latitude"]), float(meta["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "mmsi": str(meta.get("MMSI", "")),
        "shipName": (meta.get("ShipName") or "").strip() or f"MMSI {meta.get('MMSI', '?')}",
        "lat": lat, "lon": lon,
        "speedKn": pr.get("Sog"), "courseDeg": pr.get("Cog"), "headingDeg": pr.get("TrueHeading"),
        "navStatus": pr.get("NavigationalStatus"),
        "timeUtc": str(meta.get("time_utc", "")),
        "receivedUtc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "receivedTs": time.time(),
    }


class AisFeed:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._vessels: dict[str, dict] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.status = "disabled"
        self.last_error: str | None = None
        self.messages = 0
        self.connected_since: str | None = None

    # ---- lifecycle -------------------------------------------------------
    def start(self) -> "AisFeed":
        if not api_key():
            self.status = "disabled"
            self.last_error = "AISSTREAM_API_KEY tidak diset"
            return self
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="aisstream-feed", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    # ---- data ------------------------------------------------------------
    def snapshot(self) -> dict:
        cutoff = time.time() - MAX_AGE_S
        with self._lock:
            stale = [k for k, v in self._vessels.items() if v["receivedTs"] < cutoff]
            for k in stale:
                del self._vessels[k]
            vessels = [{k: v for k, v in d.items() if k != "receivedTs"} for d in self._vessels.values()]
        vessels.sort(key=lambda d: d["shipName"])
        lat_sw, lon_sw, lat_ne, lon_ne = bbox()
        return {"status": self.status, "source": "aisstream.io", "vessels": vessels, "count": len(vessels),
                "messages": self.messages, "connectedSinceUtc": self.connected_since, "lastError": self.last_error,
                "boundingBox": {"latSw": lat_sw, "lonSw": lon_sw, "latNe": lat_ne, "lonNe": lon_ne}}

    # ---- worker ----------------------------------------------------------
    def _run(self) -> None:
        from websockets.sync.client import connect

        backoff = 2.0
        while not self._stop.is_set():
            try:
                self.status = "connecting"
                with connect(AISSTREAM_URL, open_timeout=15, close_timeout=5) as ws:
                    lat_sw, lon_sw, lat_ne, lon_ne = bbox()
                    ws.send(json.dumps({"APIKey": api_key(),
                                        "BoundingBoxes": [[[lat_sw, lon_sw], [lat_ne, lon_ne]]],
                                        "FilterMessageTypes": ["PositionReport"]}))
                    self.status = "connected"
                    self.last_error = None
                    self.connected_since = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    backoff = 2.0
                    while not self._stop.is_set():
                        raw = ws.recv(timeout=60)
                        try:
                            msg = json.loads(raw)
                        except (TypeError, ValueError):
                            continue
                        if "error" in msg:  # AISStream mengirim {"error": "..."} bila kunci salah
                            raise RuntimeError(str(msg["error"]))
                        doc = parse_position(msg)
                        if doc:
                            self.messages += 1
                            with self._lock:
                                self._vessels[doc["mmsi"]] = doc
            except Exception as exc:  # noqa: BLE001 - reconnect apa pun penyebabnya
                self.status = "error"
                self.last_error = f"{type(exc).__name__}: {exc}"[:300]
                if self.messages == 0 and "ConnectionClosed" in type(exc).__name__:
                    # AISStream memutus koneksi tanpa close frame bila kunci API tidak valid
                    self.last_error += " (koneksi diputus tanpa data; periksa AISSTREAM_API_KEY)"
                self.connected_since = None
                if "api" in str(exc).lower() and "key" in str(exc).lower():
                    backoff = 60.0  # kunci salah: jangan spam
            if not self._stop.is_set():
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60.0)
        self.status = "stopped"

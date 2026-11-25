"""Konversi waktu antara representasi kanonik (jam sejak T0) dan format native tiap perusahaan.

Heterogenitas yang disimulasikan:
- OceanLine   : ISO-8601 UTC berakhiran 'Z'          -> "2026-11-20T01:00:00Z"
- SmartPort   : waktu lokal WIB tanpa zona            -> "2026-11-20 08:00"
- GlobalCargo : Unix epoch (detik)                    -> 1795143600
- ImportExport: format Indonesia dd/mm/yyyy HH:MM     -> "20/11/2026 08:00"
- Customs     : ISO-8601 dengan offset +07:00         -> "2026-11-20T08:00:00+07:00"
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from common.config import T0, WIB


def h2dt(h: float) -> datetime:
    return T0 + timedelta(hours=float(h))


def dt2h(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=WIB)
    return round((dt - T0).total_seconds() / 3600.0, 4)


# --- OceanLine (UTC Z) ---------------------------------------------------
def to_utc_z(h: float) -> str:
    return h2dt(h).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_utc_z(s: str) -> float:
    return dt2h(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc))


# --- SmartPort (local WIB) ---------------------------------------------------
def to_local(h: float) -> str:
    return h2dt(h).strftime("%Y-%m-%d %H:%M")


def from_local(s: str) -> float:
    return dt2h(datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=WIB))


# --- GlobalCargo (epoch seconds) --------------------------------------------
def to_epoch(h: float) -> int:
    return int(h2dt(h).timestamp())


def from_epoch(sec: int | float) -> float:
    return dt2h(datetime.fromtimestamp(float(sec), tz=timezone.utc))


# --- ImportExport (dd/mm/yyyy HH:MM, WIB) ------------------------------------
def to_id(h: float) -> str:
    return h2dt(h).strftime("%d/%m/%Y %H:%M")


def from_id(s: str) -> float:
    return dt2h(datetime.strptime(s, "%d/%m/%Y %H:%M").replace(tzinfo=WIB))


# --- Customs (ISO +07:00) ----------------------------------------------------
def to_iso(h: float) -> str:
    return h2dt(h).isoformat(timespec="seconds")


def from_iso(s: str) -> float:
    return dt2h(datetime.fromisoformat(s))


# --- tampilan --------------------------------------------------------------------
def clock(h: float | None) -> str:
    """Label singkat: '08:00' untuk hari-1, 'D+1 06:30' untuk hari berikutnya."""
    if h is None:
        return "-"
    dt = h2dt(h)
    day = int((dt - T0).total_seconds() // 86400)
    s = dt.strftime("%H:%M")
    return s if day == 0 else f"D+{day} {s}"


def snap_up(h: float, slot: float = 0.5) -> float:
    """Bulatkan ke atas ke slot terdekat (toleransi kecil)."""
    import math
    return round(math.ceil(round(h / slot, 6) - 1e-9) * slot, 4)

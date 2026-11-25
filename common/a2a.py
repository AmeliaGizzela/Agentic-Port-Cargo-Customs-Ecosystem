"""Kontrak pesan antar-agen (terinspirasi konsep Agent2Agent / A2A) dan klien message bus.

Catatan: format ini adalah kontrak JSON buatan untuk simulasi, bukan implementasi
lengkap spesifikasi A2A. Konsep yang diadopsi: Agent Card untuk discovery,
pesan bertipe (performative ala FIPA-ACL), conversation/task id, dan kebijakan
otorisasi antar-organisasi.
"""
from __future__ import annotations

import itertools
import threading
import time
from typing import Any

import requests
from pydantic import BaseModel, Field

from common.config import API_KEYS, api_url

MESSAGE_TYPES = [
    "INFORM",            # berbagi fakta / status
    "REQUEST",           # meminta tindakan / data
    "CFP",               # call for proposal (Contract Net Protocol)
    "PROPOSE",           # tawaran
    "COUNTER_PROPOSE",   # tawaran balik
    "ACCEPT",            # menerima tawaran
    "REJECT",            # menolak tawaran
    "REFUSE",            # menolak CFP (tidak sanggup)
    "CONFIRM",           # konfirmasi komitmen
    "ALERT",             # peringatan / gangguan
    "ENV_EVENT",         # perubahan lingkungan yang dipersepsi agen
    "MEDIATION_REQUEST", # permintaan mediasi ke Control Center
]


class A2AMessage(BaseModel):
    message_id: str | None = None
    conversation_id: str | None = None
    in_reply_to: str | None = None
    sender: str
    receiver: str
    message_type: str
    task: str
    payload: dict[str, Any] = Field(default_factory=dict)
    reply_required: bool = False
    sim_time_h: float | None = None
    created_at: str | None = None
    protocol: str = "a2a-sim/1.0"


def enterprise_of(address: str) -> str:
    return address.split(".", 1)[0]


_conv_counter = itertools.count(1)
_conv_lock = threading.Lock()


def new_conversation_id(prefix: str) -> str:
    with _conv_lock:
        n = next(_conv_counter)
    return f"CONV-{prefix.upper()}-{int(time.time() * 1000) % 10_000_000:07d}-{n:03d}"


class BusClient:
    """Klien untuk mengirim pesan melalui message bus di Global Control Center."""

    def __init__(self, enterprise_id: str):
        self.enterprise_id = enterprise_id
        self.base = api_url("control_center")
        self.headers = {"X-API-Key": API_KEYS[enterprise_id], "X-Enterprise-Id": enterprise_id}

    def send(self, message: dict) -> dict:
        try:
            r = requests.post(f"{self.base}/a2a/send", json=message, headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # bus tidak tersedia -> catat tetapi jangan crash
            return {"accepted": False, "error": str(exc)}

    def report_decision(self, record: dict) -> None:
        try:
            requests.post(f"{self.base}/trace", json=record, headers=self.headers, timeout=10)
        except Exception:
            pass


def build_agent_card(enterprise_id: str, meta: dict, agents: list) -> dict:
    """Agent Card ala A2A (/.well-known/agent-card.json)."""
    skills = []
    for ag in agents:
        for sk in ag.skills:
            skills.append({
                "id": f"{ag.name}.{sk['id']}",
                "name": sk.get("name", sk["id"]),
                "description": sk.get("description", ""),
                "tags": sk.get("tags", []),
                "agent": ag.address,
            })
    return {
        "name": meta["name"],
        "description": meta.get("tagline", ""),
        "url": api_url(enterprise_id) + "/a2a/inbox",
        "provider": {"organization": meta["name"], "fictional": True},
        "version": "1.0.0",
        "protocolVersion": "a2a-sim/1.0",
        "capabilities": {"streaming": False, "pushNotifications": True, "stateTransitionHistory": True},
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json", "application/xml"] if enterprise_id == "importexport" else ["application/json"],
        "securitySchemes": {"apiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}},
        "agents": [{"address": a.address, "role": a.role, "description": a.description} for a in agents],
        "skills": skills,
        "tools_endpoint": api_url(enterprise_id) + "/mcp",
    }

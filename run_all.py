"""Launcher: menjalankan 6 server API (FastAPI/uvicorn) + 6 dashboard (Streamlit) sebagai proses terpisah.

Pemakaian:
  python run_all.py                 # semua server + dashboard
  python run_all.py --no-dash       # hanya server API
  python run_all.py --open          # sekaligus membuka tab browser
Hentikan dengan Ctrl+C (semua proses anak ikut berhenti).
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from common.config import CONTROL_CENTER, ENTERPRISES, HOST, LOG_DIR  # noqa: E402

PY = sys.executable


def start(cmd: list[str], name: str) -> subprocess.Popen:
    LOG_DIR.mkdir(exist_ok=True)
    log = open(LOG_DIR / f"{name}.log", "w", encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    return subprocess.Popen(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, env=env, creationflags=flags)


def wait_health(url: str, timeout: float = 90) -> bool:
    t_end = time.time() + timeout
    while time.time() < t_end:
        try:
            if requests.get(url, timeout=2).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-dash", action="store_true")
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    procs: list[tuple[str, subprocess.Popen]] = []
    api = [("control_center", "control_center.server:app", CONTROL_CENTER["api_port"])] + [
        (eid, m["module"], m["api_port"]) for eid, m in ENTERPRISES.items()]
    for name, module, port in api:
        procs.append((f"api_{name}", start([PY, "-m", "uvicorn", module, "--host", HOST, "--port", str(port),
                                            "--log-level", "warning"], f"api_{name}")))
    print("Menunggu server API siap (model ML dilatih otomatis pada run pertama)...")
    for name, _module, port in api:
        ok = wait_health(f"http://{HOST}:{port}/health", timeout=180)
        print(f"  {'OK ' if ok else 'ERR'} {name:<15} http://{HOST}:{port}")
    if not args.no_dash:
        dash = [("control_center", "dashboards/app_control_center.py", CONTROL_CENTER["dash_port"])] + [
            (eid, m["dashboard"], m["dash_port"]) for eid, m in ENTERPRISES.items()]
        for name, script, port in dash:
            procs.append((f"dash_{name}", start([PY, "-m", "streamlit", "run", script, "--server.port", str(port),
                                                 "--server.address", HOST, "--server.headless", "true",
                                                 "--browser.gatherUsageStats", "false"], f"dash_{name}")))
        for name, _script, port in dash:
            ok = wait_health(f"http://{HOST}:{port}/_stcore/health", timeout=120)
            print(f"  {'OK ' if ok else 'ERR'} dashboard {name:<15} http://{HOST}:{port}")
            if ok and args.open:
                webbrowser.open(f"http://{HOST}:{port}")
    print("\nSemua proses berjalan. Log di folder logs/. Tekan Ctrl+C untuk berhenti.")
    try:
        while True:
            time.sleep(1)
            for name, p in procs:
                if p.poll() is not None:
                    print(f"[!] proses {name} berhenti (kode {p.returncode}); lihat logs/{name}.log")
                    procs.remove((name, p))
                    break
    except KeyboardInterrupt:
        pass
    finally:
        print("Menghentikan semua proses...")
        for _name, p in procs:
            try:
                if os.name == "nt":
                    p.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    p.terminate()
            except Exception:
                pass
        time.sleep(1.5)
        for _name, p in procs:
            if p.poll() is None:
                p.kill()


if __name__ == "__main__":
    main()

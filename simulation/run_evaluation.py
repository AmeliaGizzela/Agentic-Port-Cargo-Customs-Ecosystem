"""Evaluasi KPI: membandingkan 3 strategi pada setiap skenario dengan menjalankan SIMULASI MULTI-AGEN SESUNGGUHNYA
(melalui API Control Center, semua server harus berjalan: python run_all.py --no-dash).

Strategi:
  static        - rencana awal dibekukan (tanpa adaptasi; baseline non-agentic)
  local         - local enterprise intelligence (re-optimasi egois, tanpa negosiasi/berbagi prioritas)
  collaborative - global ecosystem intelligence (objektif ekosistem + negosiasi A2A + mediasi + express CFP)

Output: results/evaluation_results.csv, results/evaluation_summary.md, results/*.png
Pemakaian: python -m simulation.run_evaluation [--scenarios S1_vessel_delay S3_crane_breakdown] [--strategies ...]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common.config import RESULTS_DIR, api_url  # noqa: E402
from control_center.scenarios import SCENARIOS, STRATEGIES  # noqa: E402

CC = api_url("control_center")
METRICS = ["J_global_objective", "C_total_cost_jt", "T_time_h", "R_risk", "Q_on_time_rate", "avg_vessel_wait_h",
           "max_vessel_wait_h", "vessels_wait_gt_2h", "late_departures", "avg_container_dwell_h",
           "late_container_hours", "importer_penalty_jt", "vessel_cost_jt", "crane_cost_jt", "truck_cost_jt",
           "express_contracts", "subcon_trips", "negotiations", "negotiation_rounds", "mediations",
           "berth_plan_versions", "berth_changes", "truck_plan_versions", "compliance_violations", "human_decisions"]


def run_one(scenario: str, strategy: str) -> dict:
    r = requests.post(f"{CC}/scenario/run", json={"scenario_id": scenario, "strategy": strategy,
                                                  "auto_officer": True, "bus_delay_s": 0.0}, timeout=30)
    r.raise_for_status()
    t0 = time.time()
    while True:
        time.sleep(1.0)
        st = requests.get(f"{CC}/scenario/status", timeout=30).json()["run"]
        if st["status"] in ("DONE", "FAILED"):
            break
        if time.time() - t0 > 900:
            raise TimeoutError(scenario)
    if st["status"] == "FAILED":
        raise RuntimeError(st.get("error"))
    row = {"scenario": scenario, "strategy": strategy, "messages": st.get("messages"),
           "runtime_s": round(time.time() - t0, 1)}
    for m in METRICS:
        row[m] = st["after"].get(m)
        row[f"baseline_{m}"] = st["baseline"].get(m)
    for k, v in (st.get("utilities") or {}).items():
        row[f"U_{k}"] = v
    return row


def write_outputs(rows: list[dict]) -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(RESULTS_DIR / "evaluation_results.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    lines = ["# Hasil Evaluasi Strategi (dihasilkan otomatis)", "",
             "Nilai = kondisi akhir setelah semua gangguan pada skenario. J lebih kecil = lebih baik.", ""]
    show = ["J_global_objective", "C_total_cost_jt", "avg_vessel_wait_h", "avg_container_dwell_h",
            "Q_on_time_rate", "importer_penalty_jt", "late_departures", "negotiations", "compliance_violations"]
    for sc in dict.fromkeys(r["scenario"] for r in rows):
        lines += [f"## {SCENARIOS[sc]['title']}", "", "| Strategi | " + " | ".join(show) + " |",
                  "|---|" + "---|" * len(show)]
        for r in rows:
            if r["scenario"] == sc:
                lines.append(f"| {r['strategy']} | " + " | ".join(str(r.get(m)) for m in show) + " |")
        lines.append("")
    lines += ["## Keterangan strategi", ""] + [f"- **{k}**: {v}" for k, v in STRATEGIES.items()]
    (RESULTS_DIR / "evaluation_summary.md").write_text("\n".join(lines), encoding="utf-8")
    try:
        make_charts(rows)
    except Exception as exc:  # grafik opsional
        print("grafik gagal:", exc)


def make_charts(rows: list[dict]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    scen = list(dict.fromkeys(r["scenario"] for r in rows))
    strat = ["static", "local", "collaborative"]
    colors = {"static": "#9AA5B1", "local": "#E3A33B", "collaborative": "#2E7D6B"}
    for metric, label in [("J_global_objective", "Objektif global J (lebih kecil lebih baik)"),
                          ("C_total_cost_jt", "Total biaya ekosistem (juta Rp)"),
                          ("avg_vessel_wait_h", "Rata-rata waktu tunggu kapal (jam)"),
                          ("avg_container_dwell_h", "Rata-rata dwelling kontainer (jam)"),
                          ("Q_on_time_rate", "Rasio kontainer tepat waktu")]:
        fig, ax = plt.subplots(figsize=(10, 4.2))
        x = np.arange(len(scen))
        for i, s in enumerate(strat):
            vals = [next((float(r[metric] or 0) for r in rows if r["scenario"] == sc and r["strategy"] == s), 0)
                    for sc in scen]
            ax.bar(x + (i - 1) * 0.27, vals, 0.26, label=s, color=colors[s])
        ax.set_xticks(x, [s.split("_")[0] for s in scen])
        ax.set_title(label)
        ax.legend(frameon=False, loc="upper left", bbox_to_anchor=(1.0, 1.0))
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(RESULTS_DIR / f"chart_{metric}.png", dpi=130)
        plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", nargs="*", default=list(SCENARIOS))
    ap.add_argument("--strategies", nargs="*", default=["static", "local", "collaborative"])
    args = ap.parse_args()
    rows = []
    for sc in args.scenarios:
        for st in args.strategies:
            print(f"> {sc:<20} {st:<14}", end="", flush=True)
            row = run_one(sc, st)
            rows.append(row)
            print(f" J={row['J_global_objective']:<7} C={row['C_total_cost_jt']:<8} wait={row['avg_vessel_wait_h']:<5} "
                  f"dwell={row['avg_container_dwell_h']:<5} Q={row['Q_on_time_rate']:<6} pen={row['importer_penalty_jt']:<6} "
                  f"neg={row['negotiations']} msgs={row['messages']} ({row['runtime_s']} s)")
    write_outputs(rows)
    print("Hasil ditulis ke", RESULTS_DIR)


if __name__ == "__main__":
    main()

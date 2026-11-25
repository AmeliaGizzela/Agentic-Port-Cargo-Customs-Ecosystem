"""Model risiko kepabeanan: Isolation Forest (anomaly detection) + policy rules -> rekomendasi jalur.

AI hanya MEREKOMENDASIKAN jalur (HIJAU/KUNING/MERAH). Keputusan release/hold selalu oleh petugas Bea Cukai.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import precision_score, recall_score, roc_auc_score

FEATURES = ["log_value_ratio", "origin_risk", "importer_score", "doc_consistency", "log_weight_per_container",
            "restricted_no_permit"]
LANE_HOURS = {"GREEN": 0.5, "YELLOW": 3.0, "RED": 8.0}


def featurize(d: dict, ref: dict, origin_risk: dict) -> dict:
    hs = ref[d["hs_code"]]
    expected = hs["ref_unit_value_usd_per_kg"] * float(d["gross_weight_kg"])
    ratio = float(d["customs_value_usd"]) / max(expected, 1.0)
    return {"log_value_ratio": math.log(max(ratio, 1e-3)),
            "origin_risk": float(origin_risk.get(d["origin_country"], 0.3)),
            "importer_score": float(d.get("importer_score", 0.8)),
            "doc_consistency": float(d.get("doc_consistency", 1.0)),
            "log_weight_per_container": math.log(float(d["gross_weight_kg"]) / max(int(d["containers"]), 1)),
            "restricted_no_permit": int(int(hs["restricted"]) == 1 and not d.get("has_permit", True)),
            "value_ratio": ratio}


class RiskModel:
    def __init__(self, model_dir: Path, history_csv: Path, ref: dict, origin_risk: dict):
        self.path = Path(model_dir) / "isoforest.joblib"
        self.metrics_path = Path(model_dir) / "risk_metrics.json"
        self.history_csv = Path(history_csv)
        self.ref, self.origin_risk = ref, origin_risk
        self.model: IsolationForest | None = None
        self.metrics: dict = {}
        self.score_sorted: np.ndarray | None = None

    def _frame(self, df: pd.DataFrame) -> pd.DataFrame:
        rows = [featurize({**r, "has_permit": bool(r["has_permit"])}, self.ref, self.origin_risk)
                for r in df.to_dict("records")]
        return pd.DataFrame(rows)

    def load_or_train(self) -> "RiskModel":
        if self.path.exists() and self.metrics_path.exists():
            self.model, self.score_sorted = joblib.load(self.path)
            self.metrics = json.loads(self.metrics_path.read_text(encoding="utf-8"))
        else:
            self.train()
        return self

    def train(self) -> dict:
        df = pd.read_csv(self.history_csv, dtype={"hs_code": str})
        X = self._frame(df)
        self.model = IsolationForest(n_estimators=300, contamination=0.08, random_state=42).fit(X[FEATURES])
        raw = -self.model.score_samples(X[FEATURES])
        self.score_sorted = np.sort(raw)
        pct = np.searchsorted(self.score_sorted, raw) / len(raw)
        y = df["violation"].values
        lanes = [self._lane(p, f)[0] for p, f in zip(pct, X.to_dict("records"))]
        flagged = np.array([ln != "GREEN" for ln in lanes])
        red = np.array([ln == "RED" for ln in lanes])
        self.metrics = {"model": "IsolationForest(n=300, contamination=0.08) + policy rules",
                        "n_history": int(len(df)), "violation_rate": round(float(y.mean()), 4),
                        "roc_auc_anomaly_score": round(float(roc_auc_score(y, raw)), 4),
                        "flag_precision": round(float(precision_score(y, flagged)), 4),
                        "flag_recall": round(float(recall_score(y, flagged)), 4),
                        "red_precision": round(float(precision_score(y, red, zero_division=0)), 4),
                        "lane_share": {ln: round(float(np.mean([x == ln for x in lanes])), 4) for ln in LANE_HOURS}}
        joblib.dump((self.model, self.score_sorted), self.path)
        self.metrics_path.write_text(json.dumps(self.metrics, indent=2), encoding="utf-8")
        return self.metrics

    def _lane(self, pct: float, f: dict, intel_hit: bool = False, manifest_ok: bool = True,
              restricted: bool = False) -> tuple[str, list[str]]:
        hits = []
        if f["restricted_no_permit"]:
            hits.append("P1 barang larangan/pembatasan tanpa izin impor -> MERAH")
        if intel_hit:
            hits.append("P2 cocok dengan nota intelijen (NHI) aktif -> MERAH")
        if f["value_ratio"] < 0.35:
            hits.append(f"P3 nilai pabean {f['value_ratio']:.0%} dari nilai referensi (indikasi undervaluation berat) -> MERAH")
        if pct >= 0.97 and (f["value_ratio"] < 0.5 or f["origin_risk"] >= 0.4):
            hits.append(f"P4 skor anomali persentil {pct:.0%} + risiko nilai/negara asal -> MERAH")
        if hits:
            return "RED", hits
        if pct >= 0.85:
            hits.append(f"P5 skor anomali tinggi (persentil {pct:.0%}) -> KUNING")
        if f["value_ratio"] < 0.6:
            hits.append(f"P6 nilai pabean {f['value_ratio']:.0%} dari referensi -> KUNING")
        if f["doc_consistency"] < 0.9:
            hits.append("P7 konsistensi dokumen < 0,90 -> KUNING")
        if not manifest_ok:
            hits.append("P8 deklarasi tidak cocok dengan manifest kapal -> KUNING")
        if restricted:
            hits.append("P9 komoditas lartas (izin ada) -> pemeriksaan dokumen KUNING")
        return ("YELLOW", hits) if hits else ("GREEN", ["Tidak ada aturan risiko yang terpicu"])

    def assess(self, d: dict, intel_hit: bool = False, manifest_ok: bool = True) -> dict:
        f = featurize(d, self.ref, self.origin_risk)
        x = pd.DataFrame([[f[k] for k in FEATURES]], columns=FEATURES)
        raw = float(-self.model.score_samples(x)[0])
        pct = float(np.searchsorted(self.score_sorted, raw) / len(self.score_sorted))
        restricted = int(self.ref[d["hs_code"]]["restricted"]) == 1
        lane, hits = self._lane(pct, f, intel_hit, manifest_ok, restricted)
        # kontribusi fitur sederhana: deviasi terhadap median historis (untuk explainability)
        contrib = {"value_ratio": round(f["value_ratio"], 3), "origin_risk": f["origin_risk"],
                   "importer_score": f["importer_score"], "doc_consistency": f["doc_consistency"]}
        return {"anomaly_score": round(raw, 4), "anomaly_percentile": round(pct, 4), "recommended_lane": lane,
                "rule_hits": hits, "features": {k: round(float(v), 4) for k, v in f.items()}, "explain": contrib}

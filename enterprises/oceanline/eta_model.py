"""Model AI prediktif OceanLine: prediksi keterlambatan ETA kapal (Gradient Boosting Regressor).

Fitur: jarak tersisa, kecepatan rencana, angin, gelombang, arus, indeks kongesti, ukuran kapal.
Target: keterlambatan (jam) terhadap ETA jadwal. Dibandingkan dengan baseline 'jadwal' (delay=0)
dan regresi linear untuk menunjukkan nilai tambah model.
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split

FEATURES = ["distanceNm", "plannedSpeedKn", "windKn", "waveM", "currentKn", "congestionIdx", "vesselTeu"]
TARGET = "delayHours"


class EtaModel:
    def __init__(self, model_dir: Path, data_csv: Path):
        self.model_path = Path(model_dir) / "eta_gbr.joblib"
        self.metrics_path = Path(model_dir) / "eta_metrics.json"
        self.data_csv = Path(data_csv)
        self.model: GradientBoostingRegressor | None = None
        self.metrics: dict = {}

    def load_or_train(self) -> "EtaModel":
        if self.model_path.exists() and self.metrics_path.exists():
            self.model = joblib.load(self.model_path)
            self.metrics = json.loads(self.metrics_path.read_text(encoding="utf-8"))
        else:
            self.train()
        return self

    def train(self) -> dict:
        df = pd.read_csv(self.data_csv)
        X, y = df[FEATURES], df[TARGET]
        Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=42)
        gbr = GradientBoostingRegressor(n_estimators=800, max_depth=4, learning_rate=0.03, subsample=0.9,
                                        random_state=42)
        gbr.fit(Xtr, ytr)
        lin = LinearRegression().fit(Xtr, ytr)
        pred = gbr.predict(Xte)
        q_lo = GradientBoostingRegressor(loss="quantile", alpha=0.1, n_estimators=200, max_depth=3,
                                         learning_rate=0.05, random_state=42).fit(Xtr, ytr)
        q_hi = GradientBoostingRegressor(loss="quantile", alpha=0.9, n_estimators=200, max_depth=3,
                                         learning_rate=0.05, random_state=42).fit(Xtr, ytr)
        self.model = gbr
        self.q_models = (q_lo, q_hi)
        self.metrics = {
            "model": "GradientBoostingRegressor(n=800, depth=4, lr=0.03)",
            "n_train": int(len(Xtr)), "n_test": int(len(Xte)),
            "mae_h": round(float(mean_absolute_error(yte, pred)), 3),
            "r2": round(float(r2_score(yte, pred)), 3),
            "baseline_schedule_mae_h": round(float(mean_absolute_error(yte, np.zeros(len(yte)))), 3),
            "linear_regression_mae_h": round(float(mean_absolute_error(yte, lin.predict(Xte))), 3),
            "feature_importance": {f: round(float(v), 4) for f, v in zip(FEATURES, gbr.feature_importances_)},
        }
        joblib.dump(gbr, self.model_path)
        joblib.dump(self.q_models, self.model_path.with_name("eta_quantiles.joblib"))
        self.metrics_path.write_text(json.dumps(self.metrics, indent=2), encoding="utf-8")
        return self.metrics

    def predict(self, features: dict) -> dict:
        x = pd.DataFrame([[float(features[f]) for f in FEATURES]], columns=FEATURES)
        delay = float(self.model.predict(x)[0])
        try:
            if not hasattr(self, "q_models"):
                self.q_models = joblib.load(self.model_path.with_name("eta_quantiles.joblib"))
            lo, hi = (float(m.predict(x)[0]) for m in self.q_models)
        except Exception:
            lo, hi = delay - 0.5, delay + 0.5
        return {"delayHours": round(delay, 3), "p10": round(min(lo, delay), 3), "p90": round(max(hi, delay), 3)}

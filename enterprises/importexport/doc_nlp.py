"""Document Intelligence ImportExport Corp.

1) Klasifikasi jenis dokumen: TF-IDF (word 1-2 gram + char 3-5 gram) + Logistic Regression.
2) Ekstraksi field: regular expression (HS code, nilai, kuantitas, berat, B/L, kapal, negara asal, nomor izin).
3) Rule engine kesiapan dokumen (kelengkapan & konsistensi) -> status COMPLETE / INCOMPLETE + daftar isu.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import joblib
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.pipeline import FeatureUnion, Pipeline

DOC_TYPES = ["INVOICE", "PACKING_LIST", "BILL_OF_LADING", "CERT_ORIGIN", "IMPORT_PERMIT", "INSURANCE"]


class DocClassifier:
    def __init__(self, model_dir: Path, train_csv: Path):
        self.path = Path(model_dir) / "doc_clf.joblib"
        self.metrics_path = Path(model_dir) / "doc_clf_metrics.json"
        self.train_csv = Path(train_csv)
        self.pipe: Pipeline | None = None
        self.metrics: dict = {}

    def load_or_train(self) -> "DocClassifier":
        if self.path.exists() and self.metrics_path.exists():
            self.pipe = joblib.load(self.path)
            self.metrics = json.loads(self.metrics_path.read_text(encoding="utf-8"))
        else:
            self.train()
        return self

    def train(self) -> dict:
        df = pd.read_csv(self.train_csv)
        Xtr, Xte, ytr, yte = train_test_split(df["teks"], df["jenis"], test_size=0.25, random_state=42,
                                              stratify=df["jenis"])
        feats = FeatureUnion([("word", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
                              ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=3, max_features=20000))])
        self.pipe = Pipeline([("tfidf", feats), ("clf", LogisticRegression(max_iter=2000, C=4.0))])
        self.pipe.fit(Xtr, ytr)
        pred = self.pipe.predict(Xte)
        self.metrics = {"model": "TF-IDF(word1-2 + char3-5) + LogisticRegression", "n_train": int(len(Xtr)),
                        "n_test": int(len(Xte)), "accuracy": round(float(accuracy_score(yte, pred)), 4),
                        "labels": DOC_TYPES,
                        "confusion_matrix": confusion_matrix(yte, pred, labels=DOC_TYPES).tolist(),
                        "report": classification_report(yte, pred, output_dict=True, zero_division=0)}
        joblib.dump(self.pipe, self.path)
        self.metrics_path.write_text(json.dumps(self.metrics, indent=2), encoding="utf-8")
        return self.metrics

    def predict(self, text: str) -> tuple[str, float]:
        proba = self.pipe.predict_proba([text])[0]
        i = int(proba.argmax())
        return str(self.pipe.classes_[i]), round(float(proba[i]), 4)


_PATTERNS = {
    "invoice_no": r"(?:Invoice No|Ref\. Invoice No)\s*:\s*([A-Z0-9\-]+)",
    "hs_code": r"(?:HS Code|Pos Tarif / HS)\s*:\s*(\d{4}\.\d{2})",
    "total_value_usd": r"Total Amount\s*:\s*USD\s*([\d,]+\.\d{2})",
    "quantity": r"(?:Quantity|Jumlah Kuota)\s*:\s*([\d,]+)",
    "gross_weight_kg": r"Gross Weight\s*:\s*([\d,]+)\s*kg",
    "containers": r"(?:Number of Containers\s*:\s*(\d+)|Packages\s*:\s*(\d+)\s*x)",
    "bl_no": r"B/L No\s*:\s*([A-Z0-9]+)",
    "vessel": r"(?:Vessel|Conveyance)\s*:\s*(MV [A-Za-z ]+?)(?:\s{2,}|\s+Voyage|$)",
    "origin": r"(?:Country of Origin|Negara Asal)\s*:\s*([A-Za-z ]+)",
    "consignee": r"(?:Buyer / Consignee|Consignee|Diberikan kepada|Insured)\s*:\s*([A-Za-z .]+)",
    "permit_no": r"Nomor\s*:\s*(PI-[\d./]+)",
    "scheme": r"Scheme\s*:\s*([A-Za-z \-]+)",
}


def extract_fields(text: str) -> dict:
    out = {}
    for k, pat in _PATTERNS.items():
        m = re.search(pat, text, flags=re.MULTILINE)
        if not m:
            continue
        val = next(g for g in m.groups() if g)
        val = val.strip()
        if k in ("total_value_usd",):
            val = float(val.replace(",", ""))
        elif k in ("quantity", "gross_weight_kg", "containers"):
            val = int(val.replace(",", ""))
        out[k] = val
    return out


def check_readiness(po: dict, shipment: dict, docs: list[dict], vessel_name: str | None) -> dict:
    """Rule engine kesiapan dokumen impor.
    po: {kode_hs, nilai_usd, jumlah, klaim_fta, wajib_izin_impor}; docs: [{jenis, fields}]"""
    present = {d["jenis"]: d["fields"] for d in docs}
    required = ["INVOICE", "PACKING_LIST", "BILL_OF_LADING"]
    if po.get("klaim_fta"):
        required.append("CERT_ORIGIN")
    if int(po.get("wajib_izin_impor") or 0):
        required.append("IMPORT_PERMIT")
    issues, warnings, checks = [], [], []
    missing = [r for r in required if r not in present]
    for m in missing:
        issues.append(f"R1 dokumen wajib belum ada: {m}")
    checks.append(("R1 kelengkapan dokumen wajib", not missing))
    inv, pl, bl = present.get("INVOICE", {}), present.get("PACKING_LIST", {}), present.get("BILL_OF_LADING", {})
    ok = not (inv and pl) or inv.get("quantity") == pl.get("quantity")
    checks.append(("R2 kuantitas invoice = packing list", ok))
    if not ok:
        issues.append(f"R2 kuantitas berbeda: invoice {inv.get('quantity')} vs PL {pl.get('quantity')}")
    hs_docs = {d: f.get("hs_code") for d, f in present.items() if f.get("hs_code")}
    ok = all(h == po["kode_hs"] for h in hs_docs.values())
    checks.append(("R3 HS code konsisten dengan PO", ok))
    if not ok:
        issues.append(f"R3 HS code tidak konsisten: {hs_docs} vs PO {po['kode_hs']}")
    ok = not bl or not vessel_name or bl.get("vessel", "").strip() == vessel_name
    checks.append(("R4 nama kapal B/L = arrival notice", ok))
    if not ok:
        issues.append(f"R4 kapal B/L '{bl.get('vessel')}' != '{vessel_name}'")
    cons = [f.get("consignee", "").strip() for f in present.values() if f.get("consignee")]
    ok = all(c.startswith("PT ImportExport Corp") for c in cons)
    checks.append(("R5 consignee = PT ImportExport Corp", ok))
    if not ok:
        issues.append(f"R5 consignee tidak sesuai: {set(cons)}")
    val = inv.get("total_value_usd")
    ok = val is not None and abs(val - float(po["nilai_usd"])) <= 0.05 * float(po["nilai_usd"])
    checks.append(("R6 nilai invoice sesuai PO (±5%)", ok))
    if not ok and inv:
        issues.append(f"R6 nilai invoice {val} vs PO {po['nilai_usd']}")
    if pl.get("gross_weight_kg") and bl.get("gross_weight_kg") and pl["gross_weight_kg"] != bl["gross_weight_kg"]:
        diff = abs(pl["gross_weight_kg"] - bl["gross_weight_kg"]) / bl["gross_weight_kg"]
        (warnings if diff <= 0.02 else issues).append(
            f"R7 berat PL {pl['gross_weight_kg']} vs B/L {bl['gross_weight_kg']} (selisih {diff:.1%})")
        checks.append(("R7 berat PL ~ B/L (toleransi 2%)", diff <= 0.02))
    score = sum(1 for _, ok in checks if ok) / len(checks)
    return {"status": "COMPLETE" if not issues else "INCOMPLETE", "score": round(score, 3), "required": required,
            "present": sorted(present), "missing": missing, "issues": issues, "warnings": warnings,
            "checks": [{"rule": r, "passed": ok} for r, ok in checks],
            "consistency": round(1.0 - 0.15 * len(warnings) - 0.25 * len([i for i in issues if not i.startswith("R1")]), 3)}

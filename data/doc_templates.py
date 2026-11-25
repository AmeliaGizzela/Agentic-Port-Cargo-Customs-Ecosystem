"""Template dokumen perdagangan sintetis (teks) untuk Document NLP ImportExport Corp."""
from __future__ import annotations

import random

DOC_TYPES = ["INVOICE", "PACKING_LIST", "BILL_OF_LADING", "CERT_ORIGIN", "IMPORT_PERMIT", "INSURANCE"]

_HEADERS = {
    "INVOICE": ["COMMERCIAL INVOICE", "INVOICE", "FAKTUR KOMERSIAL", "PROFORMA / COMMERCIAL INVOICE"],
    "PACKING_LIST": ["PACKING LIST", "DAFTAR KEMASAN", "PACKING / WEIGHT LIST"],
    "BILL_OF_LADING": ["BILL OF LADING", "OCEAN BILL OF LADING", "B/L - COMBINED TRANSPORT"],
    "CERT_ORIGIN": ["CERTIFICATE OF ORIGIN", "SURAT KETERANGAN ASAL", "FORM E / COO"],
    "IMPORT_PERMIT": ["PERSETUJUAN IMPOR", "IMPORT PERMIT", "IZIN IMPOR (LARTAS)"],
    "INSURANCE": ["CERTIFICATE OF INSURANCE", "MARINE CARGO INSURANCE", "POLIS ASURANSI PENGANGKUTAN"],
}


def _money(v: float) -> str:
    return f"{v:,.2f}"


def render(doc_type: str, f: dict, rng: random.Random | None = None, header: bool = True) -> str:
    """f: dict field (inv_no, supplier, consignee, hs, desc, qty, unit, value_usd, weight_kg, containers,
    vessel, voyage, bl_no, origin, pol, fta, permit_no, date)."""
    rng = rng or random.Random(0)
    h = rng.choice(_HEADERS[doc_type]) if header else ""
    lines: list[str] = []
    if doc_type == "INVOICE":
        lines = [h, f"Invoice No: {f['inv_no']}", f"Date: {f['date']}", f"Seller / Exporter: {f['supplier']}",
                 f"Buyer / Consignee: {f['consignee']}", f"Description of Goods: {f['desc']}",
                 f"HS Code: {f['hs']}", f"Quantity: {f['qty']:,} {f['unit']}",
                 f"Unit Price: USD {_money(f['value_usd'] / max(f['qty'], 1))}",
                 f"Total Amount: USD {_money(f['value_usd'])}", f"Incoterms: {rng.choice(['CIF', 'CFR', 'FOB'])} Nusa Raya",
                 f"Country of Origin: {f['origin']}", "Payment Terms: L/C at sight", "Amount due for payment as invoiced."]
    elif doc_type == "PACKING_LIST":
        lines = [h, f"Ref. Invoice No: {f['inv_no']}", f"Shipper: {f['supplier']}", f"Consignee: {f['consignee']}",
                 f"Goods: {f['desc']}", f"Quantity: {f['qty']:,} {f['unit']}",
                 f"Packages: {f['containers']} x 40' HC containers",
                 f"Gross Weight: {f['weight_kg']:,} kg", f"Net Weight: {int(f['weight_kg'] * 0.96):,} kg",
                 f"Measurement: {f['containers'] * 67} CBM", "Marks & Numbers: AS PER ATTACHED SHEET",
                 "Each pallet shrink-wrapped and labelled with carton count."]
    elif doc_type == "BILL_OF_LADING":
        lines = [h, f"B/L No: {f['bl_no']}", f"Shipper: {f['supplier']}", f"Consignee: {f['consignee']}",
                 "Notify Party: SAME AS CONSIGNEE", f"Vessel: {f['vessel']}  Voyage: {f['voyage']}",
                 f"Port of Loading: {f['pol']}", "Port of Discharge: Nusa Raya, Indonesia",
                 f"Number of Containers: {f['containers']}", f"Gross Weight: {f['weight_kg']:,} kg",
                 "Freight: PREPAID", "SHIPPED ON BOARD in apparent good order and condition.",
                 "Container Nos: " + ", ".join(f"OCLU{rng.randint(1000000, 9999999)}" for _ in range(min(3, f['containers'])))]
    elif doc_type == "CERT_ORIGIN":
        lines = [h, f"Reference No: COO-{f['inv_no'][-6:]}", f"Exporter: {f['supplier']}", f"Consignee: {f['consignee']}",
                 f"Scheme: {f.get('fta') or 'NON-PREFERENTIAL'}", f"HS Code: {f['hs']}", f"Description: {f['desc']}",
                 f"Country of Origin: {f['origin']}", "Origin Criterion: WO / RVC 40%",
                 f"Invoice No: {f['inv_no']}", "Certified by issuing authority of the exporting country."]
    elif doc_type == "IMPORT_PERMIT":
        lines = [h, f"Nomor: {f['permit_no']}", "Kementerian Perdagangan Republik Indonesia (fiktif)",
                 f"Diberikan kepada: {f['consignee']}", f"Pos Tarif / HS: {f['hs']}", f"Uraian Barang: {f['desc']}",
                 f"Jumlah Kuota: {f['qty']:,} {f['unit']}", f"Negara Asal: {f['origin']}",
                 "Masa berlaku: 31/12/2026", "Persetujuan impor ini berlaku untuk pemasukan melalui pelabuhan tujuan."]
    elif doc_type == "INSURANCE":
        lines = [h, f"Policy No: MCI-{f['inv_no'][-6:]}", f"Insured: {f['consignee']}",
                 f"Insured Value: USD {_money(f['value_usd'] * 1.1)}", f"Conveyance: {f['vessel']}",
                 f"Voyage: {f['pol']} to Nusa Raya", "Conditions: Institute Cargo Clauses (A)",
                 f"Subject matter insured: {f['desc']}"]
    body = [ln for ln in lines if ln]
    if rng.random() < 0.3 and len(body) > 4:  # variasi urutan baris (kecuali header)
        head, rest = body[:1], body[1:]
        rng.shuffle(rest)
        body = head + rest
    return "\n".join(body)


def random_fields(rng: random.Random) -> dict:
    goods = [("8471.30", "Portable computers", "pcs"), ("3004.90", "Medicaments", "boxes"),
             ("0402.21", "Milk powder", "kg"), ("8708.29", "Body parts", "pcs"), ("6403.99", "Footwear", "pairs"),
             ("9503.00", "Toys", "ctn"), ("2710.19", "Lubricating oil", "litres"), ("3901.10", "Polyethylene", "kg")]
    hs, desc, unit = rng.choice(goods)
    origin = rng.choice(["China", "India", "Vietnam", "Japan", "South Korea", "Thailand", "Germany", "USA"])
    n = rng.randint(1, 12)
    return {
        "inv_no": f"INV-{rng.randint(10, 99)}-{rng.randint(100000, 999999)}",
        "supplier": rng.choice(["Alpha Trading Ltd", "Orient Export Co", "Delta Manufacturing", "Global Parts GmbH",
                                "Sunrise Industrial Pvt", "Kanto Supplies K.K."]),
        "consignee": rng.choice(["PT ImportExport Corp", "PT Mitra Retail", "PT Sinar Abadi", "CV Maju Bersama"]),
        "hs": hs, "desc": desc, "qty": rng.randint(100, 200000), "unit": unit,
        "value_usd": float(rng.randint(10000, 900000)), "weight_kg": rng.randint(5000, 200000), "containers": n,
        "vessel": rng.choice(["MV Ocean Pioneer", "MV Ocean Mercury", "MV Selat Biru", "MV Lintas Timur"]),
        "voyage": f"{rng.randint(10, 99)}{rng.choice('ENSW')}", "bl_no": f"OL{rng.randint(10000000, 99999999)}",
        "origin": origin, "pol": rng.choice(["Shanghai", "Chennai", "Busan", "Hamburg", "Laem Chabang", "Haiphong"]),
        "fta": rng.choice(["ACFTA Form E", "AIFTA", "AKFTA", "ATIGA", "IJEPA", None]),
        "permit_no": f"PI-{rng.randint(10, 99)}.{rng.randint(1000, 9999)}/2026", "date": f"{rng.randint(1, 28):02d}/10/2026",
    }


def training_corpus(n_per_type: int = 120, seed: int = 7) -> list[tuple[str, str]]:
    rng = random.Random(seed)
    out = []
    for dt in DOC_TYPES:
        for _ in range(n_per_type):
            f = random_fields(rng)
            # 35% tanpa header agar model belajar dari isi dokumen, bukan judul saja
            text = render(dt, f, rng, header=rng.random() > 0.35)
            lines = text.split("\n")
            r = rng.random()
            if r < 0.3:  # fragmen pendek (mis. potongan halaman / lampiran) -> kasus sulit
                lines = rng.sample(lines, rng.randint(1, 3))
            elif r < 0.7:  # dokumen terpotong / hasil scan sebagian
                keep = max(2, int(len(lines) * rng.uniform(0.25, 0.6)))
                lines = rng.sample(lines, keep)
            text = "\n".join(lines)
            if rng.random() < 0.4:  # derau OCR: karakter tertukar
                chars = list(text)
                for i in rng.sample(range(len(chars)), max(1, len(chars) // 12)):
                    chars[i] = rng.choice("0O1lI5S|.,: ")
                text = "".join(chars)
            out.append((text, dt))
    rng.shuffle(out)
    return out

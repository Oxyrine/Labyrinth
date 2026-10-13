"""Extraction accuracy: compare what the extractor read from each generated PDF/scan with the world's ground truth.

Reads only files: `extractions.jsonl` (pipeline output) and `invoices.json` (ground truth).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCALAR_FIELDS = (
    "invoice_no", "invoice_date", "due_date", "financial_year", "seller_gstin", "buyer_gstin", "place_of_supply_state",
    "payment_terms_days", "po_id", "contract_id", "subtotal_paise", "cgst_paise", "sgst_paise", "igst_paise",
    "total_tax_paise", "grand_total_paise",
)
LINE_FIELDS = ("description", "hsn_sac", "qty", "unit", "rate_paise", "taxable_paise", "tax_paise", "total_paise")


def _lines(inv: dict[str, Any]) -> list[tuple[Any, ...]]:
    return [tuple(li[k] for k in LINE_FIELDS) for li in inv.get("line_items", [])]


def evaluate_extraction(regime_dir: Path) -> dict[str, Any] | None:
    path = regime_dir / "extractions.jsonl"
    truth_path = regime_dir / "invoices.json"
    if not path.is_file() or not truth_path.is_file():
        return None
    truth = {i["invoice_no"]: i for i in json.loads(truth_path.read_text(encoding="utf-8"))}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    by_layout: dict[str, dict[str, int]] = {}
    field_hits = {k: 0 for k in (*SCALAR_FIELDS, "line_items")}
    field_n = 0
    unreadable: dict[str, int] = {}
    low_conf = 0
    for r in rows:
        key = r.get("layout") if r["kind"] == "pdf" else "scan"
        b = by_layout.setdefault(key or "unrecognised", {"n": 0, "read": 0, "exact": 0})
        b["n"] += 1
        if r["status"] != "ok":
            reason = (r.get("missing") or ["unreadable"])[0]
            unreadable[reason] = unreadable.get(reason, 0) + 1
            continue
        b["read"] += 1
        got, want = r["invoice"], truth.get(r["invoice"]["invoice_no"])
        if want is None:
            continue
        field_n += 1
        ok_all = True
        for k in SCALAR_FIELDS:
            hit = got.get(k) == want.get(k)
            field_hits[k] += hit
            ok_all &= hit
        hit_lines = _lines(got) == _lines(want)
        field_hits["line_items"] += hit_lines
        ok_all &= hit_lines
        b["exact"] += ok_all
        low_conf += r.get("min_confidence", 1.0) < 0.9
    return {
        "documents": len(rows),
        "read": sum(b["read"] for b in by_layout.values()),
        "exact_matches": sum(b["exact"] for b in by_layout.values()),
        "by_layout": by_layout,
        "field_accuracy": [{"field": k, "accuracy": round(v / field_n, 4) if field_n else None} for k, v in field_hits.items()],
        "unreadable_reasons": unreadable,
        "read_with_confidence_below_0_9": low_conf,
    }

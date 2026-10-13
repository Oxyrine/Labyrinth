"""Layout fingerprinting and parsing of the visible text of GST invoices."""

from __future__ import annotations

from typing import Any

from contract.models import FieldProvenance, Invoice
from extract.layouts.common import parse_money_to_paise, render_invoice_pdf
from extract.layouts.visible import ExtractionError, parse_visible


def detect_layout(text: str) -> str:
    """Fingerprint the visible text into `tabular_gst`, `letterhead`, `tally`, or `generic` (unrecognised)."""
    upper = (text or "").upper()
    if "TALLY ERP VOUCHER" in upper or "VOUCHER NO:" in upper:
        return "tally"
    if "BILLED TO:" in upper and "COMMERCIAL" in upper:
        return "letterhead"
    if "(GOODS & SERVICES TAX)" in upper or "RECIPIENT (BILL TO)" in upper:
        return "tabular_gst"
    return "generic"


def parse_invoice_text(
    pages_text: list[str],
    layout: str | None = None,
    *,
    ocr: bool = False,
) -> tuple[Invoice, dict[str, FieldProvenance], str, dict[str, Any]]:
    """Detect the layout and parse the pages into `(Invoice, provenance, layout, extras)`."""
    detected = layout or detect_layout("\n".join(pages_text))
    invoice, prov, extras = parse_visible(pages_text, detected, ocr=ocr)
    return invoice, prov, detected, extras


__all__ = ["ExtractionError", "detect_layout", "parse_invoice_text", "parse_money_to_paise", "parse_visible", "render_invoice_pdf"]

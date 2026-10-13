"""Shared helpers for invoice layouts: money parsing and a test-helper PDF renderer."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from contract.models import Invoice, Party


def parse_money_to_paise(raw: str) -> int:
    """Deterministically parse a Rupee or paise string into strict integer paise without floats."""
    s = raw.strip()
    if not s:
        return 0
    if s.lower().endswith("paise"):
        digits = re.sub(r"[^0-9-]", "", s)
        return int(digits) if digits else 0
    cleaned = re.sub(r"[₹$,\s]|Rs\.?|INR", "", s, flags=re.IGNORECASE)
    if not cleaned:
        return 0
    sign = -1 if cleaned.startswith("-") else 1
    cleaned = cleaned.lstrip("+-")
    if "." in cleaned:
        whole_str, frac_str = cleaned.split(".", 1)
        whole_digits = re.sub(r"[^0-9]", "", whole_str)
        frac_digits = re.sub(r"[^0-9]", "", frac_str)
        whole = int(whole_digits) if whole_digits else 0
        frac_padded = (frac_digits + "00")[:2]
        return sign * (whole * 100 + int(frac_padded))
    digits_only = re.sub(r"[^0-9]", "", cleaned)
    return sign * (int(digits_only) * 100) if digits_only else 0


def _stub_party(party_id: str, gstin: str, label: str) -> Party:
    from contract.models import IndustryClass

    return Party(
        party_id=party_id if party_id.startswith("P-") else "P-STUB", name=f"{label} {gstin[2:12]} Pvt Ltd", gstin=gstin,
        pan=gstin[2:12], state_code=gstin[:2], industry_class=IndustryClass.TRADING, address="Industrial Area",
        pincode="400001", city="Mumbai", phone="+91-0000000000", bank_account_hash="0" * 64,
    )


def render_invoice_pdf(
    invoice: Invoice,
    layout: str = "tabular_gst",
    *,
    seller: Party | None = None,
    buyer: Party | None = None,
    noisy_scan: bool = False,
    prompt_injection_text: str | None = None,
    tamper_incremental: bool = False,
    mod_date_after: bool = False,
    producer_mismatch: bool = False,
) -> bytes:
    """Render a deterministic PDF of an `Invoice` (test helper; the visible layouts live in `data.generator`)."""
    from data.generator.pdfs import render_invoice_pdf_bytes
    from data.generator.scans import render_noisy_scan_pdf_bytes

    seller = seller or _stub_party(invoice.seller_id, invoice.seller_gstin, "Seller")
    buyer = buyer or _stub_party(invoice.buyer_id, invoice.buyer_gstin, "Buyer")
    if noisy_scan:
        return render_noisy_scan_pdf_bytes(invoice, seller, buyer, seed=2601)
    pdf_bytes = render_invoice_pdf_bytes(invoice, seller, buyer, layout=layout, remarks=prompt_injection_text)  # type: ignore[arg-type]

    if mod_date_after or producer_mismatch:
        creator_str = "Labyrinth ReportLab Invariant v1"
        producer_str = "Adobe Acrobat Pro PostEdit 24.0" if producer_mismatch else creator_str
        try:
            inv_dt = datetime.strptime(invoice.invoice_date, "%Y-%m-%d")
        except ValueError:
            inv_dt = datetime(2026, 8, 12)
        mod_dt_str = (
            (inv_dt + timedelta(days=30)).strftime("D:%Y%m%d120000Z") if mod_date_after else inv_dt.strftime("D:%Y%m%d090000Z")
        )
        override = (
            f"\n% Metadata Info Override\n"
            f"999 0 obj\n<< /Creator ({creator_str}) /Producer ({producer_str}) /ModDate ({mod_dt_str}) >>\nendobj\n"
        ).encode("latin-1")
        pdf_bytes = re.sub(rb"/ModDate\s*\([^)]*\)", f"/ModDate ({mod_dt_str})".encode("latin-1"), pdf_bytes)
        if producer_mismatch:
            pdf_bytes = re.sub(rb"/Producer\s*\([^)]*\)", f"/Producer ({producer_str})".encode("latin-1"), pdf_bytes)
        if b"/ModDate" not in pdf_bytes:
            pdf_bytes = pdf_bytes.replace(b"%%EOF", override + b"%%EOF", 1)

    if tamper_incremental:
        pdf_bytes += b"\n% Incremental update revision\nxref\n0 1\n0000000000 65535 f \ntrailer\n<< /Size 1 >>\nstartxref\n0\n%%EOF\n"
    return pdf_bytes

"""End-to-end PDF invoice extraction (`extract/pipeline.py`).

Order: pypdf text layer -> OCR (image-only scans, needs Tesseract) -> layout fingerprint -> visible-text parser
-> optional schema-bound Claude fallback (off unless `LABYRINTH_ALLOW_EXTERNAL_LLM=1`) -> deterministic validators.
Nothing is invented: an unreadable document raises `ExtractionError` and goes to a human.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import shutil
from dataclasses import dataclass, field
from typing import Any

from PIL import Image
from pypdf import PdfReader

from contract.models import (
    CheckResult,
    CheckSource,
    FieldProvenance,
    Invoice,
    InvoiceVersion,
    VersionKind,
    canonical_sha256,
)
from extract.layouts import ExtractionError, parse_invoice_text
from extract.llm import call_llm_fallback, detect_prompt_injection, is_external_llm_enabled, sanitize_untrusted_text
from extract.tamper import extract_tamper_hints, inspect_pdf_tamper
from extract.validators import run_invoice_validators

DEMO_HMAC_KEY = b"tessera-consortium-demo-hmac-key-v1"


def compute_invoice_identity(gstin: str, fy: str, doc_type: str, invoice_no: str) -> str:
    """HMAC-SHA256 invoice identity (supplier GSTIN | FY | document type | invoice number)."""
    norm_inv_no = " ".join(invoice_no.strip().upper().split())
    norm = "|".join(part.strip().upper() for part in (gstin, fy, doc_type, norm_inv_no))
    return hmac.new(DEMO_HMAC_KEY, norm.encode("utf-8"), hashlib.sha256).hexdigest()


def derive_invoice_id(seller_gstin: str, financial_year: str, invoice_no: str) -> str:
    """Platform-assigned id: the same (supplier, FY, number) always maps to the same id."""
    norm = "|".join(p.strip().upper() for p in (seller_gstin, financial_year, " ".join(invoice_no.split())))
    return "INV-" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12].upper()


@dataclass
class ExtractionResult:
    invoice: Invoice
    version: InvoiceVersion
    checks: list[CheckResult]
    extras: dict[str, Any] = field(default_factory=dict)
    method: str = "text_layer"


def ocr_available() -> bool:
    try:
        import pytesseract  # type: ignore[import-not-found]  # noqa: F401
    except Exception:
        return False
    return shutil.which("tesseract") is not None


def _ocr_pages(reader: PdfReader) -> list[str]:
    """OCR the embedded page images. Raises `ExtractionError` when no OCR engine is installed."""
    if not ocr_available():
        raise ExtractionError("Scanned document: no text layer and the OCR engine (Tesseract) is not installed.", ["ocr_engine"])
    import pytesseract  # type: ignore[import-not-found]

    pages: list[str] = []
    for page in reader.pages:
        chunks = []
        for img_file in getattr(page, "images", []):
            chunks.append(pytesseract.image_to_string(Image.open(io.BytesIO(img_file.data))))
        pages.append("\n".join(chunks))
    if not any(p.strip() for p in pages):
        raise ExtractionError("Scanned document: OCR returned no text.", ["ocr_text"])
    return pages


def extract_document(
    file_bytes: bytes,
    *,
    version_id: str | None = None,
    version_no: int = 1,
    kind: VersionKind = VersionKind.INITIAL,
    created_at: str = "2026-08-12T10:00:00Z",
    source: CheckSource = CheckSource.LIVE,
    layout_hint: str | None = None,
    parties_by_gstin: dict[str, str] | None = None,
) -> ExtractionResult:
    """Extract an `Invoice` (+ version, validator checks, printed references) from PDF bytes."""
    try:
        reader = PdfReader(io.BytesIO(file_bytes), strict=False)
        pages = [(p.extract_text() or "") for p in reader.pages]
    except Exception as exc:
        raise ExtractionError(f"Unreadable PDF ({type(exc).__name__}).", ["pdf"]) from exc

    ocr = False
    if not any(p.strip() for p in pages):
        pages, ocr = _ocr_pages(reader), True

    raw_text = "\n".join(pages)
    injection_hits = detect_prompt_injection(raw_text)
    clean_pages = [sanitize_untrusted_text(p) for p in pages]

    method = "ocr" if ocr else "text_layer"
    try:
        invoice, provenance, layout, extras = parse_invoice_text(clean_pages, layout_hint, ocr=ocr)
        extras = {**extras, "layout": layout}
    except ExtractionError:
        if not is_external_llm_enabled():
            raise
        llm_inv, audit = call_llm_fallback("\n".join(clean_pages))
        if llm_inv is None:
            raise
        invoice, extras, method = llm_inv, {}, "llm_fallback"
        provenance = {"invoice_no": FieldProvenance(value=invoice.invoice_no, confidence=0.6, page=1)}
        injection_hits.append(f"LLM fallback used (model {audit['model_id']}, prompt {audit['prompt_hash'][:12]})")

    by_gstin = parties_by_gstin or {}
    invoice = invoice.model_copy(update={
        "invoice_id": derive_invoice_id(invoice.seller_gstin, invoice.financial_year, invoice.invoice_no),
        "seller_id": by_gstin.get(invoice.seller_gstin, f"P-{invoice.seller_gstin[2:12]}"),
        "buyer_id": by_gstin.get(invoice.buyer_gstin, f"P-{invoice.buyer_gstin[2:12]}"),
        "linked_invoice_id": (
            derive_invoice_id(invoice.seller_gstin, invoice.financial_year, extras["linked_invoice_no"])
            if extras.get("linked_invoice_no") else None
        ),
    })

    file_sha256 = hashlib.sha256(file_bytes).hexdigest()
    tamper_hints, _ = extract_tamper_hints(file_bytes, invoice.invoice_date)
    extras = {**extras, "ignored_instruction_text": injection_hits}

    version = InvoiceVersion(
        version_id=version_id or f"V-{invoice.invoice_id.removeprefix('INV-')}-{version_no}",
        invoice_id=invoice.invoice_id, version_no=version_no, kind=kind,
        invoice_identity=compute_invoice_identity(invoice.seller_gstin, invoice.financial_year, invoice.doc_type.value, invoice.invoice_no),
        file_sha256=file_sha256,
        field_root=canonical_sha256(invoice),  # provisional commitment; the audit module replaces it with the salted Merkle root
        field_provenance=provenance, tamper_hints=tamper_hints, created_at=created_at,
    )
    checks = run_invoice_validators(invoice, source=source)
    checks.append(inspect_pdf_tamper(file_bytes=file_bytes, invoice_date=invoice.invoice_date, tamper_hints=tamper_hints, source=source, invoice_id=invoice.invoice_id))
    return ExtractionResult(invoice, version, checks, extras, method)


def extract(file_bytes: bytes, **kwargs: Any) -> tuple[Invoice, InvoiceVersion, list[CheckResult]]:
    """`(Invoice, InvoiceVersion, checks)` view of `extract_document`."""
    r = extract_document(file_bytes, **kwargs)
    return r.invoice, r.version, r.checks


extract_invoice_pdf = extract


"""Tessera Stage 1 — Document Extraction & Deterministic Consistency Validators (`extract/`)."""

from __future__ import annotations

from extract.check import run
from extract.gstin import validate_gstin_checksum, validate_gstin_format
from extract.layouts import ExtractionError, detect_layout, parse_invoice_text, render_invoice_pdf
from extract.llm import (
    PINNED_MODEL_ID,
    build_delimited_prompt,
    call_llm_fallback,
    detect_prompt_injection,
    is_external_llm_enabled,
    sanitize_untrusted_text,
)
from extract.pipeline import (
    ExtractionResult,
    compute_invoice_identity,
    derive_invoice_id,
    extract,
    extract_document,
    extract_invoice_pdf,
    ocr_available,
)
from extract.tamper import extract_tamper_hints, inspect_pdf_tamper
from extract.validators import (
    check_arith_lines,
    check_arith_total,
    check_date_valid,
    check_gstin_checksum,
    check_gstin_format,
    check_hsn_sac,
    check_tax_regime,
    run_invoice_validators,
)

__all__ = [
    "ExtractionError",
    "ExtractionResult",
    "derive_invoice_id",
    "extract_document",
    "ocr_available",
    "PINNED_MODEL_ID",
    "build_delimited_prompt",
    "call_llm_fallback",
    "check_arith_lines",
    "check_arith_total",
    "check_date_valid",
    "check_gstin_checksum",
    "check_gstin_format",
    "check_hsn_sac",
    "check_tax_regime",
    "compute_invoice_identity",
    "detect_layout",
    "detect_prompt_injection",
    "extract",
    "extract_invoice_pdf",
    "extract_tamper_hints",
    "inspect_pdf_tamper",
    "is_external_llm_enabled",
    "parse_invoice_text",
    "render_invoice_pdf",
    "run",
    "run_invoice_validators",
    "sanitize_untrusted_text",
    "validate_gstin_checksum",
    "validate_gstin_format",
]

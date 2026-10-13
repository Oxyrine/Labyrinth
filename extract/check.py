"""Seam entrypoint `extract/check.py:run(ctx: CaseContext) -> list[CheckResult]`."""

from __future__ import annotations

import os
from pathlib import Path

from contract.context import CaseContext
from contract.models import CheckResult, CheckSource
from extract.tamper import inspect_pdf_tamper
from extract.validators import run_invoice_validators


def _resolve_check_source() -> CheckSource:
    live_flag = os.environ.get("LABYRINTH_EXTRACT_LIVE", "1").strip()
    return CheckSource.LIVE if live_flag == "1" else CheckSource.FIXTURE


def _extract_pdf_bytes_from_context(ctx: CaseContext) -> bytes | None:
    """Retrieve optional raw PDF bytes or PDF file path from `ctx.history.case_metadata()`."""
    if ctx.history is None or not hasattr(ctx.history, "case_metadata"):
        return None
    meta = ctx.history.case_metadata() or {}
    raw_bytes = meta.get("pdf_bytes")
    if isinstance(raw_bytes, bytes):
        return raw_bytes
    if isinstance(raw_bytes, str) and raw_bytes:
        try:
            return bytes.fromhex(raw_bytes)
        except ValueError:
            return raw_bytes.encode("latin-1", errors="ignore")

    pdf_path = meta.get("pdf_path")
    if isinstance(pdf_path, str) and pdf_path:
        p = Path(pdf_path)
        if p.is_file():
            return p.read_bytes()
    return None


def run(ctx: CaseContext) -> list[CheckResult]:
    """Run all 8 Stage 1 (`extract.*`) checks against `ctx: CaseContext`."""
    source = _resolve_check_source()
    checks = run_invoice_validators(ctx.invoice, source=source)

    pdf_bytes = _extract_pdf_bytes_from_context(ctx)
    tamper_check = inspect_pdf_tamper(
        file_bytes=pdf_bytes,
        invoice_date=ctx.invoice.invoice_date,
        tamper_hints=list(ctx.version.tamper_hints),
        source=source,
        invoice_id=ctx.invoice.invoice_id,
    )
    checks.append(tamper_check)
    return checks

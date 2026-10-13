"""PDF metadata and incremental-save tamper inspection (`extract.tamper_metadata`)."""

from __future__ import annotations

import io
import re
from typing import Any

from pypdf import PdfReader

from contract.models import CheckResult, CheckSource, CheckStatus, Severity

PDF_DATE_RE = re.compile(r"(?:D:)?(\d{4})[-]?(\d{2})[-]?(\d{2})")


def _parse_pdf_date(raw: Any) -> str | None:
    """Extract YYYY-MM-DD from PDF /ModDate or /CreationDate string or datetime."""
    if raw is None:
        return None
    if hasattr(raw, "strftime"):
        return raw.strftime("%Y-%m-%d")
    s = str(raw).strip()
    m = PDF_DATE_RE.search(s)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def _same_tool_family(creator: str, producer: str) -> bool:
    """Return True if Creator and Producer belong to the same PDF tool family."""
    c = creator.strip().lower()
    p = producer.strip().lower()
    if not c or not p or c in ("none", "unspecified", "unknown") or p in ("none", "unspecified", "unknown"):
        return True
    if c == p or c in p or p in c:
        return True
    # Compare primary family token (letters only)
    c_tokens = [tok for tok in re.split(r"[^a-z0-9]+", c) if len(tok) >= 4]
    p_tokens = [tok for tok in re.split(r"[^a-z0-9]+", p) if len(tok) >= 4]
    if c_tokens and p_tokens and c_tokens[0] == p_tokens[0]:
        return True
    return False


def extract_tamper_hints(
    file_bytes: bytes | None,
    invoice_date: str,
    existing_hints: list[str] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Inspect PDF metadata and raw byte stream for tamper indicators."""
    hints: list[str] = []
    if existing_hints:
        for h in existing_hints:
            if h and h.strip() and h.strip() not in hints:
                hints.append(h.strip())

    details: dict[str, Any] = {
        "eof_count": 0,
        "creator": "",
        "producer": "",
        "mod_date": "",
    }

    if not file_bytes:
        details["tamper_hint_count"] = len(hints)
        return hints, details

    eof_count = file_bytes.count(b"%%EOF")
    details["eof_count"] = eof_count

    creator_str = ""
    producer_str = ""
    mod_date_iso: str | None = None

    try:
        reader = PdfReader(io.BytesIO(file_bytes), strict=False)
        meta = reader.metadata or {}
        raw_creator = meta.get("/Creator") or getattr(meta, "creator", None)
        raw_producer = meta.get("/Producer") or getattr(meta, "producer", None)
        raw_mod_date = meta.get("/ModDate") or getattr(meta, "modification_date", None)

        if raw_creator:
            creator_str = str(raw_creator).strip()
        if raw_producer:
            producer_str = str(raw_producer).strip()
        mod_date_iso = _parse_pdf_date(raw_mod_date)
    except Exception:
        pass

    # Also inspect raw PDF Info dictionary entries (catches incremental/trailer overrides)
    for m_mod in re.finditer(rb"/ModDate\s*\(([^)]+)\)", file_bytes):
        candidate = _parse_pdf_date(m_mod.group(1).decode("latin-1", errors="ignore"))
        if candidate and (mod_date_iso is None or candidate > mod_date_iso):
            mod_date_iso = candidate
    for m_cre in re.finditer(rb"/Creator\s*\(([^)]+)\)", file_bytes):
        val = m_cre.group(1).decode("latin-1", errors="ignore").strip()
        if val and val.lower() != "unspecified":
            creator_str = val
    for m_prod in re.finditer(rb"/Producer\s*\(([^)]+)\)", file_bytes):
        val = m_prod.group(1).decode("latin-1", errors="ignore").strip()
        if val and val.lower() != "unspecified":
            producer_str = val

    details["creator"] = creator_str
    details["producer"] = producer_str
    details["mod_date"] = mod_date_iso or ""

    if mod_date_iso and invoice_date and mod_date_iso > invoice_date:
        msg = f"PDF ModDate {mod_date_iso} is later than printed invoice date {invoice_date}."
        if msg not in hints:
            hints.append(msg)

    if creator_str and producer_str and not _same_tool_family(creator_str, producer_str):
        msg = (
            f"PDF Producer '{producer_str}' mismatches Creator '{creator_str}', "
            f"indicating post-generation editing."
        )
        if msg not in hints:
            hints.append(msg)

    if eof_count > 1:
        msg = (
            f"PDF stream contains {eof_count} '%%EOF' markers ({eof_count - 1} incremental save(s) detected)."
        )
        if msg not in hints:
            hints.append(msg)

    details["tamper_hint_count"] = len(hints)
    return hints, details


def inspect_pdf_tamper(
    file_bytes: bytes | None,
    invoice_date: str,
    tamper_hints: list[str] | None = None,
    source: CheckSource = CheckSource.LIVE,
    invoice_id: str | None = None,
) -> CheckResult:
    """Run `extract.tamper_metadata` check.

    Status is at most `CheckStatus.WARN` (never `CheckStatus.FAIL`).
    """
    hints, details = extract_tamper_hints(
        file_bytes=file_bytes,
        invoice_date=invoice_date,
        existing_hints=tamper_hints,
    )
    refs = [invoice_id] if invoice_id else []

    if hints:
        return CheckResult(
            check="extract.tamper_metadata",
            status=CheckStatus.WARN,
            severity=Severity.MEDIUM,
            source=source,
            evidence=hints,
            refs=refs,
            details=details,
        )

    eof_count = details.get("eof_count", 0)
    if eof_count > 0:
        clean_msg = (
            f"PDF metadata and byte stream show {eof_count} '%%EOF' marker and consistent creation "
            f"metadata on or before invoice date {invoice_date}."
        )
    else:
        clean_msg = (
            f"No PDF metadata anomalies or incremental-save tamper hints recorded for invoice dated {invoice_date}."
        )

    return CheckResult(
        check="extract.tamper_metadata",
        status=CheckStatus.PASS,
        severity=Severity.INFO,
        source=source,
        evidence=[clean_msg],
        refs=refs,
        details=details,
    )

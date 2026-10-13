"""Invoice identity HMAC computation and version classification (`dedup.identity`)."""

from __future__ import annotations

import hashlib
import hmac
import os
import re

from contract.context import CaseContext
from contract.models import (
    CheckResult,
    CheckSource,
    CheckStatus,
    DocType,
    Invoice,
    InvoiceVersion,
    Severity,
    VersionKind,
    format_inr,
)

DEFAULT_IDENTITY_KEY = b"tessera-consortium-identity-hmac-key-v1"


def normalize_identity_token(value: str) -> str:
    """Minimal normalization: trim, uppercase, and collapse internal whitespace."""
    return re.sub(r"\s+", " ", str(value).strip().upper())


def compute_invoice_identity(
    gstin: str,
    fy: str,
    doc_type: str | DocType,
    invoice_no: str,
    key: bytes | str | None = None,
) -> str:
    """Compute HMAC-SHA256 invoice identity over minimally normalized (gstin, fy, doc_type, invoice_no)."""
    if key is None:
        env_key = os.environ.get("LABYRINTH_IDENTITY_KEY", "")
        key_bytes = env_key.encode("utf-8") if env_key else DEFAULT_IDENTITY_KEY
    elif isinstance(key, str):
        key_bytes = key.encode("utf-8")
    else:
        key_bytes = key

    dtype_str = doc_type.value if isinstance(doc_type, DocType) else str(doc_type)
    n_gstin = normalize_identity_token(gstin)
    n_fy = normalize_identity_token(fy)
    n_dtype = normalize_identity_token(dtype_str)
    n_inv_no = normalize_identity_token(invoice_no)

    payload = f"{n_gstin}|{n_fy}|{n_dtype}|{n_inv_no}".encode("utf-8")
    return hmac.new(key_bytes, payload, hashlib.sha256).hexdigest()


def _get_dedup_source() -> CheckSource:
    val = os.environ.get("LABYRINTH_DEDUP_LIVE", "1").strip().lower()
    if val in ("0", "false", "fixture", "no"):
        return CheckSource.FIXTURE
    return CheckSource.LIVE


def diff_invoices(prior: Invoice, current: Invoice) -> list[str]:
    """Produce human-readable field-by-field diff sentences between two versions of an invoice."""
    diffs: list[str] = []

    if prior.grand_total_paise != current.grand_total_paise:
        diffs.append(
            f"Unexplained change: Grand total changed from {format_inr(prior.grand_total_paise)} "
            f"to {format_inr(current.grand_total_paise)} without a linked credit/debit note."
        )
    if prior.subtotal_paise != current.subtotal_paise:
        diffs.append(
            f"Unexplained change: Subtotal changed from {format_inr(prior.subtotal_paise)} "
            f"to {format_inr(current.subtotal_paise)} without a linked credit/debit note."
        )
    if prior.total_tax_paise != current.total_tax_paise:
        diffs.append(
            f"Unexplained change: Total tax changed from {format_inr(prior.total_tax_paise)} "
            f"to {format_inr(current.total_tax_paise)} without a linked credit/debit note."
        )
    if prior.invoice_date != current.invoice_date:
        diffs.append(
            f"Unexplained change: Invoice date changed from {prior.invoice_date} to {current.invoice_date}."
        )
    if prior.due_date != current.due_date:
        diffs.append(
            f"Unexplained change: Due date changed from {prior.due_date} to {current.due_date}."
        )
    if prior.buyer_id != current.buyer_id or prior.buyer_gstin != current.buyer_gstin:
        diffs.append(
            f"Unexplained change: Buyer changed from {prior.buyer_id} ({prior.buyer_gstin}) "
            f"to {current.buyer_id} ({current.buyer_gstin})."
        )
    if prior.payment_terms_days != current.payment_terms_days:
        diffs.append(
            f"Unexplained change: Payment terms changed from {prior.payment_terms_days} days "
            f"to {current.payment_terms_days} days."
        )

    max_lines = max(len(prior.line_items), len(current.line_items))
    for i in range(max_lines):
        if i >= len(prior.line_items):
            li = current.line_items[i]
            diffs.append(
                f"Unexplained change: Added line {li.line_no} ({li.hsn_sac}, qty {li.qty}, total {format_inr(li.total_paise)})."
            )
        elif i >= len(current.line_items):
            li = prior.line_items[i]
            diffs.append(
                f"Unexplained change: Removed line {li.line_no} ({li.hsn_sac}, qty {li.qty}, total {format_inr(li.total_paise)})."
            )
        else:
            p_li = prior.line_items[i]
            c_li = current.line_items[i]
            if p_li.qty != c_li.qty:
                diffs.append(
                    f"Unexplained change: Line {c_li.line_no} quantity changed from {p_li.qty} {p_li.unit} to {c_li.qty} {c_li.unit}."
                )
            if p_li.rate_paise != c_li.rate_paise:
                diffs.append(
                    f"Unexplained change: Line {c_li.line_no} unit rate changed from {format_inr(p_li.rate_paise)} to {format_inr(c_li.rate_paise)}."
                )
            if p_li.hsn_sac != c_li.hsn_sac:
                diffs.append(
                    f"Unexplained change: Line {c_li.line_no} HSN/SAC changed from {p_li.hsn_sac} to {c_li.hsn_sac}."
                )
            if p_li.description != c_li.description:
                diffs.append(
                    f"Unexplained change: Line {c_li.line_no} description changed from '{p_li.description}' to '{c_li.description}'."
                )

    return diffs


def classify_version(ctx: CaseContext) -> CheckResult:
    """Classify `(ctx.invoice, ctx.version)` against prior versions and emit `dedup.identity`."""
    check_id = "dedup.identity"
    source = _get_dedup_source()

    meta = (
        ctx.history.case_metadata()
        if hasattr(ctx.history, "case_metadata") and callable(ctx.history.case_metadata)
        else {}
    )
    unavailable = (meta or {}).get("unavailable_checks") or []
    if check_id in unavailable:
        return CheckResult(
            check=check_id,
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[f"Version history is unavailable to classify identity for invoice {ctx.invoice.invoice_id}."],
        )

    raw_priors: list[tuple[Invoice, InvoiceVersion]] = []
    if hasattr(ctx.history, "prior_versions") and callable(ctx.history.prior_versions):
        raw_priors = list(ctx.history.prior_versions(ctx.version.invoice_identity))

    # Exclude self when version_id and file_sha256 match initial version
    priors: list[tuple[Invoice, InvoiceVersion]] = []
    self_skipped = False
    for p_inv, p_ver in raw_priors:
        if (
            not self_skipped
            and p_ver.version_id == ctx.version.version_id
            and p_ver.file_sha256 == ctx.version.file_sha256
            and ctx.version.kind == VersionKind.INITIAL
        ):
            self_skipped = True
            continue
        priors.append((p_inv, p_ver))

    if ctx.version.kind == VersionKind.EXTRACTION_REVISION:
        refs = [p_ver.version_id for _, p_ver in priors] or [ctx.version.version_id]
        return CheckResult(
            check=check_id,
            status=CheckStatus.PASS,
            severity=Severity.INFO,
            source=source,
            evidence=[
                f"Extraction revision {ctx.version.version_id} for invoice {ctx.invoice.invoice_id} "
                f"on unchanged file SHA-256 {ctx.version.file_sha256[:16]}... logged with no duplicate or tamper flag."
            ],
            refs=refs,
            details={"classification": VersionKind.EXTRACTION_REVISION.value},
        )

    if not priors:
        if ctx.version.kind == VersionKind.UNEXPLAINED_CHANGE:
            return CheckResult(
                check=check_id,
                status=CheckStatus.WARN,
                severity=Severity.HIGH,
                source=source,
                evidence=[
                    f"Unexplained change detected on invoice {ctx.invoice.invoice_id} ({format_inr(ctx.invoice.grand_total_paise)}) without a linked credit/debit note."
                ],
                refs=[ctx.version.version_id],
                details={"classification": VersionKind.UNEXPLAINED_CHANGE.value},
            )
        return CheckResult(
            check=check_id,
            status=CheckStatus.PASS,
            severity=Severity.INFO,
            source=source,
            evidence=[
                f"Initial submission {ctx.version.version_id} for invoice {ctx.invoice.invoice_id} "
                f"(identity {ctx.version.invoice_identity[:16]}..., total {format_inr(ctx.invoice.grand_total_paise)})."
            ],
            refs=[ctx.version.version_id],
            details={"classification": VersionKind.INITIAL.value},
        )

    prior_inv, prior_ver = priors[-1]
    refs = [prior_inv.invoice_id, prior_ver.version_id]

    # Case 1: Same identity + same file_sha256 -> resubmission (PASS here; dedup.registry handles conflict)
    if prior_ver.file_sha256 == ctx.version.file_sha256:
        return CheckResult(
            check=check_id,
            status=CheckStatus.PASS,
            severity=Severity.INFO,
            source=source,
            evidence=[
                f"Resubmission of invoice {ctx.invoice.invoice_no} with identical file SHA-256 "
                f"{ctx.version.file_sha256[:16]}... matching prior version {prior_ver.version_id}; "
                f"active reservation conflict is enforced by consortium registry."
            ],
            refs=refs,
            details={"classification": VersionKind.RESUBMISSION.value, "prior_version_id": prior_ver.version_id},
        )

    # Case 2: Same identity + different file_sha256 -> check if linked amendment exists
    has_credit_note = False
    if hasattr(ctx.history, "credit_notes_for_invoice") and callable(ctx.history.credit_notes_for_invoice):
        if ctx.history.credit_notes_for_invoice(prior_inv.invoice_id) or ctx.history.credit_notes_for_invoice(
            ctx.invoice.invoice_id
        ):
            has_credit_note = True
    if any(d.doc_type in (DocType.CREDIT_NOTE, DocType.DEBIT_NOTE) for d in ctx.evidence):
        has_credit_note = True

    if ctx.invoice.linked_invoice_id is not None or has_credit_note:
        linked_ref = ctx.invoice.linked_invoice_id or prior_inv.invoice_id
        return CheckResult(
            check=check_id,
            status=CheckStatus.PASS,
            severity=Severity.INFO,
            source=source,
            evidence=[
                f"Amended version {ctx.version.version_id} for invoice {ctx.invoice.invoice_id} "
                f"({format_inr(ctx.invoice.grand_total_paise)}) is backed by linked amendment reference {linked_ref}."
            ],
            refs=refs,
            details={"classification": VersionKind.AMENDED.value, "prior_version_id": prior_ver.version_id},
        )

    # Case 3: Unexplained change -> WARN (Severity.HIGH) with concrete field-by-field diff
    diffs = diff_invoices(prior_inv, ctx.invoice)
    if not diffs:
        diffs = [
            f"Unexplained change: Source file SHA-256 changed from {prior_ver.file_sha256[:16]}... "
            f"to {ctx.version.file_sha256[:16]}... on invoice {ctx.invoice.invoice_no} "
            f"({format_inr(ctx.invoice.grand_total_paise)}) without a linked credit/debit note."
        ]

    return CheckResult(
        check=check_id,
        status=CheckStatus.WARN,
        severity=Severity.HIGH,
        source=source,
        evidence=diffs,
        refs=refs,
        details={
            "classification": VersionKind.UNEXPLAINED_CHANGE.value,
            "prior_version_id": prior_ver.version_id,
            "prior_invoice_id": prior_inv.invoice_id,
            "diff_count": len(diffs),
        },
    )

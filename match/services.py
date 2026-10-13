"""Service milestone and acceptance verification (`match.services.acceptance`)."""

from __future__ import annotations

from contract.context import CaseContext
from contract.models import CheckResult, CheckStatus, DocType, Severity, format_inr
from match.three_way import (
    derive_weight_and_status,
    get_case_evidence,
    get_case_metadata,
    get_check_source,
    is_check_unavailable,
)


def check_service_acceptance(ctx: CaseContext) -> CheckResult:
    """Check `match.services.acceptance`: requires SERVICE_ACCEPTANCE + contract/PO for service invoices."""
    check_id = "match.services.acceptance"
    source = get_check_source()

    has_service_lines = any(li.is_service for li in ctx.invoice.line_items)
    if not has_service_lines:
        return CheckResult(
            check=check_id,
            status=CheckStatus.NOT_APPLICABLE,
            severity=Severity.INFO,
            source=source,
            evidence=[
                f"Invoice {ctx.invoice.invoice_id} is a pure goods invoice; service acceptance verification is not applicable."
            ],
        )

    if is_check_unavailable(ctx, check_id):
        return CheckResult(
            check=check_id,
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[
                f"Service acceptance and contract documents are unavailable for service invoice {ctx.invoice.invoice_id}."
            ],
        )

    docs = get_case_evidence(ctx)
    sa_docs = [d for d in docs if d.doc_type == DocType.SERVICE_ACCEPTANCE]
    gov_docs = [d for d in docs if d.doc_type in (DocType.CONTRACT, DocType.PURCHASE_ORDER)]
    meta = get_case_metadata(ctx)

    if not sa_docs and not gov_docs and not meta.get("service_acceptance_confirmed_absent"):
        return CheckResult(
            check=check_id,
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[
                f"Neither service acceptance nor governing contract/PO is present in evidence for service invoice {ctx.invoice.invoice_id}."
            ],
        )

    if not sa_docs or not gov_docs:
        missing_parts: list[str] = []
        if not sa_docs:
            missing_parts.append("service acceptance record")
        if not gov_docs:
            missing_parts.append("governing contract or purchase order")
        refs = [d.doc_id for d in (sa_docs + gov_docs)]
        weight, v_status = derive_weight_and_status(sa_docs + gov_docs)
        return CheckResult(
            check=check_id,
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            source=source,
            evidence_weight=weight,
            verification_status=v_status,
            evidence=[
                f"Service invoice {ctx.invoice.invoice_id} for {format_inr(ctx.invoice.grand_total_paise)} is missing required {' and '.join(missing_parts)}."
            ],
            refs=refs,
        )

    all_docs = sa_docs + gov_docs
    weight, v_status = derive_weight_and_status(all_docs)
    refs = [d.doc_id for d in all_docs]
    sa = sa_docs[0]
    gov = gov_docs[0]
    milestone_note = f" (milestone {sa.milestone_id})" if sa.milestone_id else ""

    return CheckResult(
        check=check_id,
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        source=source,
        evidence_weight=weight,
        verification_status=v_status,
        evidence=[
            f"Service acceptance {sa.doc_id}{milestone_note} and {gov.doc_type.value} {gov.doc_id} verified for service invoice {ctx.invoice.invoice_id} ({format_inr(ctx.invoice.grand_total_paise)})."
        ],
        refs=refs,
    )

"""Chronological date ordering and validity window verification (`match.ordering.dates`)."""

from __future__ import annotations

from contract.context import CaseContext
from contract.models import CheckResult, CheckStatus, DocType, Severity
from match.three_way import (
    derive_weight_and_status,
    get_case_evidence,
    get_check_source,
    is_check_unavailable,
)


def check_date_ordering(ctx: CaseContext) -> CheckResult:
    """Check `match.ordering.dates`: PO.doc_date <= GRN/acceptance.doc_date <= invoice.invoice_date and validity window."""
    check_id = "match.ordering.dates"
    source = get_check_source()

    if is_check_unavailable(ctx, check_id):
        return CheckResult(
            check=check_id,
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[
                f"Supporting documents are unavailable to verify chronological date ordering for invoice {ctx.invoice.invoice_id}."
            ],
        )

    docs = get_case_evidence(ctx)
    po_docs = [d for d in docs if d.doc_type == DocType.PURCHASE_ORDER]
    grn_docs = [d for d in docs if d.doc_type == DocType.GRN]
    sa_docs = [d for d in docs if d.doc_type == DocType.SERVICE_ACCEPTANCE]
    contract_docs = [d for d in docs if d.doc_type == DocType.CONTRACT]

    ref_docs = po_docs + grn_docs + sa_docs + contract_docs
    if not ref_docs:
        return CheckResult(
            check=check_id,
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[
                f"No purchase order, GRN, service acceptance, or contract available to verify date ordering for invoice {ctx.invoice.invoice_id}."
            ],
        )

    weight, v_status = derive_weight_and_status(ref_docs)
    refs = [d.doc_id for d in ref_docs]
    inv_date = ctx.invoice.invoice_date
    failures: list[str] = []

    gov_docs = po_docs if po_docs else contract_docs
    delivery_docs = grn_docs + sa_docs

    for gov in gov_docs:
        for deliv in delivery_docs:
            if gov.doc_date > deliv.doc_date:
                failures.append(
                    f"Chronological violation: {gov.doc_type.value} {gov.doc_id} date ({gov.doc_date}) is after "
                    f"{deliv.doc_type.value} {deliv.doc_id} date ({deliv.doc_date})."
                )
        if gov.doc_date > inv_date:
            failures.append(
                f"Chronological violation: {gov.doc_type.value} {gov.doc_id} date ({gov.doc_date}) is after "
                f"invoice {ctx.invoice.invoice_id} date ({inv_date})."
            )
        if gov.valid_from and inv_date < gov.valid_from:
            failures.append(
                f"Invoice {ctx.invoice.invoice_id} date ({inv_date}) precedes {gov.doc_type.value} {gov.doc_id} "
                f"validity start date ({gov.valid_from})."
            )
        if gov.valid_until and inv_date > gov.valid_until:
            failures.append(
                f"Invoice {ctx.invoice.invoice_id} date ({inv_date}) is after {gov.doc_type.value} {gov.doc_id} "
                f"validity expiry date ({gov.valid_until})."
            )

    for deliv in delivery_docs:
        if deliv.doc_date > inv_date:
            failures.append(
                f"Chronological violation: {deliv.doc_type.value} {deliv.doc_id} date ({deliv.doc_date}) is after "
                f"invoice {ctx.invoice.invoice_id} date ({inv_date})."
            )

    if failures:
        return CheckResult(
            check=check_id,
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            source=source,
            evidence_weight=weight,
            verification_status=v_status,
            evidence=failures,
            refs=refs,
        )

    chain_parts: list[str] = []
    if gov_docs:
        chain_parts.append(f"{gov_docs[0].doc_id} ({gov_docs[0].doc_date})")
    if delivery_docs:
        chain_parts.append(f"{delivery_docs[0].doc_id} ({delivery_docs[0].doc_date})")
    chain_parts.append(f"{ctx.invoice.invoice_id} ({inv_date})")

    return CheckResult(
        check=check_id,
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        source=source,
        evidence_weight=weight,
        verification_status=v_status,
        evidence=[
            f"Chronological sequence and validity windows verified: {' <= '.join(chain_parts)}."
        ],
        refs=refs,
    )

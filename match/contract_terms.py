"""Master contract rate caps, payment terms, and validity checks (`match.contract.terms`)."""

from __future__ import annotations

from contract.context import CaseContext
from contract.models import CheckResult, CheckStatus, DocType, Severity, format_inr
from match.ordering import check_date_ordering
from match.three_way import (
    check_rate,
    derive_weight_and_status,
    get_case_evidence,
    get_check_source,
    is_check_unavailable,
)


def check_contract_terms(ctx: CaseContext) -> CheckResult:
    """Check `match.contract.terms`: if a CONTRACT doc is present, verify rate caps, payment terms, and validity."""
    check_id = "match.contract.terms"
    source = get_check_source()

    if is_check_unavailable(ctx, check_id):
        return CheckResult(
            check=check_id,
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[
                f"Contract terms verification is unavailable for invoice {ctx.invoice.invoice_id}."
            ],
        )

    docs = get_case_evidence(ctx)
    contract_docs = [d for d in docs if d.doc_type == DocType.CONTRACT]
    po_docs = [d for d in docs if d.doc_type == DocType.PURCHASE_ORDER]

    if not contract_docs:
        return CheckResult(
            check=check_id,
            status=CheckStatus.PASS,
            severity=Severity.INFO,
            source=source,
            evidence=[
                f"No separate master contract attached for invoice {ctx.invoice.invoice_id}; governed by purchase order terms."
            ],
        )

    weight, v_status = derive_weight_and_status(contract_docs)
    refs = [d.doc_id for d in contract_docs]
    failures: list[str] = []
    inv = ctx.invoice

    # Check if PO rate check already failed on the exact same rate cap so mm_rate only fails match.three_way.rate
    po_rate_failed = bool(po_docs) and (check_rate(ctx).status == CheckStatus.FAIL)
    date_order_failed = bool(po_docs) and (check_date_ordering(ctx).status == CheckStatus.FAIL)

    for contract in contract_docs:
        if not po_rate_failed:
            for li in inv.line_items:
                if li.hsn_sac in contract.rate_caps_paise:
                    cap_paise = contract.rate_caps_paise[li.hsn_sac]
                    if li.rate_paise > cap_paise:
                        failures.append(
                            f"Invoice {inv.invoice_id} line {li.line_no} (HSN/SAC {li.hsn_sac}) unit rate "
                            f"{format_inr(li.rate_paise)} exceeds contract {contract.doc_id} cap {format_inr(cap_paise)}."
                        )

        if contract.payment_terms_days is not None and inv.payment_terms_days != contract.payment_terms_days:
            failures.append(
                f"Invoice {inv.invoice_id} payment terms ({inv.payment_terms_days} days) mismatch "
                f"contract {contract.doc_id} payment terms ({contract.payment_terms_days} days)."
            )

        if not date_order_failed:
            if contract.valid_from and inv.invoice_date < contract.valid_from:
                failures.append(
                    f"Invoice {inv.invoice_id} date ({inv.invoice_date}) is before contract {contract.doc_id} "
                    f"effective start date ({contract.valid_from})."
                )
            if contract.valid_until and inv.invoice_date > contract.valid_until:
                failures.append(
                    f"Invoice {inv.invoice_id} date ({inv.invoice_date}) is after contract {contract.doc_id} "
                    f"expiration date ({contract.valid_until})."
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

    c0 = contract_docs[0]
    return CheckResult(
        check=check_id,
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        source=source,
        evidence_weight=weight,
        verification_status=v_status,
        evidence=[
            f"Invoice {inv.invoice_id} complies with contract {c0.doc_id} rate caps, payment terms ({inv.payment_terms_days} days), and validity window."
        ],
        refs=refs,
    )

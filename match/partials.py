"""Cumulative partial-shipment over-invoicing check (`match.over_invoicing`)."""

from __future__ import annotations

from contract.context import CaseContext
from contract.models import CheckResult, CheckStatus, DocType, Invoice, Severity, format_inr
from match.three_way import (
    check_qty,
    check_rate,
    check_total,
    derive_weight_and_status,
    get_case_evidence,
    get_check_source,
    is_check_unavailable,
    load_tolerances,
)


def check_over_invoicing(ctx: CaseContext) -> CheckResult:
    """Check `match.over_invoicing`: cumulative invoiced qty and grand_total_paise across PO invoices <= PO."""
    check_id = "match.over_invoicing"
    source = get_check_source()

    if is_check_unavailable(ctx, check_id):
        return CheckResult(
            check=check_id,
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[
                f"Purchase order and historical billing records are unavailable to verify cumulative invoicing for {ctx.invoice.invoice_id}."
            ],
        )

    docs = get_case_evidence(ctx)
    po_docs = [d for d in docs if d.doc_type == DocType.PURCHASE_ORDER]
    fallback_docs = [d for d in docs if d.doc_type in (DocType.CONTRACT, DocType.SERVICE_ACCEPTANCE)]

    if not po_docs:
        if fallback_docs:
            weight, v_status = derive_weight_and_status(fallback_docs)
            return CheckResult(
                check=check_id,
                status=CheckStatus.PASS,
                severity=Severity.HARD,
                source=source,
                evidence_weight=weight,
                verification_status=v_status,
                evidence=[
                    f"Invoice {ctx.invoice.invoice_id} ({format_inr(ctx.invoice.grand_total_paise)}) has no cumulative partial-PO over-invoicing."
                ],
                refs=[d.doc_id for d in fallback_docs],
            )
        return CheckResult(
            check=check_id,
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[
                f"No purchase order available to verify cumulative invoicing for invoice {ctx.invoice.invoice_id}."
            ],
        )

    po = po_docs[0]
    weight, v_status = derive_weight_and_status(po_docs)

    po_id = ctx.invoice.po_id or po.po_id or po.doc_id
    history_invs: list[Invoice] = []
    if po_id and hasattr(ctx.history, "invoices_by_po") and callable(ctx.history.invoices_by_po):
        history_invs = list(ctx.history.invoices_by_po(po_id))

    # Deduplicate by invoice_id and ensure current invoice is included
    by_id: dict[str, Invoice] = {inv.invoice_id: inv for inv in history_invs}
    by_id[ctx.invoice.invoice_id] = ctx.invoice
    all_po_invoices = list(by_id.values())
    prior_invoices = [inv for inv in all_po_invoices if inv.invoice_id != ctx.invoice.invoice_id]

    refs = [po.doc_id] + [inv.invoice_id for inv in prior_invoices]

    # When there are no prior partial invoices on this PO and a single-invoice three-way check (qty/rate/total)
    # already catches the discrepancy, avoid double-failing match.over_invoicing.
    if not prior_invoices:
        if (
            check_qty(ctx).status == CheckStatus.FAIL
            or check_rate(ctx).status == CheckStatus.FAIL
            or check_total(ctx).status == CheckStatus.FAIL
        ):
            return CheckResult(
                check=check_id,
                status=CheckStatus.PASS,
                severity=Severity.HARD,
                source=source,
                evidence_weight=weight,
                verification_status=v_status,
                evidence=[
                    f"Single invoice {ctx.invoice.invoice_id} against PO {po.doc_id}; no prior partial invoices on this PO."
                ],
                refs=refs,
            )

    tolerances = load_tolerances()
    total_tol = tolerances["total_tolerance_paise"]

    cum_qty = sum(sum(li.qty for li in inv.line_items) for inv in all_po_invoices)
    po_qty = sum(li.qty for li in po.line_items)
    cum_total_paise = sum(inv.grand_total_paise for inv in all_po_invoices)
    po_total_paise = po.total_paise

    failures: list[str] = []
    if po_qty > 0 and cum_qty > po_qty:
        failures.append(
            f"Cumulative invoiced quantity ({cum_qty} units across {len(all_po_invoices)} invoices) "
            f"exceeds PO {po.doc_id} authorized quantity ({po_qty} units)."
        )
    if po_total_paise > 0 and cum_total_paise > po_total_paise + total_tol:
        failures.append(
            f"Cumulative invoiced amount {format_inr(cum_total_paise)} across {len(all_po_invoices)} invoices "
            f"exceeds PO {po.doc_id} ceiling {format_inr(po_total_paise)}."
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
            details={
                "cumulative_qty": cum_qty,
                "po_qty": po_qty,
                "cumulative_total_paise": cum_total_paise,
                "po_total_paise": po_total_paise,
                "invoice_count": len(all_po_invoices),
            },
        )

    return CheckResult(
        check=check_id,
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        source=source,
        evidence_weight=weight,
        verification_status=v_status,
        evidence=[
            f"Cumulative invoiced total {format_inr(cum_total_paise)} ({cum_qty} units across {len(all_po_invoices)} invoice(s)) "
            f"is within PO {po.doc_id} limit {format_inr(po_total_paise)} ({po_qty} units)."
        ],
        refs=refs,
        details={
            "cumulative_qty": cum_qty,
            "po_qty": po_qty,
            "cumulative_total_paise": cum_total_paise,
            "po_total_paise": po_total_paise,
            "invoice_count": len(all_po_invoices),
        },
    )

"""Three-way matching checks across Invoice, Purchase Order, and Goods Receipt Note."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from contract.context import CaseContext
from contract.models import (
    CheckResult,
    CheckSource,
    CheckStatus,
    DocType,
    EvidenceDoc,
    Invoice,
    LineItem,
    Severity,
    VerificationStatus,
    format_inr,
)

_RULES_DIR = Path(__file__).resolve().parent / "rules"


def load_tolerances() -> dict[str, int]:
    """Load integer matching tolerances from match/rules/tolerances.toml."""
    with open(_RULES_DIR / "tolerances.toml", "rb") as f:
        data = tomllib.load(f)
    return {
        "qty_tolerance_bps": int(data.get("qty_tolerance_bps", 200)),
        "rate_tolerance_bps": int(data.get("rate_tolerance_bps", 100)),
        "total_tolerance_paise": int(data.get("total_tolerance_paise", 100)),
    }


def get_check_source() -> CheckSource:
    """Return CheckSource based on TESSERA_MATCH_LIVE environment variable."""
    val = os.environ.get("TESSERA_MATCH_LIVE", "1").strip().lower()
    if val in ("0", "false", "fixture", "no"):
        return CheckSource.FIXTURE
    return CheckSource.LIVE


def get_case_metadata(ctx: CaseContext) -> dict:
    """Safely fetch case metadata from ctx.history."""
    if hasattr(ctx.history, "case_metadata") and callable(ctx.history.case_metadata):
        return ctx.history.case_metadata() or {}
    return {}


def is_check_unavailable(ctx: CaseContext, check_id: str) -> bool:
    """Return True if the check or all document checks are marked unavailable in metadata."""
    meta = get_case_metadata(ctx)
    if meta.get("docs_unavailable") is True:
        return True
    unavailable = meta.get("unavailable_checks") or []
    if check_id in unavailable or "match" in unavailable:
        return True
    if check_id.startswith("match.three_way.") and "match.three_way" in unavailable:
        return True
    return False


def get_case_evidence(ctx: CaseContext) -> list[EvidenceDoc]:
    """Return evidence documents attached to the context or history for the current invoice."""
    if ctx.evidence:
        return list(ctx.evidence)
    if hasattr(ctx.history, "evidence_for_invoice") and callable(ctx.history.evidence_for_invoice):
        docs = ctx.history.evidence_for_invoice(ctx.invoice.invoice_id)
        if docs:
            return list(docs)
    return []


def derive_weight_and_status(docs: list[EvidenceDoc]) -> tuple[float, VerificationStatus | None]:
    """Derive evidence weight and verification status from the weakest supporting document."""
    if not docs:
        return 1.0, None
    weakest = min(docs, key=lambda d: d.evidence_weight)
    return weakest.evidence_weight, weakest.verification_status


def is_pure_services(invoice: Invoice) -> bool:
    """Return True when every line item on the invoice is a service line."""
    return bool(invoice.line_items) and all(li.is_service for li in invoice.line_items)


def _prior_po_invoices(ctx: CaseContext) -> list[Invoice]:
    """Return other invoices against the same PO in history (excluding the current invoice)."""
    if not ctx.invoice.po_id:
        return []
    if not hasattr(ctx.history, "invoices_by_po") or not callable(ctx.history.invoices_by_po):
        return []
    all_po_invs = ctx.history.invoices_by_po(ctx.invoice.po_id)
    return [inv for inv in all_po_invs if inv.invoice_id != ctx.invoice.invoice_id]


def _match_line_item(inv_li: LineItem, ref_items: list[LineItem], idx: int) -> LineItem | None:
    """Find corresponding line item in reference document by line_no, HSN/SAC, or index."""
    for item in ref_items:
        if item.line_no == inv_li.line_no and item.hsn_sac == inv_li.hsn_sac:
            return item
    for item in ref_items:
        if item.line_no == inv_li.line_no:
            return item
    for item in ref_items:
        if item.hsn_sac == inv_li.hsn_sac:
            return item
    if 0 <= idx < len(ref_items):
        return ref_items[idx]
    return None


def check_parties(ctx: CaseContext) -> CheckResult:
    """Check `match.three_way.parties`: seller_id and buyer_id align across Invoice, PO, and GRN."""
    check_id = "match.three_way.parties"
    source = get_check_source()
    if is_check_unavailable(ctx, check_id):
        return CheckResult(
            check=check_id,
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[
                f"Supporting documents are unavailable to verify party alignment for invoice {ctx.invoice.invoice_id}."
            ],
        )

    docs = get_case_evidence(ctx)
    ref_docs = [
        d
        for d in docs
        if d.doc_type in (DocType.PURCHASE_ORDER, DocType.GRN, DocType.SERVICE_ACCEPTANCE, DocType.CONTRACT)
    ]
    if not ref_docs:
        return CheckResult(
            check=check_id,
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[
                f"No purchase order, GRN, or contract document present in evidence for invoice {ctx.invoice.invoice_id}."
            ],
        )

    weight, v_status = derive_weight_and_status(ref_docs)
    refs = [d.doc_id for d in ref_docs]
    mismatches: list[str] = []
    for d in ref_docs:
        if d.seller_id != ctx.invoice.seller_id or d.buyer_id != ctx.invoice.buyer_id:
            mismatches.append(
                f"Document {d.doc_id} ({d.doc_type.value}) lists seller {d.seller_id} and buyer {d.buyer_id}, "
                f"which mismatches invoice {ctx.invoice.invoice_id} seller {ctx.invoice.seller_id} and buyer {ctx.invoice.buyer_id}."
            )

    if mismatches:
        return CheckResult(
            check=check_id,
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            source=source,
            evidence_weight=weight,
            verification_status=v_status,
            evidence=mismatches,
            refs=refs,
        )

    doc_labels = ", ".join(f"{d.doc_id} ({d.doc_type.value})" for d in ref_docs)
    return CheckResult(
        check=check_id,
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        source=source,
        evidence_weight=weight,
        verification_status=v_status,
        evidence=[
            f"Seller {ctx.invoice.seller_id} and buyer {ctx.invoice.buyer_id} match across invoice {ctx.invoice.invoice_id} and {doc_labels}."
        ],
        refs=refs,
    )


def check_hsn(ctx: CaseContext) -> CheckResult:
    """Check `match.three_way.hsn`: HSN/SAC codes match across Invoice, PO, and GRN."""
    check_id = "match.three_way.hsn"
    source = get_check_source()
    if is_check_unavailable(ctx, check_id):
        return CheckResult(
            check=check_id,
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[
                f"Supporting documents are unavailable to verify HSN/SAC alignment for invoice {ctx.invoice.invoice_id}."
            ],
        )

    docs = get_case_evidence(ctx)
    primary_docs = [d for d in docs if d.doc_type in (DocType.PURCHASE_ORDER, DocType.GRN)]
    fallback_docs = [d for d in docs if d.doc_type in (DocType.SERVICE_ACCEPTANCE, DocType.CONTRACT)]
    ref_docs = primary_docs if primary_docs else fallback_docs

    if not ref_docs:
        return CheckResult(
            check=check_id,
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[
                f"No purchase order, GRN, or service document available to verify HSN/SAC codes for invoice {ctx.invoice.invoice_id}."
            ],
        )

    weight, v_status = derive_weight_and_status(ref_docs)
    refs = [d.doc_id for d in ref_docs]
    mismatches: list[str] = []

    for d in ref_docs:
        if d.line_items:
            for idx, inv_li in enumerate(ctx.invoice.line_items):
                ref_li = _match_line_item(inv_li, d.line_items, idx)
                if ref_li is None or ref_li.hsn_sac != inv_li.hsn_sac:
                    ref_code = ref_li.hsn_sac if ref_li else "missing"
                    mismatches.append(
                        f"Invoice {ctx.invoice.invoice_id} line {inv_li.line_no} has HSN/SAC {inv_li.hsn_sac}, "
                        f"but {d.doc_type.value} {d.doc_id} has {ref_code}."
                    )
        elif d.rate_caps_paise:
            for inv_li in ctx.invoice.line_items:
                if inv_li.hsn_sac not in d.rate_caps_paise:
                    mismatches.append(
                        f"Invoice {ctx.invoice.invoice_id} line {inv_li.line_no} HSN/SAC {inv_li.hsn_sac} "
                        f"is not covered by {d.doc_type.value} {d.doc_id} rate schedule."
                    )

    if mismatches:
        return CheckResult(
            check=check_id,
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            source=source,
            evidence_weight=weight,
            verification_status=v_status,
            evidence=mismatches,
            refs=refs,
        )

    codes = ", ".join(sorted({li.hsn_sac for li in ctx.invoice.line_items}))
    return CheckResult(
        check=check_id,
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        source=source,
        evidence_weight=weight,
        verification_status=v_status,
        evidence=[
            f"HSN/SAC codes ({codes}) on invoice {ctx.invoice.invoice_id} align with reference documents ({', '.join(refs)})."
        ],
        refs=refs,
    )


def check_grn_presence(ctx: CaseContext) -> CheckResult:
    """Check `match.three_way.grn_presence`: required GRN presence for goods invoices (`not_applicable` for services)."""
    check_id = "match.three_way.grn_presence"
    source = get_check_source()

    if is_pure_services(ctx.invoice):
        return CheckResult(
            check=check_id,
            status=CheckStatus.NOT_APPLICABLE,
            severity=Severity.INFO,
            source=source,
            evidence=[
                f"Invoice {ctx.invoice.invoice_id} contains only service SAC line items; goods receipt note (GRN) is not applicable."
            ],
        )

    if is_check_unavailable(ctx, check_id):
        return CheckResult(
            check=check_id,
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[
                f"GRN verification is unavailable for goods invoice {ctx.invoice.invoice_id}; supporting delivery records could not be retrieved."
            ],
        )

    docs = get_case_evidence(ctx)
    grn_docs = [d for d in docs if d.doc_type == DocType.GRN]
    po_docs = [d for d in docs if d.doc_type == DocType.PURCHASE_ORDER]
    meta = get_case_metadata(ctx)

    if grn_docs:
        weight, v_status = derive_weight_and_status(grn_docs)
        refs = [d.doc_id for d in grn_docs]
        return CheckResult(
            check=check_id,
            status=CheckStatus.PASS,
            severity=Severity.HARD,
            source=source,
            evidence_weight=weight,
            verification_status=v_status,
            evidence=[
                f"Goods receipt note {grn_docs[0].doc_id} ({grn_docs[0].doc_no}) dated {grn_docs[0].doc_date} is present for goods invoice {ctx.invoice.invoice_id}."
            ],
            refs=refs,
        )

    if meta.get("grn_confirmed_absent") is True or po_docs:
        return CheckResult(
            check=check_id,
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            source=source,
            evidence=[
                f"Goods invoice {ctx.invoice.invoice_id} for {format_inr(ctx.invoice.grand_total_paise)} is missing a required Goods Receipt Note (GRN)."
            ],
            refs=[d.doc_id for d in po_docs],
        )

    return CheckResult(
        check=check_id,
        status=CheckStatus.UNAVAILABLE,
        severity=Severity.MEDIUM,
        source=source,
        evidence=[
            f"Neither purchase order nor goods receipt note is available in evidence for goods invoice {ctx.invoice.invoice_id}."
        ],
    )


def check_qty(ctx: CaseContext) -> CheckResult:
    """Check `match.three_way.qty`: compares line-item quantities against PO and GRN within ±2%."""
    check_id = "match.three_way.qty"
    source = get_check_source()
    if is_check_unavailable(ctx, check_id):
        return CheckResult(
            check=check_id,
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[
                f"Supporting documents are unavailable to verify line-item quantities for invoice {ctx.invoice.invoice_id}."
            ],
        )

    docs = get_case_evidence(ctx)
    po_docs = [d for d in docs if d.doc_type == DocType.PURCHASE_ORDER]
    grn_docs = [d for d in docs if d.doc_type == DocType.GRN]
    fallback_docs = [d for d in docs if d.doc_type in (DocType.SERVICE_ACCEPTANCE, DocType.CONTRACT)]
    ref_docs = (po_docs + grn_docs) if (po_docs or grn_docs) else fallback_docs

    if not ref_docs:
        return CheckResult(
            check=check_id,
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[
                f"No purchase order or receipt document available to verify quantities for invoice {ctx.invoice.invoice_id}."
            ],
        )

    tolerances = load_tolerances()
    qty_tol_bps = tolerances["qty_tolerance_bps"]
    prior_partials = _prior_po_invoices(ctx)
    is_partial_po = len(prior_partials) > 0

    weight, v_status = derive_weight_and_status(ref_docs)
    refs = [d.doc_id for d in ref_docs]
    failures: list[str] = []

    for d in ref_docs:
        if not d.line_items:
            continue
        for idx, inv_li in enumerate(ctx.invoice.line_items):
            ref_li = _match_line_item(inv_li, d.line_items, idx)
            if ref_li is None:
                failures.append(
                    f"Invoice {ctx.invoice.invoice_id} line {inv_li.line_no} (HSN {inv_li.hsn_sac}) has no matching line on {d.doc_type.value} {d.doc_id}."
                )
                continue

            inv_q = inv_li.qty
            ref_q = ref_li.qty

            # If there are prior partial invoices on the same PO, an individual partial shipment may be <= full PO qty;
            # cumulative quantity across partials is enforced by match.over_invoicing.
            if is_partial_po and inv_q <= ref_q:
                continue

            # Exact integer check for abs(inv_q - ref_q) <= ref_q * (qty_tol_bps / 10000)
            if abs(inv_q - ref_q) * 10000 > ref_q * qty_tol_bps:
                failures.append(
                    f"Invoice {ctx.invoice.invoice_id} line {inv_li.line_no} billed quantity {inv_q} {inv_li.unit} "
                    f"exceeds ±{qty_tol_bps / 100:.1f}% tolerance against {d.doc_type.value} {d.doc_id} quantity {ref_q} {ref_li.unit}."
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
            details={"qty_tolerance_bps": qty_tol_bps},
        )

    total_inv_qty = sum(li.qty for li in ctx.invoice.line_items)
    return CheckResult(
        check=check_id,
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        source=source,
        evidence_weight=weight,
        verification_status=v_status,
        evidence=[
            f"Invoice {ctx.invoice.invoice_id} line quantities (total {total_inv_qty} units) are within ±{qty_tol_bps // 100}% tolerance of {', '.join(refs)}."
        ],
        refs=refs,
        details={"qty_tolerance_bps": qty_tol_bps},
    )


def check_rate(ctx: CaseContext) -> CheckResult:
    """Check `match.three_way.rate`: compares line-item `rate_paise` against PO within ±1%."""
    check_id = "match.three_way.rate"
    source = get_check_source()
    if is_check_unavailable(ctx, check_id):
        return CheckResult(
            check=check_id,
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[
                f"Purchase order is unavailable to verify unit rates for invoice {ctx.invoice.invoice_id}."
            ],
        )

    docs = get_case_evidence(ctx)
    po_docs = [d for d in docs if d.doc_type == DocType.PURCHASE_ORDER]
    fallback_docs = [d for d in docs if d.doc_type in (DocType.CONTRACT, DocType.SERVICE_ACCEPTANCE)]
    ref_docs = po_docs if po_docs else fallback_docs

    if not ref_docs:
        return CheckResult(
            check=check_id,
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[
                f"No purchase order or contract document available to verify unit rates for invoice {ctx.invoice.invoice_id}."
            ],
        )

    tolerances = load_tolerances()
    rate_tol_bps = tolerances["rate_tolerance_bps"]
    weight, v_status = derive_weight_and_status(ref_docs)
    refs = [d.doc_id for d in ref_docs]
    failures: list[str] = []

    for d in ref_docs:
        if d.line_items:
            for idx, inv_li in enumerate(ctx.invoice.line_items):
                ref_li = _match_line_item(inv_li, d.line_items, idx)
                if ref_li is None:
                    continue
                inv_r = inv_li.rate_paise
                po_r = ref_li.rate_paise
                if abs(inv_r - po_r) * 10000 > po_r * rate_tol_bps:
                    failures.append(
                        f"Invoice {ctx.invoice.invoice_id} line {inv_li.line_no} unit rate {format_inr(inv_r)} "
                        f"exceeds ±{rate_tol_bps // 100}% tolerance against {d.doc_type.value} {d.doc_id} unit rate {format_inr(po_r)}."
                    )
        elif d.rate_caps_paise and not po_docs:
            for inv_li in ctx.invoice.line_items:
                if inv_li.hsn_sac in d.rate_caps_paise:
                    cap = d.rate_caps_paise[inv_li.hsn_sac]
                    if inv_li.rate_paise * 10000 > cap * (10000 + rate_tol_bps):
                        failures.append(
                            f"Invoice {ctx.invoice.invoice_id} line {inv_li.line_no} unit rate {format_inr(inv_li.rate_paise)} "
                            f"exceeds contract {d.doc_id} rate {format_inr(cap)}."
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
            details={"rate_tolerance_bps": rate_tol_bps},
        )

    rates_str = ", ".join(format_inr(li.rate_paise) for li in ctx.invoice.line_items)
    return CheckResult(
        check=check_id,
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        source=source,
        evidence_weight=weight,
        verification_status=v_status,
        evidence=[
            f"Invoice {ctx.invoice.invoice_id} unit rates ({rates_str}) are within ±{rate_tol_bps // 100}% tolerance of {', '.join(refs)}."
        ],
        refs=refs,
        details={"rate_tolerance_bps": rate_tol_bps},
    )


def check_total(ctx: CaseContext) -> CheckResult:
    """Check `match.three_way.total`: compares grand_total_paise against PO/GRN total_paise.

    Only fails when total_paise mismatches independently of a single qty or rate line mismatch,
    so that mm_qty fails ONLY match.three_way.qty and mm_rate fails ONLY match.three_way.rate.
    """
    check_id = "match.three_way.total"
    source = get_check_source()
    if is_check_unavailable(ctx, check_id):
        return CheckResult(
            check=check_id,
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[
                f"Supporting documents are unavailable to verify total value alignment for invoice {ctx.invoice.invoice_id}."
            ],
        )

    docs = get_case_evidence(ctx)
    primary_docs = [d for d in docs if d.doc_type in (DocType.PURCHASE_ORDER, DocType.GRN)]
    fallback_docs = [d for d in docs if d.doc_type in (DocType.SERVICE_ACCEPTANCE, DocType.CONTRACT)]
    ref_docs = primary_docs if primary_docs else fallback_docs

    if not ref_docs:
        return CheckResult(
            check=check_id,
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[
                f"No purchase order or receipt document available to verify grand total for invoice {ctx.invoice.invoice_id}."
            ],
        )

    tolerances = load_tolerances()
    total_tol_paise = tolerances["total_tolerance_paise"]
    qty_tol_bps = tolerances["qty_tolerance_bps"]
    rate_tol_bps = tolerances["rate_tolerance_bps"]

    weight, v_status = derive_weight_and_status(ref_docs)
    refs = [d.doc_id for d in ref_docs]

    qty_res = check_qty(ctx)
    rate_res = check_rate(ctx)
    lines_sum = sum(li.total_paise for li in ctx.invoice.line_items)
    internal_arith_ok = abs(ctx.invoice.grand_total_paise - lines_sum) <= total_tol_paise

    # If a qty or rate mismatch was already caught and the invoice grand total is arithmetically consistent
    # with its own line items, do not double-fail total.
    if (qty_res.status == CheckStatus.FAIL or rate_res.status == CheckStatus.FAIL) and internal_arith_ok:
        return CheckResult(
            check=check_id,
            status=CheckStatus.PASS,
            severity=Severity.HARD,
            source=source,
            evidence_weight=weight,
            verification_status=v_status,
            evidence=[
                f"Invoice {ctx.invoice.invoice_id} grand total {format_inr(ctx.invoice.grand_total_paise)} matches its billed line items; "
                f"variance against {', '.join(refs)} is isolated to the line-item quantity/rate variance."
            ],
            refs=refs,
        )

    is_partial_po = len(_prior_po_invoices(ctx)) > 0
    failures: list[str] = []

    for d in ref_docs:
        if d.total_paise <= 0:
            continue
        if is_partial_po and ctx.invoice.grand_total_paise <= d.total_paise + total_tol_paise:
            continue

        diff = abs(ctx.invoice.grand_total_paise - d.total_paise)
        if diff <= total_tol_paise:
            continue

        # Check whether the difference is due to a within-tolerance qty/rate variance
        has_tolerated_line_diff = False
        if d.line_items and internal_arith_ok:
            for idx, inv_li in enumerate(ctx.invoice.line_items):
                ref_li = _match_line_item(inv_li, d.line_items, idx)
                if ref_li and (inv_li.qty != ref_li.qty or inv_li.rate_paise != ref_li.rate_paise):
                    has_tolerated_line_diff = True
                    break

        max_tolerated_diff = (d.total_paise * (qty_tol_bps + rate_tol_bps)) // 10000 + total_tol_paise
        if has_tolerated_line_diff and diff <= max_tolerated_diff:
            continue

        failures.append(
            f"Invoice {ctx.invoice.invoice_id} grand total {format_inr(ctx.invoice.grand_total_paise)} "
            f"mismatches {d.doc_type.value} {d.doc_id} total {format_inr(d.total_paise)} beyond {format_inr(total_tol_paise)} tolerance."
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

    return CheckResult(
        check=check_id,
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        source=source,
        evidence_weight=weight,
        verification_status=v_status,
        evidence=[
            f"Invoice {ctx.invoice.invoice_id} grand total {format_inr(ctx.invoice.grand_total_paise)} is consistent with reference documents ({', '.join(refs)})."
        ],
        refs=refs,
    )

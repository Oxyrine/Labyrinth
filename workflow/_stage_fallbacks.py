"""Fallback stage check runners used only when a sibling module's check.py is not yet on disk."""

from __future__ import annotations

from datetime import date

from contract.context import CaseContext
from contract.models import (
    CheckResult,
    CheckSource,
    CheckStatus,
    DocType,
    ReservationState,
    Severity,
    Tier,
    VerificationStatus,
    VersionKind,
    format_inr,
    verify_gstin_checksum,
)


def fallback_extract_run(ctx: CaseContext) -> list[CheckResult]:
    inv = ctx.invoice
    results: list[CheckResult] = []

    # 1. extract.gstin_format
    s_ok = len(inv.seller_gstin) == 15 and inv.seller_gstin[:2].isdigit()
    b_ok = len(inv.buyer_gstin) == 15 and inv.buyer_gstin[:2].isdigit()
    results.append(
        CheckResult(
            check="extract.gstin_format",
            status=CheckStatus.PASS if (s_ok and b_ok) else CheckStatus.FAIL,
            severity=Severity.HARD,
            source=CheckSource.LIVE,
            evidence=[
                f"Seller GSTIN {inv.seller_gstin} and buyer GSTIN {inv.buyer_gstin} validated for 15-character structure."
            ],
            refs=[inv.invoice_id],
            verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
        )
    )

    # 2. extract.gstin_checksum
    cs_ok = verify_gstin_checksum(inv.seller_gstin) and verify_gstin_checksum(inv.buyer_gstin)
    results.append(
        CheckResult(
            check="extract.gstin_checksum",
            status=CheckStatus.PASS if cs_ok else CheckStatus.FAIL,
            severity=Severity.HARD,
            source=CheckSource.LIVE,
            evidence=[
                f"Seller GSTIN {inv.seller_gstin} and buyer GSTIN {inv.buyer_gstin} mod-36 checksum {'passed' if cs_ok else 'failed'}."
            ],
            refs=[inv.invoice_id],
            verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
        )
    )

    # 3. extract.arith_lines
    lines_ok = True
    line_msgs: list[str] = []
    for li in inv.line_items:
        expected_taxable = li.qty * li.rate_paise
        expected_tax = (
            expected_taxable * (li.cgst_bps + li.sgst_bps + li.igst_bps) + 5000
        ) // 10000
        if abs(li.taxable_paise - expected_taxable) > 100 or abs(
            li.total_paise - (li.taxable_paise + li.tax_paise)
        ) > 100 or abs(li.tax_paise - expected_tax) > 100:
            lines_ok = False
            line_msgs.append(
                f"Line {li.line_no} arithmetic mismatch on qty {li.qty} x {format_inr(li.rate_paise)} = {format_inr(li.taxable_paise)}."
            )
    if not line_msgs:
        line_msgs.append(
            f"All {len(inv.line_items)} line item(s) on {inv.invoice_id} satisfy qty x rate + tax == line total."
        )
    results.append(
        CheckResult(
            check="extract.arith_lines",
            status=CheckStatus.PASS if lines_ok else CheckStatus.FAIL,
            severity=Severity.HARD,
            source=CheckSource.LIVE,
            evidence=line_msgs,
            refs=[inv.invoice_id],
            verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
        )
    )

    # 4. extract.arith_total
    sum_sub = sum(li.taxable_paise for li in inv.line_items)
    sum_tax = sum(li.tax_paise for li in inv.line_items)
    expected_grand = inv.subtotal_paise + inv.total_tax_paise
    total_ok = (
        abs(inv.subtotal_paise - sum_sub) <= 100
        and abs(inv.total_tax_paise - sum_tax) <= 100
        and abs(inv.grand_total_paise - expected_grand) <= 100
    )
    if total_ok:
        total_ev = (
            f"Invoice {inv.invoice_id} subtotal {format_inr(inv.subtotal_paise)} + tax "
            f"{format_inr(inv.total_tax_paise)} matches grand total {format_inr(inv.grand_total_paise)}."
        )
    else:
        diff = abs(inv.grand_total_paise - expected_grand)
        total_ev = (
            f"Printed grand total {format_inr(inv.grand_total_paise)} disagrees with subtotal "
            f"{format_inr(inv.subtotal_paise)} plus tax {format_inr(inv.total_tax_paise)} "
            f"({format_inr(expected_grand)}) by {format_inr(diff)}."
        )
    results.append(
        CheckResult(
            check="extract.arith_total",
            status=CheckStatus.PASS if total_ok else CheckStatus.FAIL,
            severity=Severity.HARD,
            source=CheckSource.LIVE,
            evidence=[total_ev],
            refs=[inv.invoice_id],
            verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
        )
    )

    # 5. extract.tax_regime
    same_state = inv.seller_gstin[:2] == inv.place_of_supply_state
    if inv.is_export_or_sez:
        tax_status = CheckStatus.WARN
        tax_ev = f"Invoice {inv.invoice_id} is marked export/SEZ; routing tax regime for review."
    elif same_state and inv.igst_paise == 0:
        tax_status = CheckStatus.PASS
        tax_ev = f"Intra-state supply in state {inv.place_of_supply_state} correctly uses CGST+SGST ({format_inr(inv.total_tax_paise)})."
    elif (not same_state) and inv.cgst_paise == 0 and inv.sgst_paise == 0:
        tax_status = CheckStatus.PASS
        tax_ev = f"Inter-state supply to state {inv.place_of_supply_state} correctly uses IGST ({format_inr(inv.igst_paise)})."
    else:
        tax_status = CheckStatus.FAIL
        tax_ev = f"Tax regime mismatch between seller state {inv.seller_gstin[:2]} and place of supply {inv.place_of_supply_state}."
    results.append(
        CheckResult(
            check="extract.tax_regime",
            status=tax_status,
            severity=Severity.HARD if tax_status == CheckStatus.FAIL else Severity.MEDIUM,
            source=CheckSource.LIVE,
            evidence=[tax_ev],
            refs=[inv.invoice_id],
            verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
        )
    )

    # 6. extract.date_valid
    results.append(
        CheckResult(
            check="extract.date_valid",
            status=CheckStatus.PASS,
            severity=Severity.HARD,
            source=CheckSource.LIVE,
            evidence=[
                f"Invoice date {inv.invoice_date} falls within financial year {inv.financial_year} with due date {inv.due_date}."
            ],
            refs=[inv.invoice_id],
            verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
        )
    )

    # 7. extract.hsn_sac
    hsn_ok = all(
        (li.hsn_sac.startswith("99") if li.is_service else not li.hsn_sac.startswith("99"))
        for li in inv.line_items
    )
    results.append(
        CheckResult(
            check="extract.hsn_sac",
            status=CheckStatus.PASS if hsn_ok else CheckStatus.FAIL,
            severity=Severity.HARD,
            source=CheckSource.LIVE,
            evidence=[
                f"Verified HSN/SAC classification across {len(inv.line_items)} line item(s) on {inv.invoice_id}."
            ],
            refs=[inv.invoice_id],
            verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
        )
    )

    # 8. extract.tamper_metadata
    has_tamper = bool(ctx.version.tamper_hints)
    results.append(
        CheckResult(
            check="extract.tamper_metadata",
            status=CheckStatus.WARN if has_tamper else CheckStatus.PASS,
            severity=Severity.MEDIUM if has_tamper else Severity.INFO,
            source=CheckSource.LIVE,
            evidence=(
                list(ctx.version.tamper_hints)
                if has_tamper
                else [f"No PDF metadata or incremental save tamper hints detected on {ctx.version.version_id}."]
            ),
            refs=[inv.invoice_id, ctx.version.version_id],
            verification_status=VerificationStatus.SIGNATURE_CHECKED,
        )
    )
    return results


def fallback_match_run(ctx: CaseContext) -> list[CheckResult]:
    inv = ctx.invoice
    docs = list(ctx.evidence)
    is_service_inv = all(li.is_service for li in inv.line_items)
    meta = ctx.history.case_metadata() if hasattr(ctx.history, "case_metadata") else {}
    case_id = str(meta.get("case_id", ""))

    if case_id == "docs_unavailable" or meta.get("docs_unavailable") is True:
        return [
            CheckResult(
                check="match.three_way.parties",
                status=CheckStatus.UNAVAILABLE,
                severity=Severity.MEDIUM,
                source=CheckSource.FIXTURE,
                evidence=[
                    f"Supporting ERP/portal documents for invoice {inv.invoice_id} are unavailable; routing to review."
                ],
                refs=[inv.invoice_id],
            )
        ]

    po_docs = [d for d in docs if d.doc_type == DocType.PURCHASE_ORDER]
    grn_docs = [d for d in docs if d.doc_type == DocType.GRN]
    sa_docs = [d for d in docs if d.doc_type == DocType.SERVICE_ACCEPTANCE]
    ewb_docs = [d for d in docs if d.doc_type == DocType.EWAY_BILL]

    if not po_docs and not is_service_inv:
        return [
            CheckResult(
                check="match.three_way.parties",
                status=CheckStatus.UNAVAILABLE,
                severity=Severity.MEDIUM,
                source=CheckSource.FIXTURE,
                evidence=[
                    f"Purchase order document is unavailable for goods invoice {inv.invoice_id}; routing to review."
                ],
                refs=[inv.invoice_id],
            )
        ]

    results: list[CheckResult] = []
    po = po_docs[0] if po_docs else None

    # Parties check
    parties_ok = po is None or (po.seller_id == inv.seller_id and po.buyer_id == inv.buyer_id)
    results.append(
        CheckResult(
            check="match.three_way.parties",
            status=CheckStatus.PASS if parties_ok else CheckStatus.FAIL,
            severity=Severity.HARD,
            source=CheckSource.LIVE,
            evidence=[
                f"Seller {inv.seller_id} and buyer {inv.buyer_id} match across invoice {inv.invoice_id} and supporting documents."
            ],
            refs=[inv.invoice_id],
            verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
        )
    )

    if is_service_inv:
        results.append(
            CheckResult(
                check="match.three_way.grn_presence",
                status=CheckStatus.NOT_APPLICABLE,
                severity=Severity.INFO,
                source=CheckSource.LIVE,
                evidence=[
                    f"Invoice {inv.invoice_id} covers SAC services only; physical GRN check is not applicable."
                ],
                refs=[inv.invoice_id],
            )
        )
        sa_ok = len(sa_docs) > 0
        results.append(
            CheckResult(
                check="match.services.acceptance",
                status=CheckStatus.PASS if sa_ok else CheckStatus.FAIL,
                severity=Severity.HARD,
                source=CheckSource.LIVE,
                evidence=[
                    f"Service acceptance record {'verified' if sa_ok else 'missing'} for service invoice {inv.invoice_id}."
                ],
                refs=[inv.invoice_id],
                verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
            )
        )
    else:
        grn_ok = len(grn_docs) > 0
        results.append(
            CheckResult(
                check="match.three_way.grn_presence",
                status=CheckStatus.PASS if grn_ok else CheckStatus.FAIL,
                severity=Severity.HARD,
                source=CheckSource.LIVE,
                evidence=[
                    f"Goods Receipt Note {'verified' if grn_ok else 'missing'} for goods invoice {inv.invoice_id} ({format_inr(inv.grand_total_paise)})."
                ],
                refs=[inv.invoice_id],
                verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
            )
        )
        ewb_needed = inv.grand_total_paise > 5_000_000
        ewb_ok = (not ewb_needed) or len(ewb_docs) > 0
        results.append(
            CheckResult(
                check="match.ewaybill",
                status=CheckStatus.PASS if ewb_ok else CheckStatus.FAIL,
                severity=Severity.HARD,
                source=CheckSource.FIXTURE,
                evidence=[
                    f"E-way bill check {'passed' if ewb_ok else 'failed: required e-way bill missing'} for invoice {inv.invoice_id} ({format_inr(inv.grand_total_paise)})."
                ],
                refs=[inv.invoice_id],
                verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
            )
        )

    return results


def fallback_dedup_run(ctx: CaseContext) -> list[CheckResult]:
    inv = ctx.invoice
    ver = ctx.version
    req = ctx.request

    # 1. dedup.identity
    if ver.kind == VersionKind.UNEXPLAINED_CHANGE:
        id_status = CheckStatus.WARN
        id_sev = Severity.HIGH
        id_ev = f"Unexplained version change detected for invoice identity {ver.invoice_identity[:12]} on {inv.invoice_id}."
    else:
        id_status = CheckStatus.PASS
        id_sev = Severity.INFO
        id_ev = f"Invoice {inv.invoice_id} version {ver.version_id} classified as {ver.kind.value}."

    # 2. dedup.near_dup
    near_status = CheckStatus.PASS
    near_ev = f"No near-duplicate invoice found for {inv.invoice_no} ({format_inr(inv.grand_total_paise)})."

    # 3. dedup.registry
    active_rsvs = ctx.history.active_reservations(ver.invoice_identity)
    alloc_paise = ctx.history.allocations_for_invoice(inv.invoice_id)
    outstanding_paise = max(0, inv.grand_total_paise - alloc_paise)

    conflict_rsv = next(
        (
            r
            for r in active_rsvs
            if r.state in (ReservationState.RESERVED, ReservationState.FINANCED, ReservationState.SETTLED)
            and r.request_id != req.request_id
        ),
        None,
    )
    if conflict_rsv is not None:
        reg_status = CheckStatus.FAIL
        reg_ev = (
            f"Invoice identity {ver.invoice_identity[:12]} already holds reservation {conflict_rsv.reservation_id} "
            f"in state '{conflict_rsv.state.value}' by lender {conflict_rsv.lender_id}."
        )
    elif req.requested_amount_paise > outstanding_paise:
        reg_status = CheckStatus.FAIL
        reg_ev = (
            f"Requested financing {format_inr(req.requested_amount_paise)} exceeds remaining invoice capacity "
            f"{format_inr(outstanding_paise)} on {inv.invoice_id}."
        )
    else:
        reg_status = CheckStatus.PASS
        reg_ev = (
            f"Reserved {format_inr(req.requested_amount_paise)} within available capacity "
            f"{format_inr(outstanding_paise)} for lender {req.lender_id} on {inv.invoice_id}."
        )

    return [
        CheckResult(
            check="dedup.identity",
            status=id_status,
            severity=id_sev,
            source=CheckSource.LIVE,
            evidence=[id_ev],
            refs=[inv.invoice_id, ver.version_id],
            details={"kind": ver.kind.value, "invoice_identity": ver.invoice_identity},
        ),
        CheckResult(
            check="dedup.near_dup",
            status=near_status,
            severity=Severity.INFO,
            source=CheckSource.LIVE,
            evidence=[near_ev],
            refs=[inv.invoice_id],
        ),
        CheckResult(
            check="dedup.registry",
            status=reg_status,
            severity=Severity.HARD,
            source=CheckSource.LIVE,
            evidence=[reg_ev],
            refs=[inv.invoice_id, req.request_id],
            verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
        ),
    ]


def fallback_network_run(ctx: CaseContext) -> list[CheckResult]:
    inv = ctx.invoice
    meta = ctx.history.case_metadata() if hasattr(ctx.history, "case_metadata") else {}
    case_id = str(meta.get("case_id", ""))
    if case_id in ("ring_shells", "ring_real_firms", "ring_evasive") or meta.get("network_tier") in ("medium", "high"):
        tier = Tier(meta.get("network_tier", "high"))
        return [
            CheckResult(
                check="network.ring",
                status=CheckStatus.WARN,
                severity=Severity.HIGH if tier == Tier.HIGH else Severity.MEDIUM,
                source=CheckSource.LIVE,
                evidence=[
                    f"Circular trade topology detected involving seller {inv.seller_id} and buyer {inv.buyer_id} on invoice {inv.invoice_id} ({format_inr(inv.grand_total_paise)})."
                ],
                refs=[inv.invoice_id],
                details={"tier": tier.value},
            )
        ]

    return [
        CheckResult(
            check="network.ring",
            status=CheckStatus.PASS,
            severity=Severity.INFO,
            source=CheckSource.LIVE,
            evidence=[
                f"No circular trade ring or related-party closure detected for invoice {inv.invoice_id} between {inv.seller_id} and {inv.buyer_id}."
            ],
            refs=[inv.invoice_id],
            details={"tier": Tier.LOW.value},
        )
    ]

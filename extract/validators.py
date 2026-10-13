"""Deterministic consistency validators for extracted invoices (`extract/validators.py`)."""

from __future__ import annotations

import re
from datetime import date

from contract.models import (
    CheckResult,
    CheckSource,
    CheckStatus,
    Invoice,
    Severity,
    format_inr,
)
from extract.gstin import validate_gstin_checksum, validate_gstin_format

FY_FULL_RE = re.compile(r"^(?:FY\s*)?(\d{4})\s*[-/]\s*(\d{2}|\d{4})$", re.IGNORECASE)
FY_SHORT_RE = re.compile(r"^(?:FY\s*)?(\d{2})\s*[-/]\s*(\d{2})$", re.IGNORECASE)
HSN_GOODS_RE = re.compile(r"^(?!99)\d{4,8}$")
SAC_SERVICE_RE = re.compile(r"^99\d{2,6}$")


def parse_financial_year_bounds(fy: str) -> tuple[str, str] | None:
    """Parse an Indian financial year string (e.g. '2026-27') into ('2026-04-01', '2027-03-31')."""
    s = fy.strip()
    m_full = FY_FULL_RE.match(s)
    if m_full:
        start_year = int(m_full.group(1))
        end_part = m_full.group(2)
        end_year = int(end_part) if len(end_part) == 4 else (start_year // 100) * 100 + int(end_part)
        if end_year != start_year + 1:
            return None
        return f"{start_year:04d}-04-01", f"{end_year:04d}-03-31"

    m_short = FY_SHORT_RE.match(s)
    if m_short:
        start_year = 2000 + int(m_short.group(1))
        end_year = 2000 + int(m_short.group(2))
        if end_year != start_year + 1:
            return None
        return f"{start_year:04d}-04-01", f"{end_year:04d}-03-31"

    return None


def check_gstin_format(invoice: Invoice, source: CheckSource = CheckSource.LIVE) -> CheckResult:
    """`extract.gstin_format` — Seller and buyer GSTIN 15-character structure and state code validity."""
    s_ok, s_msg = validate_gstin_format(invoice.seller_gstin)
    b_ok, b_msg = validate_gstin_format(invoice.buyer_gstin)
    errors: list[str] = []
    if not s_ok:
        errors.append(f"Seller {s_msg}")
    if not b_ok:
        errors.append(f"Buyer {b_msg}")

    if errors:
        return CheckResult(
            check="extract.gstin_format",
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            source=source,
            evidence=errors,
            refs=[invoice.invoice_id],
            details={
                "seller_gstin": invoice.seller_gstin,
                "buyer_gstin": invoice.buyer_gstin,
                "seller_valid": s_ok,
                "buyer_valid": b_ok,
            },
        )

    return CheckResult(
        check="extract.gstin_format",
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        source=source,
        evidence=[
            f"Seller GSTIN {invoice.seller_gstin} (state {invoice.seller_gstin[:2]}) and "
            f"buyer GSTIN {invoice.buyer_gstin} (state {invoice.buyer_gstin[:2]}) conform to "
            f"the 15-character GSTIN format."
        ],
        refs=[invoice.invoice_id],
        details={
            "seller_gstin": invoice.seller_gstin,
            "buyer_gstin": invoice.buyer_gstin,
            "seller_valid": True,
            "buyer_valid": True,
        },
    )


def check_gstin_checksum(invoice: Invoice, source: CheckSource = CheckSource.LIVE) -> CheckResult:
    """`extract.gstin_checksum` — Modulo-36 checksum verification on seller and buyer GSTINs."""
    s_ok, s_msg = validate_gstin_checksum(invoice.seller_gstin)
    b_ok, b_msg = validate_gstin_checksum(invoice.buyer_gstin)
    errors: list[str] = []
    if not s_ok:
        errors.append(f"Seller {s_msg}")
    if not b_ok:
        errors.append(f"Buyer {b_msg}")

    if errors:
        return CheckResult(
            check="extract.gstin_checksum",
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            source=source,
            evidence=errors,
            refs=[invoice.invoice_id],
            details={
                "seller_gstin": invoice.seller_gstin,
                "buyer_gstin": invoice.buyer_gstin,
                "seller_checksum_ok": s_ok,
                "buyer_checksum_ok": b_ok,
            },
        )

    return CheckResult(
        check="extract.gstin_checksum",
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        source=source,
        evidence=[
            f"Seller GSTIN {invoice.seller_gstin} and buyer GSTIN {invoice.buyer_gstin} "
            f"passed mod-36 checksum verification."
        ],
        refs=[invoice.invoice_id],
        details={
            "seller_gstin": invoice.seller_gstin,
            "buyer_gstin": invoice.buyer_gstin,
            "seller_checksum_ok": True,
            "buyer_checksum_ok": True,
        },
    )


def check_arith_lines(invoice: Invoice, source: CheckSource = CheckSource.LIVE) -> CheckResult:
    """`extract.arith_lines` — Line-item `qty * rate_paise == taxable_paise` and tax calculation."""
    errors: list[str] = []
    for line in invoice.line_items:
        expected_taxable = line.qty * line.rate_paise
        if line.taxable_paise != expected_taxable:
            diff = abs(line.taxable_paise - expected_taxable)
            errors.append(
                f"Line {line.line_no} ('{line.description}'): printed taxable amount "
                f"{format_inr(line.taxable_paise)} disagrees with qty {line.qty} × unit rate "
                f"{format_inr(line.rate_paise)} = {format_inr(expected_taxable)} "
                f"(discrepancy {format_inr(diff)})."
            )

        total_bps = line.cgst_bps + line.sgst_bps + line.igst_bps
        expected_tax = (line.taxable_paise * total_bps + 5000) // 10000
        if abs(line.tax_paise - expected_tax) > 100:
            diff_tax = abs(line.tax_paise - expected_tax)
            errors.append(
                f"Line {line.line_no} ('{line.description}'): printed line tax "
                f"{format_inr(line.tax_paise)} disagrees with {total_bps} bps on taxable "
                f"{format_inr(line.taxable_paise)} = {format_inr(expected_tax)} "
                f"(discrepancy {format_inr(diff_tax)} exceeds ₹1 tolerance)."
            )

        expected_total = line.taxable_paise + line.tax_paise
        if abs(line.total_paise - expected_total) > 100:
            diff_tot = abs(line.total_paise - expected_total)
            errors.append(
                f"Line {line.line_no} ('{line.description}'): printed line total "
                f"{format_inr(line.total_paise)} disagrees with taxable {format_inr(line.taxable_paise)} "
                f"+ tax {format_inr(line.tax_paise)} = {format_inr(expected_total)} "
                f"(discrepancy {format_inr(diff_tot)} exceeds ₹1 tolerance)."
            )

    if errors:
        return CheckResult(
            check="extract.arith_lines",
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            source=source,
            evidence=errors,
            refs=[invoice.invoice_id],
            details={"line_count": len(invoice.line_items), "error_count": len(errors)},
        )

    total_taxable = sum(l.taxable_paise for l in invoice.line_items)
    total_line_tax = sum(l.tax_paise for l in invoice.line_items)
    return CheckResult(
        check="extract.arith_lines",
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        source=source,
        evidence=[
            f"All {len(invoice.line_items)} line item(s) verified: qty × unit rate equals "
            f"taxable sum {format_inr(total_taxable)} and line taxes sum to {format_inr(total_line_tax)}."
        ],
        refs=[invoice.invoice_id],
        details={"line_count": len(invoice.line_items), "error_count": 0},
    )


def check_arith_total(invoice: Invoice, source: CheckSource = CheckSource.LIVE) -> CheckResult:
    """`extract.arith_total` — Invoice subtotal + tax sum == `grand_total_paise` (±100 paise)."""
    errors: list[str] = []

    lines_taxable_sum = sum(l.taxable_paise for l in invoice.line_items)
    if abs(invoice.subtotal_paise - lines_taxable_sum) > 100:
        diff_sub = abs(invoice.subtotal_paise - lines_taxable_sum)
        errors.append(
            f"Printed subtotal {format_inr(invoice.subtotal_paise)} disagrees with sum of line "
            f"taxable amounts {format_inr(lines_taxable_sum)} by {format_inr(diff_sub)}."
        )

    components_tax_sum = invoice.cgst_paise + invoice.sgst_paise + invoice.igst_paise
    if abs(invoice.total_tax_paise - components_tax_sum) > 100:
        diff_comp = abs(invoice.total_tax_paise - components_tax_sum)
        errors.append(
            f"Printed total tax {format_inr(invoice.total_tax_paise)} disagrees with "
            f"CGST {format_inr(invoice.cgst_paise)} + SGST {format_inr(invoice.sgst_paise)} + "
            f"IGST {format_inr(invoice.igst_paise)} ({format_inr(components_tax_sum)}) "
            f"by {format_inr(diff_comp)}."
        )

    lines_tax_sum = sum(l.tax_paise for l in invoice.line_items)
    if abs(invoice.total_tax_paise - lines_tax_sum) > 100:
        diff_ltax = abs(invoice.total_tax_paise - lines_tax_sum)
        errors.append(
            f"Printed total tax {format_inr(invoice.total_tax_paise)} disagrees with sum of line "
            f"taxes {format_inr(lines_tax_sum)} by {format_inr(diff_ltax)}."
        )

    expected_grand = invoice.subtotal_paise + invoice.total_tax_paise
    diff_grand = abs(invoice.grand_total_paise - expected_grand)
    if diff_grand > 100:
        if invoice.grand_total_paise > expected_grand:
            errors.append(
                f"Printed grand total {format_inr(invoice.grand_total_paise)} exceeds subtotal "
                f"{format_inr(invoice.subtotal_paise)} plus total tax "
                f"{format_inr(invoice.total_tax_paise)} ({format_inr(expected_grand)}) "
                f"by {format_inr(diff_grand)}."
            )
        else:
            errors.append(
                f"Printed grand total {format_inr(invoice.grand_total_paise)} is less than subtotal "
                f"{format_inr(invoice.subtotal_paise)} plus total tax "
                f"{format_inr(invoice.total_tax_paise)} ({format_inr(expected_grand)}) "
                f"by {format_inr(diff_grand)}."
            )

    if errors:
        return CheckResult(
            check="extract.arith_total",
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            source=source,
            evidence=errors,
            refs=[invoice.invoice_id],
            details={
                "subtotal_paise": invoice.subtotal_paise,
                "total_tax_paise": invoice.total_tax_paise,
                "expected_grand_total_paise": expected_grand,
                "grand_total_paise": invoice.grand_total_paise,
                "discrepancy_paise": diff_grand,
            },
        )

    return CheckResult(
        check="extract.arith_total",
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        source=source,
        evidence=[
            f"Subtotal {format_inr(invoice.subtotal_paise)} plus total tax "
            f"{format_inr(invoice.total_tax_paise)} equals grand total "
            f"{format_inr(invoice.grand_total_paise)} within ₹1 rounding tolerance."
        ],
        refs=[invoice.invoice_id],
        details={
            "subtotal_paise": invoice.subtotal_paise,
            "total_tax_paise": invoice.total_tax_paise,
            "expected_grand_total_paise": expected_grand,
            "grand_total_paise": invoice.grand_total_paise,
            "discrepancy_paise": diff_grand,
        },
    )


def check_tax_regime(invoice: Invoice, source: CheckSource = CheckSource.LIVE) -> CheckResult:
    """`extract.tax_regime` — Intra-state CGST+SGST vs inter-state IGST consistency (SEZ/exports -> warn)."""
    seller_state = invoice.seller_gstin.strip()[:2]
    pos_state = invoice.place_of_supply_state.strip()

    has_cgst_sgst = (
        invoice.cgst_paise > 0
        or invoice.sgst_paise > 0
        or any(l.cgst_bps > 0 or l.sgst_bps > 0 for l in invoice.line_items)
    )
    has_igst = invoice.igst_paise > 0 or any(l.igst_bps > 0 for l in invoice.line_items)

    if invoice.is_export_or_sez:
        return CheckResult(
            check="extract.tax_regime",
            status=CheckStatus.WARN,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[
                f"Invoice {invoice.invoice_no} is flagged as SEZ/export supply "
                f"(seller state {seller_state}, place of supply {pos_state}, "
                f"IGST {format_inr(invoice.igst_paise)}, CGST {format_inr(invoice.cgst_paise)}, "
                f"SGST {format_inr(invoice.sgst_paise)}); routed to human review for LUT/SEZ endorsement."
            ],
            refs=[invoice.invoice_id],
            details={
                "seller_state": seller_state,
                "place_of_supply_state": pos_state,
                "is_export_or_sez": True,
            },
        )

    errors: list[str] = []
    if seller_state == pos_state:
        # Intra-state supply: requires CGST + SGST, no IGST
        if has_igst:
            errors.append(
                f"Intra-state supply (seller state {seller_state} == place of supply {pos_state}) "
                f"improperly charges IGST {format_inr(invoice.igst_paise)} instead of CGST + SGST."
            )
        if not has_cgst_sgst:
            errors.append(
                f"Intra-state supply (seller state {seller_state} == place of supply {pos_state}) "
                f"is missing CGST and SGST charges (total tax {format_inr(invoice.total_tax_paise)})."
            )
        if abs(invoice.cgst_paise - invoice.sgst_paise) > 100:
            errors.append(
                f"Intra-state supply has unequal CGST {format_inr(invoice.cgst_paise)} and "
                f"SGST {format_inr(invoice.sgst_paise)}."
            )
    else:
        # Inter-state supply: requires IGST, no CGST/SGST
        if has_cgst_sgst:
            errors.append(
                f"Inter-state supply (seller state {seller_state} != place of supply {pos_state}) "
                f"improperly charges CGST {format_inr(invoice.cgst_paise)} + "
                f"SGST {format_inr(invoice.sgst_paise)} instead of IGST."
            )
        if not has_igst:
            errors.append(
                f"Inter-state supply (seller state {seller_state} != place of supply {pos_state}) "
                f"is missing IGST charges (total tax {format_inr(invoice.total_tax_paise)})."
            )

    if errors:
        return CheckResult(
            check="extract.tax_regime",
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            source=source,
            evidence=errors,
            refs=[invoice.invoice_id],
            details={
                "seller_state": seller_state,
                "place_of_supply_state": pos_state,
                "is_export_or_sez": False,
            },
        )

    regime_desc = (
        f"intra-state CGST {format_inr(invoice.cgst_paise)} + SGST {format_inr(invoice.sgst_paise)}"
        if seller_state == pos_state
        else f"inter-state IGST {format_inr(invoice.igst_paise)}"
    )
    return CheckResult(
        check="extract.tax_regime",
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        source=source,
        evidence=[
            f"Seller state {seller_state} and place of supply {pos_state} match "
            f"{regime_desc} tax regime."
        ],
        refs=[invoice.invoice_id],
        details={
            "seller_state": seller_state,
            "place_of_supply_state": pos_state,
            "is_export_or_sez": False,
        },
    )


def check_date_valid(
    invoice: Invoice,
    source: CheckSource = CheckSource.LIVE,
    max_allowed_date: str = "2026-12-31",
) -> CheckResult:
    """`extract.date_valid` — Invoice date not in future, inside `financial_year`, and `due_date >= invoice_date`."""
    errors: list[str] = []

    try:
        inv_dt = date.fromisoformat(invoice.invoice_date)
    except ValueError:
        inv_dt = None
        errors.append(f"Invoice date {invoice.invoice_date!r} is not a valid calendar date.")

    try:
        due_dt = date.fromisoformat(invoice.due_date)
    except ValueError:
        due_dt = None
        errors.append(f"Due date {invoice.due_date!r} is not a valid calendar date.")

    if inv_dt is not None and invoice.invoice_date > max_allowed_date:
        errors.append(
            f"Invoice date {invoice.invoice_date} is in the future relative to reference cutoff {max_allowed_date}."
        )

    fy_bounds = parse_financial_year_bounds(invoice.financial_year)
    if fy_bounds is None:
        errors.append(
            f"Printed financial year {invoice.financial_year!r} could not be parsed as a valid Indian FY (e.g. 2026-27)."
        )
    else:
        fy_start, fy_end = fy_bounds
        if inv_dt is not None and not (fy_start <= invoice.invoice_date <= fy_end):
            errors.append(
                f"Invoice date {invoice.invoice_date} falls outside printed financial year "
                f"{invoice.financial_year} ({fy_start} to {fy_end})."
            )

    if inv_dt is not None and due_dt is not None and invoice.due_date < invoice.invoice_date:
        errors.append(
            f"Due date {invoice.due_date} precedes invoice date {invoice.invoice_date}."
        )

    if errors:
        return CheckResult(
            check="extract.date_valid",
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            source=source,
            evidence=errors,
            refs=[invoice.invoice_id],
            details={
                "invoice_date": invoice.invoice_date,
                "due_date": invoice.due_date,
                "financial_year": invoice.financial_year,
            },
        )

    return CheckResult(
        check="extract.date_valid",
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        source=source,
        evidence=[
            f"Invoice date {invoice.invoice_date} falls within financial year {invoice.financial_year} "
            f"and due date {invoice.due_date} is on or after invoice date."
        ],
        refs=[invoice.invoice_id],
        details={
            "invoice_date": invoice.invoice_date,
            "due_date": invoice.due_date,
            "financial_year": invoice.financial_year,
        },
    )


def check_hsn_sac(invoice: Invoice, source: CheckSource = CheckSource.LIVE) -> CheckResult:
    """`extract.hsn_sac` — HSN codes on goods lines (`is_service=False`), SAC (`99...`) on service lines (`is_service=True`)."""
    errors: list[str] = []
    for line in invoice.line_items:
        code = line.hsn_sac.strip()
        if line.is_service:
            if not SAC_SERVICE_RE.match(code):
                errors.append(
                    f"Line {line.line_no} ('{line.description}') is marked as a service (is_service=True) "
                    f"but carries non-SAC code '{code}' (expected 4-8 digit SAC starting with '99')."
                )
        else:
            if not HSN_GOODS_RE.match(code):
                errors.append(
                    f"Line {line.line_no} ('{line.description}') is marked as goods (is_service=False) "
                    f"but carries invalid or service code '{code}' (expected 4-8 digit HSN not starting with '99')."
                )

    if errors:
        return CheckResult(
            check="extract.hsn_sac",
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            source=source,
            evidence=errors,
            refs=[invoice.invoice_id],
            details={"line_count": len(invoice.line_items), "error_count": len(errors)},
        )

    summary = ", ".join(
        f"line {l.line_no} {'SAC' if l.is_service else 'HSN'} {l.hsn_sac}" for l in invoice.line_items
    )
    return CheckResult(
        check="extract.hsn_sac",
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        source=source,
        evidence=[
            f"All {len(invoice.line_items)} line item(s) carry valid goods HSN or services SAC codes ({summary})."
        ],
        refs=[invoice.invoice_id],
        details={"line_count": len(invoice.line_items), "error_count": 0},
    )


def run_invoice_validators(
    invoice: Invoice,
    source: CheckSource = CheckSource.LIVE,
) -> list[CheckResult]:
    """Run all 7 deterministic consistency checks on an `Invoice`."""
    return [
        check_gstin_format(invoice, source=source),
        check_gstin_checksum(invoice, source=source),
        check_arith_lines(invoice, source=source),
        check_arith_total(invoice, source=source),
        check_tax_regime(invoice, source=source),
        check_date_valid(invoice, source=source),
        check_hsn_sac(invoice, source=source),
    ]

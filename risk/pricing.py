"""Exact integer-paise risk-based pricing and discount calculation with round-half-up arithmetic."""

from __future__ import annotations

from contract.models import Grade, PricingQuote, format_inr

ADVANCE_RATE_BPS_BY_GRADE: dict[Grade, int] = {
    Grade.A: 9000,
    Grade.B: 8500,
    Grade.C: 7500,
    Grade.D: 6000,
    Grade.E: 0,
}

ANNUAL_RATE_BPS_BY_GRADE: dict[Grade, int] = {
    Grade.A: 1000,
    Grade.B: 1200,
    Grade.C: 1400,
    Grade.D: 1700,
    Grade.E: 0,
}

POLICY_NOTICE = "Illustrative prototype policy, not validated lending prices."


def compute_pricing_quote(
    principal_paise: int,
    grade: Grade,
    tenor_days: int,
) -> PricingQuote:
    """Compute exact integer-paise advance, discount, and net disbursement using round-half-up."""
    if not isinstance(principal_paise, int) or isinstance(principal_paise, bool) or principal_paise < 0:
        raise ValueError(f"principal_paise must be a non-negative integer, got {principal_paise!r}")
    if not isinstance(tenor_days, int) or isinstance(tenor_days, bool) or tenor_days < 0:
        raise ValueError(f"tenor_days must be a non-negative integer, got {tenor_days!r}")

    advance_rate_bps = ADVANCE_RATE_BPS_BY_GRADE[grade]
    annual_rate_bps = ANNUAL_RATE_BPS_BY_GRADE[grade]

    if grade == Grade.E or advance_rate_bps == 0:
        return PricingQuote(
            grade=grade,
            advance_rate_bps=0,
            advance_amount_paise=0,
            annual_rate_bps=0,
            tenor_days=tenor_days,
            discount_paise=0,
            net_disbursement_paise=0,
            arithmetic_trace=(
                f"Grade E decline on principal {format_inr(principal_paise)} ({principal_paise} paise): "
                "advance_rate_bps = 0, advance_amount_paise = 0, discount_paise = 0, net_disbursement_paise = 0."
            ),
            policy_notice=POLICY_NOTICE,
        )

    advance_amount_paise = (principal_paise * advance_rate_bps + 5000) // 10000
    numerator = advance_amount_paise * annual_rate_bps * tenor_days
    denominator = 10000 * 365
    discount_paise = (numerator + (denominator // 2)) // denominator
    net_disbursement_paise = advance_amount_paise - discount_paise

    adv_pct_whole = advance_rate_bps // 100
    ann_pct_whole = annual_rate_bps // 100
    trace = (
        f"Principal {format_inr(principal_paise)} ({principal_paise} paise) @ Grade {grade.value} "
        f"{adv_pct_whole}% advance ({advance_rate_bps} bps) = {format_inr(advance_amount_paise)} "
        f"({advance_amount_paise} paise); Discount @ {ann_pct_whole}% p.a. ({annual_rate_bps} bps) "
        f"for {tenor_days} days = ({advance_amount_paise} * {annual_rate_bps} * {tenor_days} + "
        f"{denominator // 2}) // {denominator} = {format_inr(discount_paise)} ({discount_paise} paise); "
        f"Net disbursement = {format_inr(net_disbursement_paise)} ({net_disbursement_paise} paise)."
    )

    return PricingQuote(
        grade=grade,
        advance_rate_bps=advance_rate_bps,
        advance_amount_paise=advance_amount_paise,
        annual_rate_bps=annual_rate_bps,
        tenor_days=tenor_days,
        discount_paise=discount_paise,
        net_disbursement_paise=net_disbursement_paise,
        arithmetic_trace=trace,
        policy_notice=POLICY_NOTICE,
    )

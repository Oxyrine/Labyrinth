"""Stage 3 check runner (`risk.grade`) producing buyer/receivables risk grade and pricing quote."""

from __future__ import annotations

from contract.context import CaseContext
from contract.models import (
    CheckResult,
    CheckSource,
    CheckStatus,
    Grade,
    Severity,
    VerificationStatus,
    format_inr,
)
from risk.features import compute_features
from risk.pricing import compute_pricing_quote
from risk.scorecard import evaluate_scorecard


def run(ctx: CaseContext) -> list[CheckResult]:
    """Run buyer & receivables risk evaluation and emit `risk.grade`."""
    features = compute_features(ctx)
    score_points, grade, top_reasons = evaluate_scorecard(features)

    principal_paise = min(ctx.request.requested_amount_paise, ctx.invoice.grand_total_paise)
    pricing = compute_pricing_quote(
        principal_paise=principal_paise,
        grade=grade,
        tenor_days=ctx.request.tenor_days,
    )

    if grade in (Grade.A, Grade.B):
        severity = Severity.INFO
    elif grade == Grade.C:
        severity = Severity.LOW
    elif grade == Grade.D:
        severity = Severity.MEDIUM
    else:
        severity = Severity.HIGH

    summary_line = (
        f"Assigned Grade {grade.value} ({score_points}/100 points) for buyer {ctx.invoice.buyer_id} "
        f"on {format_inr(principal_paise)} principal: advance {format_inr(pricing.advance_amount_paise)} "
        f"({pricing.advance_rate_bps // 100}%), {pricing.tenor_days}-day discount "
        f"{format_inr(pricing.discount_paise)} ({pricing.annual_rate_bps // 100}% p.a.), "
        f"net disbursement {format_inr(pricing.net_disbursement_paise)}."
    )
    evidence = [summary_line, *top_reasons]

    return [
        CheckResult(
            check="risk.grade",
            status=CheckStatus.PASS,
            severity=severity,
            source=CheckSource.LIVE,
            evidence=evidence,
            refs=[ctx.invoice.invoice_id, ctx.request.request_id, ctx.invoice.buyer_id],
            verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
            details={
                "grade": grade.value,
                "score_points": score_points,
                "pricing": pricing.model_dump(mode="json"),
                "top_reasons": top_reasons,
                "features": features.to_int_summary(),
            },
        )
    ]

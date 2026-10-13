"""Strict precedence decision engine mapping stage CheckResults to a Decision."""

from __future__ import annotations

from datetime import datetime, timezone

from contract.context import CaseContext
from contract.models import (
    CheckResult,
    CheckStatus,
    Decision,
    Grade,
    Outcome,
    PricingQuote,
    RequestState,
    Severity,
    Tier,
    VersionKind,
)
from risk.features import compute_features, extract_network_tier
from risk.pricing import compute_pricing_quote
from risk.scorecard import evaluate_scorecard
from workflow.states import transition_state

REQUIRED_CHECK_PREFIXES: tuple[str, ...] = (
    "extract.",
    "match.",
    "dedup.",
    "network.ring",
    "risk.grade",
)

OPTIONAL_UNAVAILABLE_CHECKS: frozenset[str] = frozenset(
    {
        "extract.tamper_metadata",
    }
)


def is_required_check(check_id: str) -> bool:
    """Return True if `check_id` is a required check whose `unavailable` status must trigger review."""
    if check_id in OPTIONAL_UNAVAILABLE_CHECKS:
        return False
    return check_id.startswith(REQUIRED_CHECK_PREFIXES)


def is_hard_fail(chk: CheckResult) -> bool:
    """Return True if `chk` represents a hard failure requiring immediate rejection."""
    if chk.status != CheckStatus.FAIL:
        return False
    if chk.severity == Severity.HARD:
        return True
    if chk.check.startswith(("extract.", "match.", "dedup.")):
        return True
    return False


def _extract_risk_metadata(
    ctx: CaseContext,
    checks: list[CheckResult],
) -> tuple[Grade, int, PricingQuote, list[str], Tier]:
    network_tier = extract_network_tier(ctx)
    for chk in checks:
        if chk.check == "network.ring":
            raw_tier = chk.details.get("tier") or chk.details.get("network_tier")
            if isinstance(raw_tier, str) and raw_tier.lower() in ("low", "medium", "high"):
                network_tier = Tier(raw_tier.lower())
            elif chk.status == CheckStatus.FAIL:
                network_tier = Tier.HIGH
            elif chk.status == CheckStatus.WARN:
                network_tier = (
                    Tier.HIGH
                    if chk.severity in (Severity.HIGH, Severity.HARD)
                    else Tier.MEDIUM
                )

    for chk in checks:
        if chk.check == "risk.grade" and chk.status != CheckStatus.UNAVAILABLE:
            det = chk.details
            if "grade" in det and "score_points" in det and "pricing" in det:
                grade = Grade(det["grade"])
                score_points = int(det["score_points"])
                pricing = PricingQuote.model_validate(det["pricing"])
                top_reasons = list(det.get("top_reasons", chk.evidence[:3]))
                return grade, score_points, pricing, top_reasons, network_tier

    # Fallback calculation if risk.grade was unavailable or not yet in checks
    ctx_with_net = ctx
    if "network" not in ctx.stage_outputs:
        net_checks = [c for c in checks if c.check.startswith("network.")]
        ctx_with_net = ctx.model_copy(
            update={"stage_outputs": {**ctx.stage_outputs, "network": net_checks}}
        )
    features = compute_features(ctx_with_net)
    score_points, grade, top_reasons = evaluate_scorecard(features)
    principal_paise = min(ctx.request.requested_amount_paise, ctx.invoice.grand_total_paise)
    pricing = compute_pricing_quote(
        principal_paise=principal_paise,
        grade=grade,
        tenor_days=ctx.request.tenor_days,
    )
    return grade, score_points, pricing, top_reasons, network_tier


def evaluate(
    ctx: CaseContext,
    checks: list[CheckResult],
    *,
    stage_latencies_ms: dict[str, int] | None = None,
    decided_at: str | None = None,
    disable_network: bool = False,
) -> Decision:
    """Evaluate all stage CheckResults under strict precedence rules (1..4) and return a Decision."""
    grade, score_points, pricing, top_reasons, network_tier = _extract_risk_metadata(ctx, checks)
    if disable_network:
        network_tier = Tier.LOW

    # Advance request state through pipeline stages:
    # submitted -> extracted -> verified -> network_checked -> scored -> {cond_approved, in_review, rejected}
    req_id = ctx.request.request_id
    ts = decided_at or ctx.request.submitted_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur_state = RequestState.SUBMITTED
    for next_stage_state in (
        RequestState.EXTRACTED,
        RequestState.VERIFIED,
        RequestState.NETWORK_CHECKED,
        RequestState.SCORED,
    ):
        cur_state = transition_state(
            cur_state,
            next_stage_state,
            entity_id=req_id,
            actor_id="pipeline",
            timestamp=ts,
        )

    conditions: list[str] = []

    # Check presence of required stage check categories
    has_extract = any(c.check.startswith("extract.") and c.check != "extract.tamper_metadata" for c in checks)
    has_match = any(c.check.startswith("match.") for c in checks)
    has_dedup = any(c.check.startswith("dedup.") for c in checks)
    has_network = disable_network or any(c.check == "network.ring" for c in checks)
    has_risk = any(c.check == "risk.grade" for c in checks)

    has_unavailable_required = (
        not (has_extract and has_match and has_dedup and has_network and has_risk)
        or any(
            c.status == CheckStatus.UNAVAILABLE and is_required_check(c.check)
            for c in checks
        )
    )

    has_unexplained_version_change = (
        ctx.version.kind == VersionKind.UNEXPLAINED_CHANGE
        or any(
            c.check == "dedup.identity"
            and (
                c.status == CheckStatus.WARN
                or c.details.get("kind") == VersionKind.UNEXPLAINED_CHANGE.value
            )
            for c in checks
        )
    )
    has_near_dup = any(
        c.check == "dedup.near_dup" and c.status == CheckStatus.WARN for c in checks
    )
    has_any_warn_or_soft_fail = any(
        c.status == CheckStatus.WARN or (c.status == CheckStatus.FAIL and not is_hard_fail(c))
        for c in checks
    )

    # Strict Precedence Rule 1: Any hard fail -> REJECT
    if any(is_hard_fail(c) for c in checks):
        outcome = Outcome.REJECT
        target_state = RequestState.REJECTED
    # Strict Precedence Rule 2: Any required check returning UNAVAILABLE -> REVIEW ("request missing evidence")
    elif has_unavailable_required:
        outcome = Outcome.REVIEW
        target_state = RequestState.IN_REVIEW
        conditions.append("request missing evidence")
    # Strict Precedence Rule 3: Unexplained version change, near duplicate, any WARN, or network_tier in (MEDIUM, HIGH) -> REVIEW
    elif (
        has_unexplained_version_change
        or has_near_dup
        or has_any_warn_or_soft_fail
        or network_tier in (Tier.MEDIUM, Tier.HIGH)
    ):
        outcome = Outcome.REVIEW
        target_state = RequestState.IN_REVIEW
        if has_unexplained_version_change:
            conditions.append("verify_amendment_diff")
        if has_near_dup:
            conditions.append("verify_near_duplicate")
        if network_tier in (Tier.MEDIUM, Tier.HIGH):
            conditions.append("investigate_network_ring")
        if not conditions:
            conditions.append("manual_underwriter_review")
    # Strict Precedence Rule 4: All required checks pass -> Grade A/B/C -> CONDITIONALLY_APPROVED; D -> REVIEW; E -> REJECT
    else:
        if grade in (Grade.A, Grade.B, Grade.C):
            outcome = Outcome.CONDITIONALLY_APPROVED
            target_state = RequestState.COND_APPROVED
            conditions.extend(["buyer_acceptance", "reservation_confirm"])
        elif grade == Grade.D:
            outcome = Outcome.REVIEW
            target_state = RequestState.IN_REVIEW
            conditions.append("manual_underwriter_review")
        else:
            outcome = Outcome.REJECT
            target_state = RequestState.REJECTED

    final_state = transition_state(
        cur_state,
        target_state,
        entity_id=req_id,
        actor_id="decision_engine",
        timestamp=ts,
    )

    # Combine check failure/warn reasons with scorecard top reasons so top_reasons always explains the outcome
    merged_reasons: list[str] = []
    for chk in checks:
        if chk.status in (CheckStatus.FAIL, CheckStatus.UNAVAILABLE, CheckStatus.WARN):
            for ev in chk.evidence:
                if ev not in merged_reasons:
                    merged_reasons.append(ev)
    for r in top_reasons:
        if r not in merged_reasons:
            merged_reasons.append(r)

    dec_suffix = req_id.removeprefix("REQ-")
    return Decision(
        decision_id=f"DEC-{dec_suffix}",
        request_id=ctx.request.request_id,
        invoice_id=ctx.invoice.invoice_id,
        version_id=ctx.version.version_id,
        outcome=outcome,
        state=final_state,
        grade=grade,
        score_points=score_points,
        network_tier=network_tier,
        pricing=pricing,
        top_reasons=merged_reasons[:3],
        conditions=conditions,
        checks=list(checks),
        stage_latencies_ms=dict(stage_latencies_ms or {}),
        overridden_by=[],
        decided_at=ts,
    )

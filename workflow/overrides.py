"""Human underwriter override controls and post-override disbursement invariant gates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from contract.models import (
    BuyerAcceptance,
    CheckStatus,
    Decision,
    FinancingRequest,
    Outcome,
    Override,
    RequestState,
    Reservation,
    ReservationState,
    Severity,
)
from workflow.decision import is_hard_fail
from workflow.states import transition_state


class OverrideViolationError(PermissionError, ValueError):
    """Raised when an override violates dual-reviewer or reason requirements."""


class DisbursementInvariantError(PermissionError, ValueError):
    """Raised when any disbursement invariant fails on `disburse()`."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_valid_hex_signature(sig_hex: str | None) -> bool:
    if not sig_hex or not isinstance(sig_hex, str):
        return False
    cleaned = sig_hex.strip()
    if len(cleaned) < 64 or len(cleaned) % 2 != 0:
        return False
    try:
        bytes.fromhex(cleaned)
    except ValueError:
        return False
    return True


def _decision_has_hard_fail(decision: Decision) -> bool:
    for chk in decision.checks:
        if chk.status == CheckStatus.FAIL and chk.severity == Severity.HARD:
            return True
        if is_hard_fail(chk):
            return True
    return False


def _outcome_to_state(outcome: Outcome) -> RequestState:
    if outcome == Outcome.CONDITIONALLY_APPROVED:
        return RequestState.COND_APPROVED
    if outcome == Outcome.REVIEW:
        return RequestState.IN_REVIEW
    return RequestState.REJECTED


def apply_override(
    decision: Decision,
    reviewer_id: str,
    new_outcome: Outcome,
    reason: str,
    second_reviewer_id: str | None = None,
    *,
    overridden_at: str | None = None,
) -> Decision:
    """Apply an audited human override to `decision`, enforcing dual distinct reviewers on hard fails."""
    if not isinstance(new_outcome, Outcome):
        new_outcome = Outcome(new_outcome)

    if not reviewer_id or not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise OverrideViolationError("Override requires a non-empty reviewer_id.")
    reviewer_clean = reviewer_id.strip()

    if not reason or not isinstance(reason, str) or len(reason.strip()) < 3:
        raise OverrideViolationError(
            "Override requires a written reason of at least 3 non-whitespace characters."
        )
    reason_clean = reason.strip()

    second_clean: str | None = None
    if second_reviewer_id is not None:
        if not isinstance(second_reviewer_id, str) or not second_reviewer_id.strip():
            raise OverrideViolationError("second_reviewer_id, when provided, must be non-empty.")
        second_clean = second_reviewer_id.strip()

    has_hard_fail = _decision_has_hard_fail(decision)
    if has_hard_fail and new_outcome in (Outcome.REVIEW, Outcome.CONDITIONALLY_APPROVED):
        if second_clean is None:
            raise PermissionError(
                "Overriding a hard-fail check to review or conditionally_approved requires a second_reviewer_id."
            )
        if second_clean == reviewer_clean:
            raise PermissionError(
                f"second_reviewer_id ({second_clean!r}) must be distinct from reviewer_id ({reviewer_clean!r})."
            )

    ts = overridden_at or _now_iso()
    target_state = _outcome_to_state(new_outcome)
    if decision.state != target_state:
        transition_state(
            decision.state,
            target_state,
            entity_id=decision.request_id,
            actor_id=reviewer_clean,
            reason=reason_clean,
            is_override=True,
            timestamp=ts,
            extra_payload={
                "prior_outcome": decision.outcome.value,
                "new_outcome": new_outcome.value,
                "second_reviewer_id": second_clean,
            },
        )

    hard_fail_checks = [
        c.check
        for c in decision.checks
        if (c.status == CheckStatus.FAIL and c.severity == Severity.HARD) or is_hard_fail(c)
    ]
    scores_snapshot: dict[str, Any] = {
        "grade": decision.grade.value,
        "score_points": decision.score_points,
        "network_tier": decision.network_tier.value,
        "prior_outcome": decision.outcome.value,
        "prior_state": decision.state.value,
        "hard_fail_checks": hard_fail_checks,
    }

    override_seq = len(decision.overridden_by) + 1
    override_rec = Override(
        override_id=f"OVR-{decision.request_id.removeprefix('REQ-')}-{override_seq:02d}",
        reviewer_id=reviewer_clean,
        second_reviewer_id=second_clean,
        reason=reason_clean,
        prior_outcome=decision.outcome,
        new_outcome=new_outcome,
        scores_snapshot=scores_snapshot,
        overridden_at=ts,
    )

    new_conditions = list(decision.conditions)
    if new_outcome == Outcome.CONDITIONALLY_APPROVED:
        for req_cond in ("buyer_acceptance", "reservation_confirm"):
            if req_cond not in new_conditions:
                new_conditions.append(req_cond)

    return decision.model_copy(
        update={
            "outcome": new_outcome,
            "state": target_state,
            "conditions": new_conditions,
            "overridden_by": [*decision.overridden_by, override_rec],
        }
    )


def disburse(
    decision: Decision,
    reservation: Reservation | None,
    buyer_acceptance: BuyerAcceptance | None,
    outstanding_paise: int,
    requesting_lender_id: str,
    request: FinancingRequest | None = None,
    *,
    disbursed_at: str | None = None,
) -> Decision:
    """Re-verify all disbursement invariants after any overrides and transition to DISBURSED."""
    if not isinstance(outstanding_paise, int) or isinstance(outstanding_paise, bool):
        raise DisbursementInvariantError("outstanding_paise must be a strict integer.")

    if not requesting_lender_id or not isinstance(requesting_lender_id, str) or not requesting_lender_id.strip():
        raise DisbursementInvariantError("requesting_lender_id must be a non-empty string.")
    lender_clean = requesting_lender_id.strip()

    # Invariant 0: If decision had any hard fail, verify that a valid dual-reviewer override exists
    if _decision_has_hard_fail(decision):
        valid_dual_override = any(
            ovr.new_outcome in (Outcome.REVIEW, Outcome.CONDITIONALLY_APPROVED)
            and ovr.second_reviewer_id is not None
            and ovr.second_reviewer_id.strip() != ""
            and ovr.second_reviewer_id.strip() != ovr.reviewer_id.strip()
            for ovr in decision.overridden_by
        )
        if not valid_dual_override:
            raise DisbursementInvariantError(
                "Cannot disburse: decision contains a hard-fail check without a distinct second-reviewer override."
            )

    # Invariant 1: decision.outcome == Outcome.CONDITIONALLY_APPROVED
    if decision.outcome != Outcome.CONDITIONALLY_APPROVED:
        raise DisbursementInvariantError(
            f"Cannot disburse when decision.outcome is {decision.outcome.value!r} (must be 'conditionally_approved')."
        )

    if decision.state not in (
        RequestState.COND_APPROVED,
        RequestState.BUYER_ACCEPTED,
        RequestState.RESERVATION_CONFIRMED,
    ):
        raise DisbursementInvariantError(
            f"Cannot disburse from request state {decision.state.value!r}."
        )

    # Invariant 2: reservation exists, state == FINANCED, and owned by requesting_lender_id
    if reservation is None:
        raise DisbursementInvariantError("Cannot disburse without an active consortium Reservation.")
    if reservation.state != ReservationState.FINANCED:
        raise DisbursementInvariantError(
            f"Reservation {reservation.reservation_id} state is {reservation.state.value!r}, expected 'financed'."
        )
    if reservation.lender_id != lender_clean:
        raise DisbursementInvariantError(
            f"Reservation {reservation.reservation_id} is owned by lender {reservation.lender_id!r}, "
            f"not requesting lender {lender_clean!r}."
        )
    if reservation.request_id != decision.request_id:
        raise DisbursementInvariantError(
            f"Reservation request_id {reservation.request_id!r} does not match decision request_id {decision.request_id!r}."
        )
    if reservation.amount_paise <= 0:
        raise DisbursementInvariantError("Reservation amount_paise must be strictly positive.")

    # Invariant 3: capacity check (reservation.amount_paise <= outstanding_paise)
    if outstanding_paise <= 0 or reservation.amount_paise > outstanding_paise:
        raise DisbursementInvariantError(
            f"Reservation amount {reservation.amount_paise} paise exceeds outstanding capacity {outstanding_paise} paise."
        )

    # Invariant 4: buyer_acceptance exists, decision == 'accept', valid signature, covers reservation amount
    if buyer_acceptance is None:
        raise DisbursementInvariantError("Cannot disburse without a signed BuyerAcceptance.")
    if buyer_acceptance.decision != "accept":
        raise DisbursementInvariantError(
            f"BuyerAcceptance decision is {buyer_acceptance.decision!r}, expected 'accept'."
        )
    if buyer_acceptance.invoice_id != decision.invoice_id:
        raise DisbursementInvariantError(
            f"BuyerAcceptance invoice_id {buyer_acceptance.invoice_id!r} does not match decision invoice_id {decision.invoice_id!r}."
        )
    if buyer_acceptance.version_id != decision.version_id:
        raise DisbursementInvariantError(
            f"BuyerAcceptance version_id {buyer_acceptance.version_id!r} does not match decision version_id {decision.version_id!r}."
        )
    if buyer_acceptance.accepted_amount_paise < reservation.amount_paise:
        raise DisbursementInvariantError(
            f"Buyer accepted amount {buyer_acceptance.accepted_amount_paise} paise is less than reservation amount {reservation.amount_paise} paise."
        )
    if not _is_valid_hex_signature(buyer_acceptance.signature_hex):
        raise DisbursementInvariantError("BuyerAcceptance signature_hex is missing or invalid hex.")

    # Invariant 5: request.seller_signature_hex valid when request is supplied
    if request is not None:
        if request.request_id != decision.request_id:
            raise DisbursementInvariantError("FinancingRequest request_id mismatch.")
        if request.lender_id != lender_clean:
            raise DisbursementInvariantError(
                f"FinancingRequest lender_id {request.lender_id!r} does not match requesting lender {lender_clean!r}."
            )
        if not _is_valid_hex_signature(request.seller_signature_hex):
            raise DisbursementInvariantError("FinancingRequest seller_signature_hex is missing or invalid hex.")

    ts = disbursed_at or _now_iso()
    cur_state = decision.state
    if cur_state == RequestState.COND_APPROVED:
        cur_state = transition_state(
            cur_state,
            RequestState.BUYER_ACCEPTED,
            entity_id=decision.request_id,
            actor_id=buyer_acceptance.buyer_id,
            timestamp=ts,
        )
    if cur_state == RequestState.BUYER_ACCEPTED:
        cur_state = transition_state(
            cur_state,
            RequestState.RESERVATION_CONFIRMED,
            entity_id=decision.request_id,
            actor_id=lender_clean,
            timestamp=ts,
        )
    if cur_state == RequestState.RESERVATION_CONFIRMED:
        cur_state = transition_state(
            cur_state,
            RequestState.DISBURSED,
            entity_id=decision.request_id,
            actor_id=lender_clean,
            timestamp=ts,
            extra_payload={
                "reservation_id": reservation.reservation_id,
                "acceptance_id": buyer_acceptance.acceptance_id,
                "disbursed_amount_paise": reservation.amount_paise,
            },
        )

    return decision.model_copy(update={"state": cur_state})

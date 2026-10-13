"""Comprehensive unit, fixture, exception-guard, and 100-sequence property tests for `workflow/`."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest

from contract.context import CaseContext, InMemoryHistoryView
from contract.models import (
    BuyerAcceptance,
    CheckResult,
    CheckSource,
    CheckStatus,
    Decision,
    Grade,
    Outcome,
    RequestState,
    Reservation,
    ReservationState,
    Severity,
    Tier,
    VersionKind,
)
from workflow.card import build_explanation_card
from workflow.decision import evaluate
from workflow.overrides import (
    DisbursementInvariantError,
    apply_override,
    disburse,
)
from workflow.pipeline import (
    load_case_context_from_fixture,
    run_batch_regime,
    run_case_from_fixture,
    run_pipeline,
)
from workflow.states import (
    IllegalStateTransition,
    clear_recorded_state_events,
    get_recorded_state_events,
    transition_state,
)

ROOT = Path(__file__).resolve().parent.parent


def test_fixtures_match_expected_outcomes() -> None:
    """Run all fixture cases in `fixtures/` and verify outcome matches expected.json."""
    fixtures_dir = ROOT / "fixtures"
    expected_files = sorted(fixtures_dir.glob("*/expected.json"))
    assert len(expected_files) >= 2

    for exp_file in expected_files:
        case_dir = exp_file.parent
        case_file = case_dir / "case.json"
        if not case_file.exists():
            continue
        expected = json.loads(exp_file.read_text(encoding="utf-8"))
        decision = run_case_from_fixture(case_dir)
        assert decision.outcome.value == expected["expected_outcome"], (
            f"Fixture {case_dir.name}: expected outcome {expected['expected_outcome']!r}, "
            f"got {decision.outcome.value!r} (checks={[(c.check, c.status.value) for c in decision.checks]})"
        )
        for req_fail in expected.get("failing_checks", []):
            assert any(
                c.check == req_fail and c.status == CheckStatus.FAIL for c in decision.checks
            ), f"Fixture {case_dir.name}: expected failing check {req_fail!r} not found in {decision.checks}"


def test_docs_unavailable_is_never_approved() -> None:
    """Verify docs_unavailable routes to review with condition 'request missing evidence' and is NEVER approved."""
    ctx = load_case_context_from_fixture(ROOT / "fixtures" / "clean_goods")
    # Simulate missing ERP/portal evidence documents
    ctx_no_docs = ctx.model_copy(
        update={
            "evidence": [],
            "history": InMemoryHistoryView(
                invoices=[ctx.invoice],
                versions=[ctx.version],
                payments=list(ctx.history.payments_by_buyer(ctx.invoice.buyer_id)),
                evidence_docs=[],
                metadata={"case_id": "docs_unavailable", "docs_unavailable": True},
            ),
        }
    )

    def _unavailable_match(_c: CaseContext) -> list[CheckResult]:
        return [
            CheckResult(
                check="match.three_way.parties",
                status=CheckStatus.UNAVAILABLE,
                severity=Severity.MEDIUM,
                source=CheckSource.FIXTURE,
                evidence=[
                    f"Buyer ERP endpoint timed out while fetching PO and GRN for {ctx.invoice.invoice_id}; routing to review."
                ],
                refs=[ctx.invoice.invoice_id],
            )
        ]

    decision = run_pipeline(ctx_no_docs, stage_runners={"match": _unavailable_match})
    assert decision.outcome == Outcome.REVIEW
    assert decision.outcome != Outcome.CONDITIONALLY_APPROVED
    assert decision.state == RequestState.IN_REVIEW
    assert "request missing evidence" in decision.conditions


def test_module_exception_becomes_unavailable_and_routes_to_review() -> None:
    """Verify unhandled module exceptions become CheckStatus.UNAVAILABLE and route to review, never pass."""
    ctx = load_case_context_from_fixture(ROOT / "fixtures" / "clean_goods")

    def _exploding_network(_c: CaseContext) -> list[CheckResult]:
        raise RuntimeError("Graph cycle budget worker crashed unexpectedly")

    decision = run_pipeline(ctx, stage_runners={"network": _exploding_network})
    assert decision.outcome == Outcome.REVIEW
    assert decision.state == RequestState.IN_REVIEW
    assert "request missing evidence" in decision.conditions

    net_checks = [c for c in decision.checks if c.check == "network.ring"]
    assert len(net_checks) == 1
    assert net_checks[0].status == CheckStatus.UNAVAILABLE
    assert net_checks[0].status != CheckStatus.PASS
    assert "RuntimeError" in net_checks[0].evidence[0]


def test_auto_cancel_reservation_on_reject() -> None:
    """Verify Stage 4 automatically calls registry.cancel(...) when decision outcome is REJECT."""
    ctx = load_case_context_from_fixture(ROOT / "fixtures" / "bad_arith")
    rsv = Reservation(
        reservation_id="RSV-FX-0002",
        invoice_identity=ctx.version.invoice_identity,
        request_id=ctx.request.request_id,
        lender_id=ctx.request.lender_id,
        seller_id=ctx.request.seller_id,
        amount_paise=ctx.request.requested_amount_paise,
        state=ReservationState.RESERVED,
        created_at="2026-08-12T10:05:00Z",
        expires_at="2026-08-13T10:05:00Z",
    )
    ctx.history.reservations.append(rsv)

    cancelled_calls: list[str] = []

    class DummyRegistry:
        def cancel(self, reservation_id: str, **kwargs: Any) -> None:
            cancelled_calls.append(reservation_id)

    decision = run_pipeline(ctx, registry=DummyRegistry())
    assert decision.outcome == Outcome.REJECT
    assert decision.state == RequestState.REJECTED
    assert "RSV-FX-0002" in cancelled_calls
    assert ctx.history.reservations[0].state == ReservationState.CANCELLED


def test_state_transition_machine_and_audit_events() -> None:
    """Verify §3.7 state transition graph, disputed rule, and hash-chained AuditEvents."""
    clear_recorded_state_events()
    s = RequestState.SUBMITTED
    for nxt in (
        RequestState.EXTRACTED,
        RequestState.VERIFIED,
        RequestState.NETWORK_CHECKED,
        RequestState.SCORED,
        RequestState.COND_APPROVED,
        RequestState.BUYER_ACCEPTED,
        RequestState.RESERVATION_CONFIRMED,
        RequestState.DISBURSED,
        RequestState.REPAID,
        RequestState.SETTLED,
    ):
        s = transition_state(s, nxt, entity_id="REQ-TEST-01", actor_id="tester")
    assert s == RequestState.SETTLED

    events = get_recorded_state_events()
    assert len(events) == 10
    assert events[0].prev_hash == "0" * 64
    for i in range(1, len(events)):
        assert events[i].prev_hash == events[i - 1].hash

    # Disputed is allowed from any state after buyer_accepted
    for post_accept_state in (
        RequestState.BUYER_ACCEPTED,
        RequestState.RESERVATION_CONFIRMED,
        RequestState.DISBURSED,
        RequestState.REPAID,
        RequestState.OVERDUE,
    ):
        assert (
            transition_state(post_accept_state, RequestState.DISPUTED, entity_id="REQ-DISP")
            == RequestState.DISPUTED
        )

    # Disputed is NOT allowed before buyer_accepted
    for pre_accept_state in (
        RequestState.SUBMITTED,
        RequestState.EXTRACTED,
        RequestState.SCORED,
        RequestState.COND_APPROVED,
        RequestState.IN_REVIEW,
    ):
        with pytest.raises(IllegalStateTransition):
            transition_state(pre_accept_state, RequestState.DISPUTED)

    # Settled is terminal
    with pytest.raises(IllegalStateTransition):
        transition_state(RequestState.SETTLED, RequestState.DISBURSED)


def test_decision_strict_precedence_order() -> None:
    """Verify precedence: 1 (hard fail) > 2 (required unavailable) > 3 (warn/network) > 4 (grade)."""
    ctx = load_case_context_from_fixture(ROOT / "fixtures" / "clean_goods")
    base_decision = run_case_from_fixture(ROOT / "fixtures" / "clean_goods")
    assert base_decision.outcome == Outcome.CONDITIONALLY_APPROVED
    assert base_decision.grade == Grade.B
    assert base_decision.conditions == ["buyer_acceptance", "reservation_confirm"]

    # Rule 3: Unexplained version change or near-dup -> REVIEW
    ctx_unexplained = ctx.model_copy(
        update={
            "version": ctx.version.model_copy(update={"kind": VersionKind.UNEXPLAINED_CHANGE})
        }
    )
    dec_unexplained = evaluate(ctx_unexplained, base_decision.checks)
    assert dec_unexplained.outcome == Outcome.REVIEW

    # Rule 2 beats Rule 3: Required unavailable + warn -> REVIEW with "request missing evidence"
    checks_unavail_and_warn = [
        *base_decision.checks,
        CheckResult(
            check="dedup.near_dup",
            status=CheckStatus.WARN,
            severity=Severity.MEDIUM,
            evidence=["Near duplicate invoice INV-O042 detected with Jaccard 0.91."],
        ),
        CheckResult(
            check="match.ewaybill",
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            evidence=["E-way bill portal unreachable for INV-FX-0001."],
        ),
    ]
    dec_unavail = evaluate(ctx, checks_unavail_and_warn)
    assert dec_unavail.outcome == Outcome.REVIEW
    assert "request missing evidence" in dec_unavail.conditions

    # Rule 1 beats Rule 2 & 3: Hard fail + unavailable + warn -> REJECT
    checks_hard_fail = [
        *checks_unavail_and_warn,
        CheckResult(
            check="extract.arith_total",
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            evidence=["Invoice grand total ₹10,50,000 mismatches subtotal + tax ₹10,00,000."],
        ),
    ]
    dec_reject = evaluate(ctx, checks_hard_fail)
    assert dec_reject.outcome == Outcome.REJECT
    assert dec_reject.state == RequestState.REJECTED


def test_explanation_card_deterministic_and_lesson_12_compliant() -> None:
    """Verify build_explanation_card includes all required fields and never uses 'CLEARED' or 'score:'."""
    ctx = load_case_context_from_fixture(ROOT / "fixtures" / "clean_goods")
    decision = run_pipeline(ctx)
    card = build_explanation_card(decision, ctx.invoice, ctx.parties)

    assert card["decision_id"] == decision.decision_id
    assert card["outcome"] == "conditionally_approved"
    assert card["grade"] == "B"
    assert "document_consistency" in card["three_property_banner"]
    assert "document_integrity" in card["three_property_banner"]
    assert "trade_authenticity" in card["three_property_banner"]
    assert card["generated_summary"] is None
    assert "₹16,767.12" in card["discount_arithmetic_trace"]
    assert card["source_badges"]["live_count"] >= 1

    serialized = json.dumps(card)
    assert "CLEARED" not in serialized
    assert "score:" not in serialized.lower()


def test_property_100_random_override_and_disburse_sequences() -> None:
    """Property test: 100 random override/disburse sequences NEVER violate safety invariants."""
    rng = random.Random(20261013)
    clean_ctx = load_case_context_from_fixture(ROOT / "fixtures" / "clean_goods")
    bad_ctx = load_case_context_from_fixture(ROOT / "fixtures" / "bad_arith")

    clean_base = run_pipeline(clean_ctx)
    hard_fail_base = run_pipeline(bad_ctx)
    assert clean_base.outcome == Outcome.CONDITIONALLY_APPROVED
    assert hard_fail_base.outcome == Outcome.REJECT

    reviewers = ["REV-ALICE", "REV-BOB", "REV-CHARLIE", ""]
    outcomes = [Outcome.REJECT, Outcome.REVIEW, Outcome.CONDITIONALLY_APPROVED]

    for seq_idx in range(100):
        # Start randomly from either a clean decision or a hard-fail decision
        has_hard_fail = rng.choice([True, False])
        dec = (hard_fail_base if has_hard_fail else clean_base).model_copy(deep=True)
        ctx = bad_ctx if has_hard_fail else clean_ctx

        # Apply 1..4 random override attempts
        num_steps = rng.randint(1, 4)
        for _ in range(num_steps):
            rev1 = rng.choice(reviewers)
            use_second = rng.choice([True, False])
            rev2 = rng.choice(reviewers) if use_second else None
            target_outcome = rng.choice(outcomes)
            reason = rng.choice(["", "ok", "Verified physical GRN and bank statement confirmation."])

            should_fail_override = False
            if not rev1.strip() or len(reason.strip()) < 3:
                should_fail_override = True
            elif rev2 is not None and not rev2.strip():
                should_fail_override = True
            elif has_hard_fail and target_outcome in (Outcome.REVIEW, Outcome.CONDITIONALLY_APPROVED):
                if rev2 is None or not rev2.strip() or rev2.strip() == rev1.strip():
                    should_fail_override = True

            if should_fail_override:
                with pytest.raises((PermissionError, ValueError)):
                    apply_override(dec, rev1, target_outcome, reason, second_reviewer_id=rev2)
            else:
                dec = apply_override(dec, rev1, target_outcome, reason, second_reviewer_id=rev2)
                assert dec.outcome == target_outcome
                assert len(dec.overridden_by) >= 1

        # Now attempt random disbursement with randomized reservation, capacity, and buyer acceptance
        outstanding_paise = rng.choice([0, 50_000_000, 100_000_000, 120_000_000])
        rsv_amount = rng.choice([50_000_000, 100_000_000, 110_000_000])
        rsv_state = rng.choice(
            [
                ReservationState.RESERVED,
                ReservationState.FINANCED,
                ReservationState.CANCELLED,
                ReservationState.EXPIRED,
            ]
        )
        rsv_lender = rng.choice(["L1", "L2"])
        req_lender = "L1"
        has_rsv = rng.choice([True, False])

        reservation = (
            Reservation(
                reservation_id=f"RSV-PROP-{seq_idx:03d}",
                invoice_identity=ctx.version.invoice_identity,
                request_id=dec.request_id,
                lender_id=rsv_lender,
                seller_id=ctx.invoice.seller_id,
                amount_paise=rsv_amount,
                state=rsv_state,
                created_at="2026-08-12T10:05:00Z",
                expires_at="2026-08-13T10:05:00Z",
            )
            if has_rsv
            else None
        )

        has_acceptance = rng.choice([True, False])
        buyer_decision = rng.choice(["accept", "reject"])
        valid_sig = rng.choice([True, False])
        accepted_amount = rng.choice([40_000_000, 100_000_000, 120_000_000])

        buyer_acceptance = (
            BuyerAcceptance(
                acceptance_id=f"ACC-PROP-{seq_idx:03d}",
                invoice_id=dec.invoice_id,
                version_id=dec.version_id,
                buyer_id=ctx.invoice.buyer_id,
                decision=buyer_decision,  # type: ignore[arg-type]
                accepted_amount_paise=accepted_amount,
                accepted_at="2026-08-12T11:00:00Z",
                signature_hex=("ab" * 64) if valid_sig else "bad-sig",
            )
            if has_acceptance
            else None
        )

        all_invariants_met = (
            dec.outcome == Outcome.CONDITIONALLY_APPROVED
            and reservation is not None
            and reservation.state == ReservationState.FINANCED
            and reservation.lender_id == req_lender
            and 0 < reservation.amount_paise <= outstanding_paise
            and buyer_acceptance is not None
            and buyer_acceptance.decision == "accept"
            and buyer_acceptance.accepted_amount_paise >= reservation.amount_paise
            and valid_sig
        )

        if all_invariants_met:
            disbursed_dec = disburse(
                dec,
                reservation=reservation,
                buyer_acceptance=buyer_acceptance,
                outstanding_paise=outstanding_paise,
                requesting_lender_id=req_lender,
                request=ctx.request,
            )
            assert disbursed_dec.state == RequestState.DISBURSED
        else:
            with pytest.raises((DisbursementInvariantError, PermissionError, ValueError)):
                disburse(
                    dec,
                    reservation=reservation,
                    buyer_acceptance=buyer_acceptance,
                    outstanding_paise=outstanding_paise,
                    requesting_lender_id=req_lender,
                    request=ctx.request,
                )


def test_run_batch_regime_writes_valid_outputs(tmp_path: Path) -> None:
    """Verify run_batch_regime writes decisions.jsonl, decisions_baseline.jsonl, and rings.json."""
    out_dir = tmp_path / "regime_A"
    decisions, rings = run_batch_regime(regime="A", out_dir=out_dir)
    assert len(decisions) >= 2
    assert isinstance(rings, list)

    assert (out_dir / "decisions.jsonl").exists()
    assert (out_dir / "decisions_baseline.jsonl").exists()
    assert (out_dir / "rings.json").exists()

    for line in (out_dir / "decisions.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = Decision.model_validate_json(line)
            assert d.schema_version == 1

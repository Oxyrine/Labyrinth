"""Unit and property tests for `risk/` (features, scorecard, exact paise pricing, and monotonicity)."""

from __future__ import annotations

from pathlib import Path

import pytest

from contract.context import CaseContext, InMemoryHistoryView
from contract.models import (
    CheckResult,
    CheckStatus,
    DocType,
    Grade,
    Invoice,
    PaymentRecord,
    Reservation,
    ReservationState,
    Severity,
    Tier,
    canonical_json,
    format_inr,
)
from risk.check import run as run_risk_check
from risk.features import compute_features, extract_network_tier
from risk.pricing import (
    ADVANCE_RATE_BPS_BY_GRADE,
    ANNUAL_RATE_BPS_BY_GRADE,
    POLICY_NOTICE,
    compute_pricing_quote,
)
from risk.scorecard import evaluate_scorecard
from workflow.pipeline import load_case_context_from_fixture

ROOT = Path(__file__).resolve().parent.parent


def test_exact_pricing_arithmetic_grade_b_10_lakh_60_days() -> None:
    """Verify exact paise round-half-up calculation required by Demo Step 4 and brief."""
    principal_paise = 100_000_000  # ₹10,00,000
    quote = compute_pricing_quote(
        principal_paise=principal_paise,
        grade=Grade.B,
        tenor_days=60,
    )
    assert quote.grade == Grade.B
    assert quote.advance_rate_bps == 8500
    assert quote.advance_amount_paise == 85_000_000  # ₹8,50,000
    assert quote.annual_rate_bps == 1200
    assert quote.tenor_days == 60
    assert quote.discount_paise == 1_676_712  # ₹16,767.12
    assert quote.net_disbursement_paise == 83_323_288  # ₹8,33,232.88
    assert format_inr(quote.advance_amount_paise) == "₹8,50,000"
    assert format_inr(quote.discount_paise) == "₹16,767.12"
    assert format_inr(quote.net_disbursement_paise) == "₹8,33,232.88"
    assert quote.policy_notice == POLICY_NOTICE
    assert "₹8,50,000" in quote.arithmetic_trace
    assert "₹16,767.12" in quote.arithmetic_trace
    # Must serialize with zero floats in canonical_json
    assert canonical_json(quote, allow_floats=False)


def test_pricing_table_all_grades() -> None:
    assert ADVANCE_RATE_BPS_BY_GRADE == {
        Grade.A: 9000,
        Grade.B: 8500,
        Grade.C: 7500,
        Grade.D: 6000,
        Grade.E: 0,
    }
    assert ANNUAL_RATE_BPS_BY_GRADE == {
        Grade.A: 1000,
        Grade.B: 1200,
        Grade.C: 1400,
        Grade.D: 1700,
        Grade.E: 0,
    }

    q_e = compute_pricing_quote(100_000_000, Grade.E, 60)
    assert q_e.advance_amount_paise == 0
    assert q_e.discount_paise == 0
    assert q_e.net_disbursement_paise == 0

    with pytest.raises(ValueError):
        compute_pricing_quote(100_000.0, Grade.B, 60)  # type: ignore[arg-type]


def test_clean_goods_lands_in_grade_b() -> None:
    """Verify clean_goods fixture lands in Grade B with ₹16,767.12 discount."""
    ctx = load_case_context_from_fixture(ROOT / "fixtures" / "clean_goods")
    results = run_risk_check(ctx)
    assert len(results) == 1
    chk = results[0]
    assert chk.check == "risk.grade"
    assert chk.status == CheckStatus.PASS
    assert chk.details["grade"] == "B"
    assert 70 <= chk.details["score_points"] <= 84
    pricing = chk.details["pricing"]
    assert pricing["advance_rate_bps"] == 8500
    assert pricing["advance_amount_paise"] == 85_000_000
    assert pricing["annual_rate_bps"] == 1200
    assert pricing["discount_paise"] == 1_676_712
    assert pricing["net_disbursement_paise"] == 83_323_288
    assert len(chk.details["top_reasons"]) == 3
    for reason in chk.details["top_reasons"]:
        assert not reason.lower().startswith("score:")
        assert any(ch.isdigit() for ch in reason)


def test_grades_a_through_e_monotonic_by_delinquency_rate() -> None:
    """Synthetic buyer profiles ordered by increasing delinquency/default rate map monotonically A -> B -> C -> D -> E."""
    base_ctx = load_case_context_from_fixture(ROOT / "fixtures" / "clean_goods")
    seller_id = base_ctx.invoice.seller_id
    buyer_id = base_ctx.invoice.buyer_id

    # Diversified seller book so buyer concentration can be varied cleanly
    other_invoices = [
        base_ctx.invoice.model_copy(
            update={
                "invoice_id": f"INV-DIV-{i:04d}",
                "buyer_id": f"P-90{i:02d}",
                "grand_total_paise": 100_000_000,
            }
        )
        for i in range(1, 5)
    ]

    profiles = [
        # Profile 0 (Prime Grade A): 100% on-time, 0 DPD, 30d tenor, low concentration, improving trend
        {
            "expected_grade": Grade.A,
            "delinquency_rate": 0.00,
            "tenor_days": 30,
            "payment_terms_days": 30,
            "extra_invoices": other_invoices,
            "payments": [
                PaymentRecord(
                    payment_id=f"PAY-A-{i}",
                    invoice_id=f"INV-A-{i}",
                    seller_id=seller_id,
                    buyer_id=buyer_id,
                    amount_paise=50_000_000,
                    due_date=f"2026-0{min(6, i)}-15",
                    paid_date=f"2026-0{min(6, i)}-10",
                    dpd=0,
                    disputed=False,
                )
                for i in range(1, 7)
            ],
            "network_tier": Tier.LOW,
        },
        # Profile 1 (Good Grade B): 100% on-time, 0 DPD, 60d tenor, standard single-buyer fixture
        {
            "expected_grade": Grade.B,
            "delinquency_rate": 0.05,
            "tenor_days": 60,
            "payment_terms_days": 60,
            "extra_invoices": [],
            "payments": [
                PaymentRecord(
                    payment_id=f"PAY-B-{i}",
                    invoice_id=f"INV-B-{i}",
                    seller_id=seller_id,
                    buyer_id=buyer_id,
                    amount_paise=80_000_000,
                    due_date="2026-07-01",
                    paid_date="2026-06-29",
                    dpd=0,
                    disputed=False,
                )
                for i in range(1, 3)
            ],
            "network_tier": Tier.LOW,
        },
        # Profile 2 (Moderate Grade C): 80% on-time (4/5), avg DPD 3.6d, worst DPD 18d, 60d tenor
        {
            "expected_grade": Grade.C,
            "delinquency_rate": 0.20,
            "tenor_days": 60,
            "payment_terms_days": 60,
            "extra_invoices": other_invoices[:2],
            "payments": [
                PaymentRecord(
                    payment_id=f"PAY-C-{i}",
                    invoice_id=f"INV-C-{i}",
                    seller_id=seller_id,
                    buyer_id=buyer_id,
                    amount_paise=60_000_000,
                    due_date=f"2026-0{i}-15",
                    paid_date=f"2026-0{i}-20" if i == 5 else f"2026-0{i}-14",
                    dpd=18 if i == 5 else 0,
                    disputed=False,
                )
                for i in range(1, 6)
            ],
            "network_tier": Tier.LOW,
        },
        # Profile 3 (Weak Grade D): 60% on-time (3/5), avg DPD 11d, worst DPD 35d, 60d tenor
        {
            "expected_grade": Grade.D,
            "delinquency_rate": 0.40,
            "tenor_days": 60,
            "payment_terms_days": 60,
            "extra_invoices": other_invoices[:2],
            "payments": [
                PaymentRecord(
                    payment_id=f"PAY-D-{i}",
                    invoice_id=f"INV-D-{i}",
                    seller_id=seller_id,
                    buyer_id=buyer_id,
                    amount_paise=60_000_000,
                    due_date=f"2026-0{i}-15",
                    paid_date=f"2026-0{i}-28" if i >= 4 else f"2026-0{i}-14",
                    dpd=35 if i == 5 else (20 if i == 4 else 0),
                    disputed=False,
                )
                for i in range(1, 6)
            ],
            "network_tier": Tier.LOW,
        },
        # Profile 4 (Distressed Grade E): 20% on-time, avg DPD > 60d, worst DPD 120d, high disputes & >90d ageing
        {
            "expected_grade": Grade.E,
            "delinquency_rate": 0.80,
            "tenor_days": 90,
            "payment_terms_days": 90,
            "extra_invoices": [],
            "payments": [
                PaymentRecord(
                    payment_id=f"PAY-E-{i}",
                    invoice_id=f"INV-E-{i}",
                    seller_id=seller_id,
                    buyer_id=buyer_id,
                    amount_paise=60_000_000,
                    due_date=f"2026-0{i}-15",
                    paid_date=f"2026-0{i}-28" if i == 1 else None,
                    dpd=0 if i == 1 else (95 + i * 5),
                    disputed=(i >= 3),
                )
                for i in range(1, 6)
            ],
            "network_tier": Tier.HIGH,
        },
    ]

    scores: list[int] = []
    grades: list[Grade] = []
    advance_rates: list[int] = []

    for prof in profiles:
        inv = base_ctx.invoice.model_copy(
            update={"payment_terms_days": prof["payment_terms_days"]}
        )
        req = base_ctx.request.model_copy(update={"tenor_days": prof["tenor_days"]})
        hist = InMemoryHistoryView(
            invoices=[inv, *prof["extra_invoices"]],
            versions=[base_ctx.version],
            payments=prof["payments"],
            evidence_docs=list(base_ctx.evidence),
        )
        net_chk = CheckResult(
            check="network.ring",
            status=CheckStatus.WARN if prof["network_tier"] != Tier.LOW else CheckStatus.PASS,
            severity=Severity.HIGH if prof["network_tier"] == Tier.HIGH else Severity.INFO,
            evidence=[f"Network ring check evaluated with tier {prof['network_tier'].value}."],
            details={"tier": prof["network_tier"].value},
        )
        ctx = CaseContext(
            request=req,
            invoice=inv,
            version=base_ctx.version,
            parties=base_ctx.parties,
            evidence=base_ctx.evidence,
            history=hist,
            stage_outputs={"network": [net_chk]},
        )
        feats = compute_features(ctx)
        pts, grade, reasons = evaluate_scorecard(feats)
        quote = compute_pricing_quote(inv.grand_total_paise, grade, req.tenor_days)

        assert grade == prof["expected_grade"], f"Expected {prof['expected_grade']}, got {grade} ({pts} pts)"
        assert len(reasons) == 3
        scores.append(pts)
        grades.append(grade)
        advance_rates.append(quote.advance_rate_bps)

    assert grades == [Grade.A, Grade.B, Grade.C, Grade.D, Grade.E]
    # Scores and advance rates must strictly decrease as delinquency/default rate increases
    for i in range(len(scores) - 1):
        assert scores[i] > scores[i + 1], f"Non-monotonic scores: {scores}"
        assert advance_rates[i] > advance_rates[i + 1], f"Non-monotonic advance rates: {advance_rates}"


def test_receivables_dilution_and_financed_share_features() -> None:
    """Verify dilution_rate, financed_share, is_new_pair, and network_tier extraction."""
    base_ctx = load_case_context_from_fixture(ROOT / "fixtures" / "clean_goods")
    credit_note = base_ctx.invoice.model_copy(
        update={
            "invoice_id": "INV-CN-0001",
            "doc_type": DocType.CREDIT_NOTE,
            "linked_invoice_id": base_ctx.invoice.invoice_id,
            "grand_total_paise": 10_000_000,  # 10% dilution on ₹10,00,000
        }
    )
    rsv = Reservation(
        reservation_id="RSV-TEST-01",
        invoice_identity=base_ctx.version.invoice_identity,
        request_id=base_ctx.request.request_id,
        lender_id="L1",
        seller_id=base_ctx.invoice.seller_id,
        amount_paise=60_000_000,  # 60% financed share
        state=ReservationState.FINANCED,
        created_at="2026-08-12T10:00:00Z",
        expires_at="2026-08-13T10:00:00Z",
    )
    hist = InMemoryHistoryView(
        invoices=[base_ctx.invoice, credit_note],
        versions=[base_ctx.version],
        payments=[],
        reservations=[rsv],
    )
    net_chk = CheckResult(
        check="network.ring",
        status=CheckStatus.WARN,
        severity=Severity.MEDIUM,
        evidence=["Moderate cycle score across 3 firms."],
        details={"tier": "medium"},
    )
    ctx = base_ctx.model_copy(
        update={
            "history": hist,
            "stage_outputs": {"network": [net_chk]},
        }
    )
    feats = compute_features(ctx)
    assert feats.is_new_pair is True
    assert abs(feats.dilution_rate - 0.10) < 1e-6
    assert abs(feats.financed_share - 0.60) < 1e-6
    assert feats.network_tier == Tier.MEDIUM
    assert extract_network_tier(ctx) == Tier.MEDIUM

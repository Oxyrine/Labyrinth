"""M0 contract, schema, canonical JSON, GSTIN checksum, and seed fixture tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from contract import (
    CaseContext,
    CheckResult,
    CheckStatus,
    EvidenceDoc,
    FinancingRequest,
    InMemoryHistoryView,
    Invoice,
    InvoiceVersion,
    LineItem,
    Party,
    Severity,
    VerificationStatus,
    canonical_json,
    derive_evidence_weight,
    format_inr,
    make_gstin,
    verify_gstin_checksum,
)
from contract.validate import export_schemas, validate_all

ROOT = Path(__file__).resolve().parent.parent


def test_gstin_mod36_roundtrip() -> None:
    gstin = make_gstin("27", "AABCS1429B", "1")
    assert len(gstin) == 15
    assert verify_gstin_checksum(gstin)
    # Flip check char
    bad = gstin[:-1] + ("A" if gstin[-1] != "A" else "B")
    assert not verify_gstin_checksum(bad)


def test_money_rejects_floats() -> None:
    with pytest.raises(ValidationError):
        LineItem(
            line_no=1,
            description="Steel Coil",
            hsn_sac="7208",
            qty=10,
            rate_paise=100.5,  # type: ignore[arg-type]
            taxable_paise=1005,
            total_paise=1005,
        )


def test_canonical_json_rejects_floats() -> None:
    with pytest.raises(ValueError, match="Float disallowed"):
        canonical_json({"amount_paise": 100.0})
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'


def test_evidence_weight_derived_strictly() -> None:
    assert derive_evidence_weight(VerificationStatus.UNVERIFIED) == 0.4
    assert derive_evidence_weight(VerificationStatus.SIGNATURE_CHECKED) == 0.7
    assert derive_evidence_weight(VerificationStatus.INDEPENDENTLY_CONFIRMED) == 1.0


def test_check_result_rejects_raw_score_evidence() -> None:
    with pytest.raises(ValidationError, match="never raw 'score: X'"):
        CheckResult(
            check="network.ring",
            status=CheckStatus.WARN,
            severity=Severity.HIGH,
            evidence=["score: 0.91"],
        )


def test_format_inr() -> None:
    assert format_inr(100_000_000) == "₹10,00,000"
    assert format_inr(85_000_000) == "₹8,50,000"
    assert format_inr(1_676_712) == "₹16,767.12"


def test_seed_fixtures_and_schemas_validate() -> None:
    schemas = export_schemas()
    assert len(schemas) >= 10
    checked, errors = validate_all()
    assert not errors, f"Validation errors: {errors}"
    assert checked >= 3

    examples = json.loads((ROOT / "fixtures" / "check_examples.json").read_text(encoding="utf-8"))
    statuses = {CheckResult.model_validate(item).status for item in examples}
    assert statuses == {
        CheckStatus.PASS,
        CheckStatus.WARN,
        CheckStatus.FAIL,
        CheckStatus.UNAVAILABLE,
        CheckStatus.NOT_APPLICABLE,
    }


def test_case_context_hydrates_clean_goods() -> None:
    raw = json.loads((ROOT / "fixtures" / "clean_goods" / "case.json").read_text(encoding="utf-8"))
    ctx = CaseContext(
        request=FinancingRequest.model_validate(raw["request"]),
        invoice=Invoice.model_validate(raw["invoice"]),
        version=InvoiceVersion.model_validate(raw["version"]),
        parties={k: Party.model_validate(v) for k, v in raw["parties"].items()},
        evidence=[EvidenceDoc.model_validate(d) for d in raw["evidence"]],
        history=InMemoryHistoryView.model_validate(raw["history"]),
    )
    assert ctx.invoice.grand_total_paise == 100_000_000
    assert len(ctx.history.all_invoices()) == 1

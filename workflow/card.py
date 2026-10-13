"""Deterministic explanation card builder with optional isolated LLM summary (`workflow/card.py`)."""

from __future__ import annotations

import os
from typing import Any

from contract.models import CheckResult, Decision, Invoice, Party, format_inr

THREE_PROPERTY_BANNER: dict[str, str] = {
    "document_consistency": (
        "Document Consistency (extract/, match/): Verifies whether Invoice, PO, GRN, and Contract "
        "agree on parties, HSN/SAC, quantities, unit rates, tax regime, and dates."
    ),
    "document_integrity": (
        "Document Integrity (audit/, chain/): Verifies whether encrypted off-chain blobs, extracted "
        "field versions, and workflow events remain unaltered and match salted Merkle commitments."
    ),
    "trade_authenticity": (
        "Trade Authenticity (network/, match/ewaybill): No software proves physical goods moved if "
        "parties collude; graph topology, corporate links, and logistics signals raise fabrication "
        "cost and route anomalies to human review with concrete numerical evidence."
    ),
}


def _format_check_entry(chk: CheckResult) -> dict[str, Any]:
    return {
        "check": chk.check,
        "status": chk.status.value,
        "severity": chk.severity.value,
        "source": chk.source.value,
        "source_badge": "LIVE" if chk.source.value == "live" else "FIXTURE",
        "verification_status": (
            chk.verification_status.value if chk.verification_status is not None else "unverified"
        ),
        "evidence_weight": chk.evidence_weight,
        "evidence": list(chk.evidence),
        "refs": list(chk.refs),
        "details": dict(chk.details),
    }


def _maybe_generate_external_llm_summary(card_payload: dict[str, Any]) -> str | None:
    """Optionally call Claude Sonnet 5.5 when LABYRINTH_ALLOW_EXTERNAL_LLM=1; off by default."""
    if os.environ.get("LABYRINTH_ALLOW_EXTERNAL_LLM") != "1":
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic  # type: ignore[import-not-found]

        client = anthropic.Anthropic(api_key=api_key, timeout=5.0)
        prompt = (
            "Summarize the following trade finance decision card in 2 concise sentences using the "
            "exact rupee figures and check statuses provided. Never use the word 'CLEARED'.\n\n"
            f"Outcome: {card_payload['outcome']}\n"
            f"Grade: {card_payload['grade']} ({card_payload['score_points']}/100)\n"
            f"Top reasons: {card_payload['top_reasons']}\n"
        )
        resp = client.messages.create(
            model="claude-sonnet-5-5",
            max_tokens=180,
            messages=[{"role": "user", "content": prompt}],
        )
        text = getattr(resp.content[0], "text", None) if resp.content else None
        if isinstance(text, str) and text.strip():
            return text.strip().replace("CLEARED", "REVIEWED")
    except Exception:
        return None
    return None


def build_explanation_card(
    decision: Decision,
    invoice: Invoice,
    parties: dict[str, Party],
) -> dict[str, Any]:
    """Build a deterministic explanation card for underwriters and auditors (Lesson 12 compliant)."""
    seller = parties.get(invoice.seller_id)
    buyer = parties.get(invoice.buyer_id)

    formatted_checks = [_format_check_entry(c) for c in decision.checks]
    live_count = sum(1 for c in decision.checks if c.source.value == "live")
    fixture_count = sum(1 for c in decision.checks if c.source.value == "fixture")

    status_counts: dict[str, int] = {
        "pass": 0,
        "warn": 0,
        "fail": 0,
        "unavailable": 0,
        "not_applicable": 0,
    }
    for chk in decision.checks:
        status_counts[chk.status.value] = status_counts.get(chk.status.value, 0) + 1

    pricing_dict = decision.pricing.model_dump(mode="json") if decision.pricing is not None else None
    arithmetic_trace = decision.pricing.arithmetic_trace if decision.pricing is not None else ""
    policy_notice = (
        decision.pricing.policy_notice
        if decision.pricing is not None
        else "Illustrative prototype policy, not validated lending prices."
    )

    card: dict[str, Any] = {
        "schema_version": decision.schema_version,
        "decision_id": decision.decision_id,
        "request_id": decision.request_id,
        "invoice_id": decision.invoice_id,
        "version_id": decision.version_id,
        "outcome": decision.outcome.value,
        "state": decision.state.value,
        "decided_at": decision.decided_at,
        "three_property_banner": dict(THREE_PROPERTY_BANNER),
        "invoice_summary": {
            "invoice_id": invoice.invoice_id,
            "invoice_no": invoice.invoice_no,
            "financial_year": invoice.financial_year,
            "invoice_date": invoice.invoice_date,
            "due_date": invoice.due_date,
            "payment_terms_days": invoice.payment_terms_days,
            "subtotal_paise": invoice.subtotal_paise,
            "subtotal_inr": format_inr(invoice.subtotal_paise),
            "total_tax_paise": invoice.total_tax_paise,
            "total_tax_inr": format_inr(invoice.total_tax_paise),
            "grand_total_paise": invoice.grand_total_paise,
            "grand_total_inr": format_inr(invoice.grand_total_paise),
        },
        "parties": {
            "seller": {
                "party_id": invoice.seller_id,
                "name": seller.name if seller else invoice.seller_id,
                "gstin": invoice.seller_gstin,
                "city": seller.city if seller else "",
                "state_code": seller.state_code if seller else invoice.place_of_supply_state,
            },
            "buyer": {
                "party_id": invoice.buyer_id,
                "name": buyer.name if buyer else invoice.buyer_id,
                "gstin": invoice.buyer_gstin,
                "city": buyer.city if buyer else "",
                "state_code": buyer.state_code if buyer else invoice.place_of_supply_state,
            },
        },
        "grade": decision.grade.value,
        "score_points": decision.score_points,
        "network_tier": decision.network_tier.value,
        "pricing": pricing_dict,
        "discount_arithmetic_trace": arithmetic_trace,
        "policy_notice": policy_notice,
        "top_reasons": list(decision.top_reasons),
        "conditions": list(decision.conditions),
        "source_badges": {
            "live_count": live_count,
            "fixture_count": fixture_count,
            "badges": sorted({c["source_badge"] for c in formatted_checks}),
        },
        "status_counts": status_counts,
        "checks": formatted_checks,
        "stage_latencies_ms": dict(decision.stage_latencies_ms),
        "overridden_by": [o.model_dump(mode="json") for o in decision.overridden_by],
        "generated_summary": None,
    }

    card["generated_summary"] = _maybe_generate_external_llm_summary(card)
    return card

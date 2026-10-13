"""Rapid cycling vs stated payment terms signal (s_rapid)."""

from __future__ import annotations

from datetime import date
from typing import Any

from network.cycles import RingCandidate
from network.graph import TradeGraph


def rapid_details(ring: RingCandidate, graph: TradeGraph) -> dict[str, Any]:
    """Compare total elapsed days across ring invoices against minimum stated payment_terms_days."""
    dates: list[date] = []
    terms: list[int] = []

    for u, v in ring.invoice_hops:
        for inv in graph.invoices_on_edge(u, v):
            try:
                dates.append(date.fromisoformat(inv.invoice_date))
            except ValueError:
                pass
            if inv.payment_terms_days > 0:
                terms.append(inv.payment_terms_days)

    if len(dates) < 2 or not terms:
        return {
            "earliest_date": None,
            "latest_date": None,
            "elapsed_days": None,
            "min_payment_terms_days": None,
            "score": None,
        }

    earliest = min(dates)
    latest = max(dates)
    elapsed_days = (latest - earliest).days
    min_terms = min(terms)

    if elapsed_days >= min_terms:
        return {
            "earliest_date": earliest.isoformat(),
            "latest_date": latest.isoformat(),
            "elapsed_days": elapsed_days,
            "min_payment_terms_days": min_terms,
            "score": None,
        }

    ratio = elapsed_days / float(min_terms)
    score = max(0.0, min(1.0, 1.0 - 0.65 * ratio))
    return {
        "earliest_date": earliest.isoformat(),
        "latest_date": latest.isoformat(),
        "elapsed_days": elapsed_days,
        "min_payment_terms_days": min_terms,
        "score": score,
    }


def s_rapid(ring: RingCandidate, graph: TradeGraph) -> float | None:
    """Compute rapid-cycling signal when ring elapsed_days < min(payment_terms_days), else None."""
    return rapid_details(ring, graph)["score"]

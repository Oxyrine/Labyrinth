"""Chronological timing speed and regularity signal (s_timing)."""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from network.cycles import RingCandidate
from network.graph import TradeGraph


def timing_details(ring: RingCandidate, graph: TradeGraph) -> dict[str, Any]:
    """Collect all ring invoice hop dates, sort chronologically, and compute speed + regularity."""
    dates: list[date] = []
    for u, v in ring.invoice_hops:
        for inv in graph.invoices_on_edge(u, v):
            try:
                dates.append(date.fromisoformat(inv.invoice_date))
            except ValueError:
                continue

    if len(dates) < 3:
        return {
            "sorted_dates": [d.isoformat() for d in sorted(dates)],
            "gaps": [],
            "mean_gap": None,
            "cv": None,
            "speed": None,
            "regularity": None,
            "elapsed_days": (max(dates) - min(dates)).days if len(dates) >= 2 else 0,
            "score": None,
        }

    sorted_dates = sorted(dates)
    gaps = [(sorted_dates[i + 1] - sorted_dates[i]).days for i in range(len(sorted_dates) - 1)]
    mean_gap = sum(gaps) / len(gaps)
    speed = math.exp(-mean_gap / 30.0)

    if mean_gap <= 1e-9:
        cv = 0.0
    else:
        variance = sum((g - mean_gap) ** 2 for g in gaps) / len(gaps)
        cv = math.sqrt(variance) / mean_gap

    n_hops = len(sorted_dates)
    regularity = (1.0 - min(1.0, cv)) * min(1.0, n_hops / 6.0)
    raw_score = 0.6 * speed + 0.4 * regularity
    score = max(0.0, min(1.0, raw_score))

    return {
        "sorted_dates": [d.isoformat() for d in sorted_dates],
        "gaps": gaps,
        "mean_gap": mean_gap,
        "cv": cv,
        "speed": speed,
        "regularity": regularity,
        "elapsed_days": (sorted_dates[-1] - sorted_dates[0]).days,
        "score": score,
    }


def s_timing(ring: RingCandidate, graph: TradeGraph) -> float | None:
    """Compute rotation-invariant chronological timing signal in [0.0, 1.0], or None if < 3 dated hops."""
    return timing_details(ring, graph)["score"]

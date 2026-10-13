"""Ring trade isolation signal (s_isolation): ring_internal_paise / total_paise_touching_ring_firms."""

from __future__ import annotations

from typing import Any

from network.cycles import RingCandidate
from network.graph import TradeGraph


def isolation_details(ring: RingCandidate, graph: TradeGraph) -> dict[str, Any]:
    """Compute ring internal volume vs total invoice volume touching any member firm."""
    ring_invoices: dict[str, int] = {}
    for u, v in ring.invoice_hops:
        for inv in graph.invoices_on_edge(u, v):
            ring_invoices[inv.invoice_id] = inv.grand_total_paise

    ring_internal_paise = sum(ring_invoices.values())
    touching_invoices = graph.invoices_touching_parties(ring.members)
    total_paise_touching = sum(inv.grand_total_paise for inv in touching_invoices)

    if total_paise_touching <= 0 or ring_internal_paise <= 0:
        return {
            "ring_internal_paise": ring_internal_paise,
            "total_paise_touching_ring_firms": total_paise_touching,
            "outside_paise": max(0, total_paise_touching - ring_internal_paise),
            "ring_invoice_count": len(ring_invoices),
            "touching_invoice_count": len(touching_invoices),
            "score": None,
        }

    ratio = ring_internal_paise / float(total_paise_touching)
    score = max(0.0, min(1.0, ratio))
    return {
        "ring_internal_paise": ring_internal_paise,
        "total_paise_touching_ring_firms": total_paise_touching,
        "outside_paise": max(0, total_paise_touching - ring_internal_paise),
        "ring_invoice_count": len(ring_invoices),
        "touching_invoice_count": len(touching_invoices),
        "score": score,
    }


def s_isolation(ring: RingCandidate, graph: TradeGraph) -> float | None:
    """Compute s_isolation = ring_internal_paise / total_paise_touching_ring_firms."""
    return isolation_details(ring, graph)["score"]

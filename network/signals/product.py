"""HSN/SAC product similarity signal (s_product), rotation-invariant across ring hops."""

from __future__ import annotations

from typing import Any

from network.cycles import RingCandidate
from network.graph import TradeGraph


def hsn_similarity(hsn_a: str, hsn_b: str) -> float:
    """Compute hierarchical HSN/SAC similarity between two codes:
    1.0 for exact match, 0.7 for 4-digit prefix match, 0.3 for 2-digit chapter match, 0.0 otherwise.
    """
    a = (hsn_a or "").strip().upper()
    b = (hsn_b or "").strip().upper()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) >= 4 and len(b) >= 4 and a[:4] == b[:4]:
        return 0.7
    if len(a) >= 2 and len(b) >= 2 and a[:2] == b[:2]:
        return 0.3
    return 0.0


def hop_hsns(u: str, v: str, graph: TradeGraph) -> tuple[str, ...]:
    """Return sorted unique HSN/SAC codes across all invoices on directed edge (u, v)."""
    codes: set[str] = set()
    for inv in graph.invoices_on_edge(u, v):
        for item in inv.line_items:
            code = (item.hsn_sac or "").strip().upper()
            if code:
                codes.add(code)
    return tuple(sorted(codes))


def product_details(ring: RingCandidate, graph: TradeGraph) -> dict[str, Any]:
    """Compute consecutive hop HSN similarities (including closing hop if transaction-closed)."""
    hops = list(ring.invoice_hops)
    if len(hops) < 2:
        return {
            "hop_hsns": [],
            "pair_scores": [],
            "score": None,
        }

    hsns_per_hop = [hop_hsns(u, v, graph) for (u, v) in hops]
    consec_pairs: list[tuple[int, int]] = []
    for i in range(len(hops) - 1):
        if graph.resolve(hops[i][1]) == graph.resolve(hops[i + 1][0]):
            consec_pairs.append((i, i + 1))

    # Include the closing invoice hop pair when transaction-closed (Lesson 3 rotation invariance)
    if not ring.bridges and graph.resolve(hops[-1][1]) == graph.resolve(hops[0][0]):
        consec_pairs.append((len(hops) - 1, 0))

    if not consec_pairs:
        return {
            "hop_hsns": hsns_per_hop,
            "pair_scores": [],
            "score": None,
        }

    pair_scores: list[float] = []
    for idx_a, idx_b in consec_pairs:
        codes_a = hsns_per_hop[idx_a]
        codes_b = hsns_per_hop[idx_b]
        if not codes_a or not codes_b:
            pair_scores.append(0.0)
        else:
            best = max(hsn_similarity(ca, cb) for ca in codes_a for cb in codes_b)
            pair_scores.append(best)

    sorted_scores = sorted(pair_scores)
    mean_score = sum(sorted_scores) / len(sorted_scores)
    return {
        "hop_hsns": hsns_per_hop,
        "pair_scores": pair_scores,
        "score": max(0.0, min(1.0, mean_score)),
    }


def s_product(ring: RingCandidate, graph: TradeGraph) -> float | None:
    """Compute rotation-invariant HSN similarity across consecutive invoice hops."""
    return product_details(ring, graph)["score"]

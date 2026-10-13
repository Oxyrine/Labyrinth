"""Flow retention signal (s_flow) evaluated strictly over interior ring firms."""

from __future__ import annotations

import math
from typing import Any

from network.cycles import RingCandidate
from network.graph import TradeGraph


def flow_details(ring: RingCandidate, graph: TradeGraph) -> dict[str, Any]:
    """Compute per-firm inflow/outflow and interior firm retention metrics.

    Only firms with BOTH an incoming invoice hop AND an outgoing invoice hop in the ring
    (in_paise > 0 and out_paise > 0) are treated as interior firms. Entry and exit firms
    of corporate-bridge-closed rings have in_paise == 0 or out_paise == 0 and are never
    divided by (test_flow_bridge_ring_no_div_zero).
    """
    in_paise: dict[str, int] = {m: 0 for m in ring.members}
    out_paise: dict[str, int] = {m: 0 for m in ring.members}
    has_in_hop: set[str] = set()
    has_out_hop: set[str] = set()

    for u, v in ring.invoice_hops:
        cu = graph.resolve(u)
        cv = graph.resolve(v)
        edge_amt = graph.edge_total_paise(cu, cv)
        out_paise[cu] = out_paise.get(cu, 0) + edge_amt
        in_paise[cv] = in_paise.get(cv, 0) + edge_amt
        if edge_amt > 0:
            has_out_hop.add(cu)
            has_in_hop.add(cv)

    interior_firms: list[str] = []
    retained_ratio_by_firm: dict[str, float] = {}
    retained_pct_by_firm: dict[str, float] = {}

    for firm in ring.members:
        cfirm = graph.resolve(firm)
        if cfirm in has_in_hop and cfirm in has_out_hop:
            firm_in = in_paise.get(cfirm, 0)
            firm_out = out_paise.get(cfirm, 0)
            if firm_in > 0 and firm_out > 0:
                interior_firms.append(cfirm)
                ret = abs(firm_out - firm_in) / float(firm_in)
                retained_ratio_by_firm[cfirm] = ret
                retained_pct_by_firm[cfirm] = round(ret * 100.0, 2)

    if not interior_firms:
        return {
            "in_paise_by_firm": in_paise,
            "out_paise_by_firm": out_paise,
            "interior_firms": [],
            "retained_ratio_by_firm": {},
            "retained_pct_by_firm": {},
            "mean_retained": None,
            "score": None,
        }

    # Sort values before summing for bit-identical rotation & relabel invariance
    sorted_ratios = sorted(retained_ratio_by_firm.values())
    mean_retained = sum(sorted_ratios) / len(sorted_ratios)
    raw_score = math.exp(-mean_retained / 0.15)
    score = max(0.0, min(1.0, raw_score))

    return {
        "in_paise_by_firm": in_paise,
        "out_paise_by_firm": out_paise,
        "interior_firms": interior_firms,
        "retained_ratio_by_firm": retained_ratio_by_firm,
        "retained_pct_by_firm": retained_pct_by_firm,
        "mean_retained": mean_retained,
        "score": score,
    }


def s_flow(ring: RingCandidate, graph: TradeGraph) -> float | None:
    """Compute flow pass-through signal in [0.0, 1.0], or None if no interior firm exists."""
    return flow_details(ring, graph)["score"]

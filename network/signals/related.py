"""Related-party corporate link signal (s_related) weighted by world rarity 1/log2(1+n)."""

from __future__ import annotations

import math
from typing import Any

from network.cycles import RingCandidate
from network.graph import TradeGraph


def related_details(ring: RingCandidate, graph: TradeGraph) -> dict[str, Any]:
    """Inspect all pairs of distinct member firms in the ring for shared corporate attributes.

    Attributes checked:
      - shared_director
      - same_pan (including gstin[2:12])
      - same_address (normalized)
      - same_phone (normalized)
      - same_bank_hash

    Each shared attribute is weighted by rarity: 1.0 / math.log2(1 + n_firms_sharing_in_world).
    Abstains (None) if no related links exist across ring members.
    """
    members = sorted({graph.resolve(m) for m in ring.members})
    pair_links: list[dict[str, Any]] = []
    # Track best rarity weight per distinct (category, value) across the ring
    attr_records: dict[tuple[str, str], dict[str, Any]] = {}

    n = len(members)
    for i in range(n):
        u = members[i]
        for j in range(i + 1, n):
            v = members[j]
            links = graph.related_links(u, v)
            if not links:
                continue
            pair_links.append(
                {
                    "from": u,
                    "to": v,
                    "links": {k: list(vals) for k, vals in links.items()},
                    "link_count": len(links),
                    "is_corporate_bridge": len(links) >= 2,
                }
            )
            for category, values in links.items():
                for val in values:
                    n_world = max(2, graph.world_sharing_count(category, val))
                    rarity_w = 1.0 / math.log2(1.0 + n_world)
                    key = (category, val)
                    rec = attr_records.get(key)
                    if rec is None:
                        attr_records[key] = {
                            "category": category,
                            "value": val,
                            "firms_in_ring": {u, v},
                            "n_firms_sharing_in_world": n_world,
                            "rarity_weight": rarity_w,
                        }
                    else:
                        rec["firms_in_ring"].update((u, v))

    if not attr_records:
        return {
            "pair_links": [],
            "shared_attributes": [],
            "score": None,
        }

    # For each category, take the rarest shared value per pair/category so independent link types stack
    weights = sorted(rec["rarity_weight"] for rec in attr_records.values())
    total_weight = sum(weights)
    score = max(0.0, min(1.0, total_weight))

    shared_list = [
        {
            "category": rec["category"],
            "value": rec["value"],
            "firms_in_ring": sorted(rec["firms_in_ring"]),
            "n_firms_sharing_in_world": rec["n_firms_sharing_in_world"],
            "rarity_weight": round(rec["rarity_weight"], 4),
        }
        for _k, rec in sorted(attr_records.items())
    ]

    return {
        "pair_links": pair_links,
        "shared_attributes": shared_list,
        "score": score,
    }


def s_related(ring: RingCandidate, graph: TradeGraph) -> float | None:
    """Compute rarity-weighted related-party link signal in [0.0, 1.0], or None if no links exist."""
    return related_details(ring, graph)["score"]

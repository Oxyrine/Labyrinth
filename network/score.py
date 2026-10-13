"""Weighted geometric mean ring scoring, tier classification, and review_priority ranking."""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from contract.models import Tier
from network.cycles import RingCandidate
from network.evidence import build_ring_evidence
from network.graph import TradeGraph
from network.guard import evaluate_legitimacy_guard
from network.signals import SIGNAL_NAMES, compute_all_signals
from network.signals.flow import flow_details
from network.signals.isolation import isolation_details

_WEIGHTS_PATH = Path(__file__).with_name("weights.toml")

TIER_RANK: dict[Tier, int] = {
    Tier.LOW: 1,
    Tier.MEDIUM: 2,
    Tier.HIGH: 3,
}


def load_weights_config(path: Path | None = None) -> dict[str, Any]:
    target = path or _WEIGHTS_PATH
    with target.open("rb") as f:
        return tomllib.load(f)


_CONFIG = load_weights_config()
DEFAULT_WEIGHTS: dict[str, float] = {k: float(v) for k, v in _CONFIG["weights"].items()}
HIGH_TIER_MIN: float = float(_CONFIG["thresholds"]["high_tier_min"])
MEDIUM_TIER_MIN: float = float(_CONFIG["thresholds"]["medium_tier_min"])
SINGLE_SIGNAL_CAP: float = float(_CONFIG["thresholds"]["single_signal_cap"])
BOOST_TRIGGER_MIN: float = float(_CONFIG["thresholds"]["boost_trigger_min"])
SIGNAL_FLOOR: float = float(_CONFIG["thresholds"]["signal_floor"])


def classify_tier(score: float) -> Tier:
    """Map ring score in [0.0, 1.0] to Tier.HIGH (>= 0.60), Tier.MEDIUM (0.30..0.60), Tier.LOW (< 0.30)."""
    if score >= HIGH_TIER_MIN:
        return Tier.HIGH
    if score >= MEDIUM_TIER_MIN:
        return Tier.MEDIUM
    return Tier.LOW


def review_priority(tier: Tier, ring_exposure_paise: int) -> tuple[int, int]:
    """Return deterministic review priority sort key (tier_rank, ring_exposure_paise)."""
    return (TIER_RANK[tier], int(ring_exposure_paise))


def weighted_geometric_mean(
    signals: Mapping[str, float | None],
    weights: Mapping[str, float] | None = None,
    *,
    floor: float = SIGNAL_FLOOR,
) -> tuple[float, int]:
    """Compute weighted geometric mean exp(sum(w_i * ln(max(s_i, 1e-4))) / sum(w_i)) over non-abstained signals."""
    w_map = weights if weights is not None else DEFAULT_WEIGHTS
    ordered_keys = list(SIGNAL_NAMES) + sorted(k for k in signals.keys() if k not in SIGNAL_NAMES)

    weighted_log_sum = 0.0
    weight_sum = 0.0
    active_count = 0

    for key in ordered_keys:
        if key not in signals:
            continue
        val = signals[key]
        if val is None:
            continue
        w = float(w_map.get(key, 0.1))
        if w <= 0.0:
            continue
        clamped_val = max(float(val), floor)
        weighted_log_sum += w * math.log(clamped_val)
        weight_sum += w
        active_count += 1

    if active_count == 0 or weight_sum <= 0.0:
        return 0.0, 0

    geom = math.exp(weighted_log_sum / weight_sum)
    return max(0.0, min(1.0, geom)), active_count


def score_signals(
    signals: Mapping[str, float | None],
    *,
    guard_multiplier: float = 1.0,
    weights: Mapping[str, float] | None = None,
) -> tuple[float, Tier]:
    """Combine non-abstained signals via weighted geometric mean (with high-confidence movement/related boost),
    apply legitimacy guard multiplier, and enforce the <2-signal cap at 0.59 (Tier.MEDIUM).
    """
    geom_mean, active_count = weighted_geometric_mean(signals, weights=weights)
    if active_count == 0:
        return 0.0, Tier.LOW

    boost_candidates: list[float] = []
    for key in ("s_movement", "s_related"):
        val = signals.get(key)
        if val is not None and float(val) >= BOOST_TRIGGER_MIN:
            boost_candidates.append(float(val))

    if boost_candidates:
        boost = max(boost_candidates)
        raw_score = max(geom_mean, 0.5 * geom_mean + 0.5 * boost)
    else:
        raw_score = geom_mean

    guarded = raw_score * float(guard_multiplier)
    if active_count < 2:
        guarded = min(guarded, SINGLE_SIGNAL_CAP)

    final_score = max(0.0, min(1.0, guarded))
    return final_score, classify_tier(final_score)


@dataclass(frozen=True)
class ScoredRing:
    """Fully scored and explained network ring."""

    ring_id: str
    canonical_key: tuple[str, ...]
    members: tuple[str, ...]
    invoice_hops: tuple[tuple[str, str], ...]
    bridges: tuple[tuple[str, str], ...]
    signals: dict[str, float | None]
    retained_pct_by_firm: dict[str, float]
    guard_multiplier: float
    guard_reasons: list[str]
    score: float
    tier: Tier
    ring_exposure_paise: int
    review_priority: tuple[int, int]
    evidence: list[str] = field(default_factory=list)

    def to_ui_dict(self, graph: TradeGraph) -> dict[str, Any]:
        """Serialize ring details for the UI ring visualizer without raw candidate counts."""
        hops_list: list[dict[str, Any]] = []
        for u, v in self.invoice_hops:
            invs = graph.invoices_on_edge(u, v)
            hsns = sorted({item.hsn_sac.strip() for inv in invs for item in inv.line_items if item.hsn_sac})
            hops_list.append(
                {
                    "from": graph.resolve(u),
                    "to": graph.resolve(v),
                    "invoice_ids": [inv.invoice_id for inv in invs],
                    "total_paise": sum(inv.grand_total_paise for inv in invs),
                    "dates": [inv.invoice_date for inv in invs],
                    "hsns": hsns,
                }
            )

        bridges_list: list[dict[str, Any]] = []
        for x, y in self.bridges:
            links = graph.related_links(x, y)
            bridges_list.append(
                {
                    "from": graph.resolve(x),
                    "to": graph.resolve(y),
                    "links": {k: list(vals) for k, vals in sorted(links.items())},
                    "link_count": len(links),
                }
            )

        return {
            "ring_id": self.ring_id,
            "canonical_key": list(self.canonical_key),
            "members": list(self.members),
            "hops": hops_list,
            "bridges": bridges_list,
            "signals": {
                k: (round(float(v), 4) if v is not None else None) for k, v in self.signals.items()
            },
            "retained_pct_by_firm": {
                k: round(float(v), 2) for k, v in sorted(self.retained_pct_by_firm.items())
            },
            "guard_multiplier": round(float(self.guard_multiplier), 4),
            "guard_reasons": list(self.guard_reasons),
            "ring_exposure_paise": int(self.ring_exposure_paise),
            "review_priority": [self.review_priority[0], self.review_priority[1]],
            "tier": self.tier.value,
            "score": round(float(self.score), 4),
            "evidence": list(self.evidence),
        }


def score_ring(
    ring: RingCandidate,
    graph: TradeGraph,
    *,
    weights: Mapping[str, float] | None = None,
) -> ScoredRing:
    """Compute all signals, legitimacy guard, weighted score, tier, review_priority, and evidence for a ring."""
    signals = compute_all_signals(ring, graph)
    f_det = flow_details(ring, graph)
    iso_det = isolation_details(ring, graph)
    guard_mult, guard_reasons = evaluate_legitimacy_guard(ring, graph, signals=signals)

    # When e-way bills exist without any anomaly (s_movement == 0.0), do not let the 1e-4 floor
    # artificially crush the geometric mean of otherwise active topology signals.
    scoring_signals = dict(signals)
    if scoring_signals.get("s_movement") == 0.0:
        scoring_signals["s_movement"] = None

    final_score, tier = score_signals(
        scoring_signals,
        guard_multiplier=guard_mult,
        weights=weights,
    )
    exposure_paise = int(iso_det["ring_internal_paise"])
    prio = review_priority(tier, exposure_paise)

    evidence = build_ring_evidence(
        ring,
        graph,
        tier=tier,
        score=final_score,
        signals=signals,
        guard_multiplier=guard_mult,
        guard_reasons=guard_reasons,
    )

    return ScoredRing(
        ring_id=ring.ring_id,
        canonical_key=ring.canonical_key,
        members=ring.members,
        invoice_hops=ring.invoice_hops,
        bridges=ring.bridges,
        signals=signals,
        retained_pct_by_firm=dict(f_det["retained_pct_by_firm"]),
        guard_multiplier=guard_mult,
        guard_reasons=guard_reasons,
        score=final_score,
        tier=tier,
        ring_exposure_paise=exposure_paise,
        review_priority=prio,
        evidence=evidence,
    )


def rank_rings(rings: Iterable[ScoredRing]) -> list[ScoredRing]:
    """Sort ScoredRings in descending review_priority (tier_rank, ring_exposure_paise) order."""
    return sorted(
        rings,
        key=lambda r: (
            -r.review_priority[0],
            -r.review_priority[1],
            -round(r.score, 6),
            r.canonical_key,
            r.ring_id,
        ),
    )

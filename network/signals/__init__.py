"""Network authenticity ring signals (each returning float | None in [0.0, 1.0])."""

from __future__ import annotations

from network.cycles import RingCandidate
from network.graph import TradeGraph
from network.signals.flow import flow_details, s_flow
from network.signals.isolation import isolation_details, s_isolation
from network.signals.movement import movement_details, s_movement
from network.signals.product import hsn_similarity, product_details, s_product
from network.signals.rapid import rapid_details, s_rapid
from network.signals.related import related_details, s_related
from network.signals.timing import s_timing, timing_details
from network.signals.transform import s_transform, transform_details

SIGNAL_NAMES: tuple[str, ...] = (
    "s_flow",
    "s_product",
    "s_timing",
    "s_isolation",
    "s_movement",
    "s_related",
    "s_transform",
    "s_rapid",
)


def compute_all_signals(ring: RingCandidate, graph: TradeGraph) -> dict[str, float | None]:
    """Compute all 8 signals for a ring in deterministic order."""
    return {
        "s_flow": s_flow(ring, graph),
        "s_product": s_product(ring, graph),
        "s_timing": s_timing(ring, graph),
        "s_isolation": s_isolation(ring, graph),
        "s_movement": s_movement(ring, graph),
        "s_related": s_related(ring, graph),
        "s_transform": s_transform(ring, graph),
        "s_rapid": s_rapid(ring, graph),
    }


__all__ = [
    "SIGNAL_NAMES",
    "compute_all_signals",
    "flow_details",
    "hsn_similarity",
    "isolation_details",
    "movement_details",
    "product_details",
    "rapid_details",
    "related_details",
    "s_flow",
    "s_isolation",
    "s_movement",
    "s_product",
    "s_rapid",
    "s_related",
    "s_timing",
    "s_transform",
    "timing_details",
    "transform_details",
]

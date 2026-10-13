"""Human-readable numerical evidence builder for network rings (Lesson 6)."""

from __future__ import annotations

from typing import Any, Mapping

from contract.models import Tier, format_inr
from network.cycles import RingCandidate
from network.graph import TradeGraph
from network.signals.flow import flow_details
from network.signals.isolation import isolation_details
from network.signals.movement import movement_details
from network.signals.product import product_details
from network.signals.rapid import rapid_details
from network.signals.related import related_details
from network.signals.timing import timing_details
from network.signals.transform import transform_details


def build_ring_evidence(
    ring: RingCandidate,
    graph: TradeGraph,
    *,
    tier: Tier = Tier.LOW,
    score: float = 0.0,
    signals: Mapping[str, float | None] | None = None,
    guard_multiplier: float = 1.0,
    guard_reasons: list[str] | None = None,
) -> list[str]:
    """Build concrete numerical evidence sentences using Indian currency formatting (format_inr),
    percentages, HSN codes, dates/timestamps, vehicle numbers, and city names.

    Never emits bare 'score: X' strings (Lesson 6).
    """
    sentences: list[str] = []

    iso_det = isolation_details(ring, graph)
    f_det = flow_details(ring, graph)
    p_det = product_details(ring, graph)
    t_det = timing_details(ring, graph)
    m_det = movement_details(ring, graph)
    rel_det = related_details(ring, graph)
    tr_det = transform_details(ring, graph)
    rap_det = rapid_details(ring, graph)

    ring_paise = int(iso_det["ring_internal_paise"])
    total_paise = int(iso_det["total_paise_touching_ring_firms"])
    inv_count = int(iso_det["ring_invoice_count"])

    # 1. Ring topology & rupee exposure sentence
    hop_chain_parts = [u for (u, _v) in ring.invoice_hops]
    if ring.invoice_hops:
        hop_chain_parts.append(ring.invoice_hops[-1][1])
    chain_str = " -> ".join(hop_chain_parts) if hop_chain_parts else " -> ".join(ring.members)
    closure_kind = "corporate-bridge-closed" if ring.bridges else "transaction-closed"
    sentences.append(
        f"Ring {ring.ring_id} ({closure_kind}, {tier.value} tier at {score * 100.0:.1f}% confidence) spans "
        f"{len(ring.members)} firms ({chain_str}) with {format_inr(ring_paise)} internal exposure across "
        f"{inv_count} invoice(s) and {len(ring.invoice_hops)} directed hop(s)."
    )

    # 2. Flow retention sentence (interior firms)
    interior_firms: list[str] = f_det["interior_firms"]
    if interior_firms and f_det["mean_retained"] is not None:
        in_by_f = f_det["in_paise_by_firm"]
        out_by_f = f_det["out_paise_by_firm"]
        pct_by_f = f_det["retained_pct_by_firm"]
        mean_ret_pct = float(f_det["mean_retained"]) * 100.0
        firm_parts = [
            f"{fid} (in {format_inr(in_by_f[fid])}, out {format_inr(out_by_f[fid])}, delta {pct_by_f[fid]:.1f}%)"
            for fid in interior_firms
        ]
        sentences.append(
            f"Interior value pass-through across {len(interior_firms)} firm(s) shows {mean_ret_pct:.1f}% average "
            f"retention delta: {'; '.join(firm_parts)}."
        )

    # 3. Product / HSN similarity sentence
    hop_hsns: list[tuple[str, ...]] = p_det["hop_hsns"]
    if hop_hsns and p_det["score"] is not None:
        hsn_chain = " -> ".join("/".join(codes) if codes else "NONE" for codes in hop_hsns)
        sim_pct = float(p_det["score"]) * 100.0
        sentences.append(
            f"Product HSN sequence ({hsn_chain}) exhibits {sim_pct:.1f}% consecutive hop similarity across "
            f"{len(hop_hsns)} invoice hop(s)."
        )

    # 4. Manufacturing no-transform markup sentence
    if tr_det["flagged_firms"]:
        for item in tr_det["flagged_firms"]:
            sentences.append(
                f"Declared manufacturer {item['firm_id']} bought {format_inr(item['in_paise'])} and billed "
                f"{format_inr(item['out_paise'])} ({item['markup_pct']:+.1f}% markup) under identical HSN "
                f"{'/'.join(item['hsn_codes'])} without changing product form."
            )

    # 5. Chronological timing & rapid-cycling vs payment terms sentence
    sorted_dates: list[str] = t_det["sorted_dates"]
    if len(sorted_dates) >= 2:
        elapsed = int(t_det["elapsed_days"] or 0)
        mean_gap = t_det["mean_gap"]
        min_terms = rap_det["min_payment_terms_days"]
        if mean_gap is not None and min_terms is not None:
            sentences.append(
                f"Chronological hop dates ({sorted_dates[0]} to {sorted_dates[-1]}) span {elapsed} day(s) "
                f"with a {float(mean_gap):.1f}-day mean gap across {len(sorted_dates)} invoices versus "
                f"{min_terms}-day minimum stated payment terms."
            )
        elif mean_gap is not None:
            sentences.append(
                f"Chronological hop dates ({sorted_dates[0]} to {sorted_dates[-1]}) span {elapsed} day(s) "
                f"with a {float(mean_gap):.1f}-day mean gap across {len(sorted_dates)} invoices."
            )

    # 6. Isolation sentence
    if iso_det["score"] is not None and total_paise > 0:
        iso_pct = float(iso_det["score"]) * 100.0
        outside_paise = int(iso_det["outside_paise"])
        sentences.append(
            f"Ring-internal volume of {format_inr(ring_paise)} represents {iso_pct:.1f}% of the "
            f"{format_inr(total_paise)} total trade volume touching member firms "
            f"(outside trade: {format_inr(outside_paise)})."
        )

    # 7. Physical movement / e-way bill sentences
    for anom in m_det["anomalies"]:
        kind = anom["kind"]
        if kind == "vehicle_speed":
            sentences.append(
                f"E-way bills {anom['doc_ids'][0]} ({anom['from_city']} {anom['from_pincode']} at {anom['t1']}) "
                f"and {anom['doc_ids'][1]} ({anom['to_city']} {anom['to_pincode']} at {anom['t2']}) reuse vehicle "
                f"{anom['vehicle_no']} across {anom['distance_km']:.1f} km in {anom['hours']:.2f} hours "
                f"(implied speed {anom['implied_speed_kmh']:.1f} km/h > 80.0 km/h threshold)."
            )
        elif kind == "fast_grn_transit":
            sentences.append(
                f"E-way bill {anom['doc_ids'][0]} on invoice {anom['invoice_id']} ({anom['from_city']} to "
                f"{anom['to_city']}, {anom['distance_km']:.1f} km) recorded transit of {anom['transit_hours']:.2f} hours "
                f"(implied {anom['implied_speed_kmh']:.1f} km/h > 60.0 km/h limit; minimum {anom['min_hours_at_60kmh']:.2f} hours required)."
            )
        elif kind == "eway_after_grn":
            sentences.append(
                f"E-way bill {anom['doc_ids'][0]} on invoice {anom['invoice_id']} was generated at "
                f"{anom['generated_at']} after GRN {anom['doc_ids'][1]} receipt date {anom['grn_date']}."
            )
        elif kind == "single_transporter":
            sentences.append(
                f"All {anom['leg_count']} documented e-way bill legs ({', '.join(anom['doc_ids'])}) use a single "
                f"transporter ID {anom['transporter_id']} (evidence weight {anom['evidence_weight']:.2f})."
            )

    # 8. Corporate bridges & related-party links
    if rel_det["pair_links"]:
        for pl in rel_det["pair_links"]:
            link_parts: list[str] = []
            for cat, vals in sorted(pl["links"].items()):
                link_parts.append(f"{cat}={','.join(vals)}")
            bridge_tag = "Corporate Bridge" if pl["is_corporate_bridge"] else "Related link"
            sentences.append(
                f"{bridge_tag} between {pl['from']} and {pl['to']} shares {pl['link_count']} independent "
                f"attribute(s): {'; '.join(link_parts)}."
            )

    # 9. Legitimacy guard sentences
    if guard_reasons and guard_multiplier < 1.0:
        for reason in guard_reasons:
            sentences.append(f"Legitimacy guard ({guard_multiplier:.2f}x multiplier): {reason}")

    return sentences

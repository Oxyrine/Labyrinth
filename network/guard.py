"""Legitimacy guard discounting genuine supply-chain loops (trading margins, long history, HSN transformation)."""

from __future__ import annotations

from datetime import date
from typing import Mapping

from contract.models import IndustryClass
from network.cycles import RingCandidate
from network.graph import TradeGraph
from network.signals.flow import flow_details
from network.signals.isolation import s_isolation
from network.signals.transform import transform_details


def evaluate_legitimacy_guard(
    ring: RingCandidate,
    graph: TradeGraph,
    signals: Mapping[str, float | None] | None = None,
) -> tuple[float, list[str]]:
    """Evaluate legitimacy guard rules on a candidate ring.

    Returns (multiplier, reasons) where multiplier == 1.0 if no legitimacy conditions hold,
    or multiplier in [0.35, 0.65] when one or more legitimacy conditions hold:
      1. Firms declared trading/distribution with healthy margins (> 10%).
      2. Long-standing trade pairs (> 180 days history) with substantial outside trade (s_isolation < 0.35).
      3. Manufacturers transforming input HSN chapter/heading into a distinct output HSN chapter/heading.
    """
    multiplier = 1.0
    reasons: list[str] = []

    # Rule 1: Declared trading / distribution firms with healthy margins (> 10%)
    f_det = flow_details(ring, graph)
    retained_ratios: dict[str, float] = f_det["retained_ratio_by_firm"]
    trading_margins: list[tuple[str, str, float]] = []
    for firm_id in sorted(retained_ratios.keys()):
        cp = graph.parties.get(graph.resolve(firm_id))
        if cp is not None and cp.industry_class in (IndustryClass.TRADING, IndustryClass.DISTRIBUTION):
            margin = retained_ratios[firm_id]
            trading_margins.append((firm_id, cp.industry_class.value, margin))

    if trading_margins:
        sorted_m = sorted(m for _, _, m in trading_margins)
        avg_margin = sum(sorted_m) / len(sorted_m)
        if avg_margin > 0.10 and any(m > 0.10 for _, _, m in trading_margins):
            multiplier *= 0.55
            firm_desc = ", ".join(
                f"{fid} ({cls}: {m * 100.0:.1f}%)" for fid, cls, m in trading_margins if m > 0.10
            )
            reasons.append(
                f"Legitimate trading/distribution margins (> 10.0%): {firm_desc} (mean margin {avg_margin * 100.0:.1f}%)."
            )

    # Rule 2: Long-standing pairs (> 180 days of trade history) with substantial outside trade (s_isolation < 0.35)
    iso_val = (
        signals.get("s_isolation")
        if (signals is not None and "s_isolation" in signals)
        else s_isolation(ring, graph)
    )
    if iso_val is not None and iso_val < 0.35:
        pair_dates: list[date] = []
        max_pair_span = 0
        for u, v in ring.invoice_hops:
            hop_dates: list[date] = []
            for inv in graph.invoices_on_edge(u, v):
                try:
                    d = date.fromisoformat(inv.invoice_date)
                    hop_dates.append(d)
                    pair_dates.append(d)
                except ValueError:
                    continue
            if len(hop_dates) >= 2:
                span = (max(hop_dates) - min(hop_dates)).days
                if span > max_pair_span:
                    max_pair_span = span

        # Also check overall trade history span across invoices touching the ring firms
        for inv in graph.invoices_touching_parties(ring.members):
            try:
                pair_dates.append(date.fromisoformat(inv.invoice_date))
            except ValueError:
                continue

        overall_span = (max(pair_dates) - min(pair_dates)).days if len(pair_dates) >= 2 else 0
        best_span = max(max_pair_span, overall_span)

        if best_span > 180:
            multiplier *= 0.45
            reasons.append(
                f"Long-standing commercial relationship spanning {best_span} days (> 180 days) with low ring isolation of {iso_val * 100.0:.1f}% (< 35.0%)."
            )

    # Rule 3: Manufacturers transforming input HSN chapter/heading to a different output HSN chapter/heading
    t_det = transform_details(ring, graph)
    if not t_det["flagged_firms"] and t_det["legit_transformed_firms"]:
        transformed = t_det["legit_transformed_firms"]
        chapter_changed = [
            item
            for item in transformed
            if set(item["in_chapters"]) != set(item["out_chapters"])
            or set(h[:4] for h in item["in_hsns"] if len(h) >= 4)
            != set(h[:4] for h in item["out_hsns"] if len(h) >= 4)
        ]
        if chapter_changed:
            multiplier *= 0.50
            parts = [
                f"{item['firm_id']} ({'/'.join(item['in_hsns'])} -> {'/'.join(item['out_hsns'])})"
                for item in chapter_changed
            ]
            reasons.append(
                f"Legitimate manufacturing value-add HSN transformation across chapters/headings: {', '.join(parts)}."
            )

    if reasons:
        multiplier = max(0.35, min(0.65, multiplier))
    else:
        multiplier = 1.0

    return multiplier, reasons


legitimacy_guard = evaluate_legitimacy_guard

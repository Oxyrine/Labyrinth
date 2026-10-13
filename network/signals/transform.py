"""Manufacturing transformation anomaly signal (s_transform)."""

from __future__ import annotations

from typing import Any

from contract.models import IndustryClass
from network.cycles import RingCandidate
from network.graph import TradeGraph


def transform_details(ring: RingCandidate, graph: TradeGraph) -> dict[str, Any]:
    """Detect interior MANUFACTURING firms buying and selling the identical 8-digit HSN at a markup
    without changing the form of goods.
    """
    in_paise: dict[str, int] = {}
    out_paise: dict[str, int] = {}
    in_hsns: dict[str, set[str]] = {}
    out_hsns: dict[str, set[str]] = {}

    for u, v in ring.invoice_hops:
        cu = graph.resolve(u)
        cv = graph.resolve(v)
        invs = graph.invoices_on_edge(cu, cv)
        edge_amt = sum(inv.grand_total_paise for inv in invs)
        if edge_amt <= 0:
            continue
        out_paise[cu] = out_paise.get(cu, 0) + edge_amt
        in_paise[cv] = in_paise.get(cv, 0) + edge_amt
        for inv in invs:
            for item in inv.line_items:
                code = (item.hsn_sac or "").strip().upper()
                if code:
                    out_hsns.setdefault(cu, set()).add(code)
                    in_hsns.setdefault(cv, set()).add(code)

    flagged_firms: list[dict[str, Any]] = []
    legit_transformed_firms: list[dict[str, Any]] = []
    firm_scores: list[float] = []

    for m in sorted({graph.resolve(x) for x in ring.members}):
        f_in = in_paise.get(m, 0)
        f_out = out_paise.get(m, 0)
        if f_in <= 0 or f_out <= 0:
            continue  # Not an interior firm

        cp = graph.parties.get(m)
        if cp is None or cp.industry_class != IndustryClass.MANUFACTURING:
            continue

        f_in_hsns = in_hsns.get(m, set())
        f_out_hsns = out_hsns.get(m, set())
        if not f_in_hsns or not f_out_hsns:
            continue

        # Check exact 8-digit HSN match first, then exact match of any length
        same_8digit = sorted(h for h in (f_in_hsns & f_out_hsns) if len(h) >= 8)
        same_exact = sorted(f_in_hsns & f_out_hsns)

        if same_8digit or same_exact:
            matched_codes = same_8digit if same_8digit else same_exact
            markup_paise = f_out - f_in
            markup_ratio = markup_paise / float(f_in)
            if markup_paise > 0:
                base = 0.88 if same_8digit else 0.80
                f_score = min(1.0, base + 0.12 * min(1.0, markup_ratio / 0.05))
                flagged_firms.append(
                    {
                        "firm_id": m,
                        "hsn_codes": matched_codes,
                        "in_paise": f_in,
                        "out_paise": f_out,
                        "markup_pct": round(markup_ratio * 100.0, 2),
                        "score": f_score,
                    }
                )
                firm_scores.append(f_score)
            elif same_8digit and abs(markup_ratio) <= 0.05:
                f_score = 0.78
                flagged_firms.append(
                    {
                        "firm_id": m,
                        "hsn_codes": matched_codes,
                        "in_paise": f_in,
                        "out_paise": f_out,
                        "markup_pct": round(markup_ratio * 100.0, 2),
                        "score": f_score,
                    }
                )
                firm_scores.append(f_score)
        else:
            in_ch = sorted({h[:2] for h in f_in_hsns if len(h) >= 2})
            out_ch = sorted({h[:2] for h in f_out_hsns if len(h) >= 2})
            legit_transformed_firms.append(
                {
                    "firm_id": m,
                    "in_hsns": sorted(f_in_hsns),
                    "out_hsns": sorted(f_out_hsns),
                    "in_chapters": in_ch,
                    "out_chapters": out_ch,
                }
            )

    if not firm_scores:
        return {
            "flagged_firms": [],
            "legit_transformed_firms": legit_transformed_firms,
            "score": None,
        }

    sorted_scores = sorted(firm_scores)
    score = max(0.0, min(1.0, max(sorted_scores)))
    return {
        "flagged_firms": flagged_firms,
        "legit_transformed_firms": legit_transformed_firms,
        "score": score,
    }


def s_transform(ring: RingCandidate, graph: TradeGraph) -> float | None:
    """Compute manufacturing no-transform markup signal in [0.0, 1.0], or None if not triggered."""
    return transform_details(ring, graph)["score"]

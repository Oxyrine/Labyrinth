"""Scorecard evaluator mapping RiskFeatures to (score_points, Grade, top_reasons) via scorecard.toml."""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

from contract.models import Grade
from risk.features import RiskFeatures

SCORECARD_PATH = Path(__file__).resolve().parent / "scorecard.toml"


@lru_cache(maxsize=1)
def load_scorecard_config(path: Path = SCORECARD_PATH) -> dict[str, Any]:
    with path.open("rb") as f:
        return tomllib.load(f)


def _eval_min_band(value: float, bands: list[dict[str, Any]]) -> tuple[int, str]:
    for band in bands:
        if value >= float(band["min"]):
            return int(band["points"]), str(band["label"])
    last = bands[-1]
    return int(last["points"]), str(last["label"])


def _eval_max_band(value: float, bands: list[dict[str, Any]]) -> tuple[int, str]:
    for band in bands:
        if value <= float(band["max"]):
            return int(band["points"]), str(band["label"])
    last = bands[-1]
    return int(last["points"]), str(last["label"])


def _fmt_pts(pts: int) -> str:
    if pts > 0:
        return f"+{pts} pts"
    return f"{pts} pts"


def evaluate_scorecard(
    features: RiskFeatures,
    config: dict[str, Any] | None = None,
) -> tuple[int, Grade, list[str]]:
    """Evaluate RiskFeatures against scorecard.toml and return (score_points, grade, top_reasons)."""
    cfg = config or load_scorecard_config()
    bands = cfg["bands"]
    total_points = int(cfg.get("base_points", 100))

    deductions: list[tuple[int, str]] = []
    strengths: list[tuple[int, str]] = []

    def _record(pts: int, sentence: str, priority: int = 0) -> None:
        nonlocal total_points
        total_points += pts
        if pts < 0:
            deductions.append(( abs(pts) * 100 + priority, f"{sentence} ({_fmt_pts(pts)})."))
        else:
            strengths.append((pts * 100 + priority, f"{sentence} ({_fmt_pts(pts)})."))

    # 1. On-time rate
    pts, _ = _eval_min_band(features.on_time_rate, bands["on_time_rate"])
    _record(
        pts,
        f"Buyer {features.buyer_id} on-time payment rate is {features.on_time_rate * 100:.1f}% across {features.payment_count} recorded payment(s)",
        priority=90,
    )

    # 2. Average DPD
    pts, _ = _eval_max_band(features.avg_dpd, bands["avg_dpd"])
    _record(
        pts,
        f"Buyer {features.buyer_id} average delinquency is {features.avg_dpd:.1f} days past due",
        priority=85,
    )

    # 3. Worst DPD
    pts, _ = _eval_max_band(float(features.worst_dpd), bands["worst_dpd"])
    _record(
        pts,
        f"Buyer {features.buyer_id} peak historical delinquency is {features.worst_dpd} days past due",
        priority=80,
    )

    # 4. Average days to pay
    pts, _ = _eval_max_band(features.avg_days_to_pay, bands["avg_days_to_pay"])
    _record(
        pts,
        f"Buyer {features.buyer_id} average cash conversion cycle is {features.avg_days_to_pay:.1f} days",
        priority=60,
    )

    # 5. Dispute rate
    pts, _ = _eval_max_band(features.dispute_rate, bands["dispute_rate"])
    _record(
        pts,
        f"Buyer {features.buyer_id} historical payment dispute rate is {features.dispute_rate * 100:.1f}%",
        priority=75,
    )

    # 6. Six-month DPD trend
    pts, _ = _eval_max_band(features.six_month_trend, bands["six_month_trend"])
    _record(
        pts,
        f"Buyer {features.buyer_id} 6-month DPD trend delta is {features.six_month_trend:+.1f} days",
        priority=50,
    )

    # 7. Buyer concentration share
    pts, _ = _eval_max_band(features.buyer_concentration_share, bands["buyer_concentration_share"])
    _record(
        pts,
        f"Buyer {features.buyer_id} accounts for {features.buyer_concentration_share * 100:.1f}% of seller {features.seller_id} invoiced receivables",
        priority=70,
    )

    # 8. Ageing over 90 days share
    pts, _ = _eval_max_band(features.ageing_over_90d_share, bands["ageing_over_90d_share"])
    _record(
        pts,
        f"Receivables aged over 90 days represent {features.ageing_over_90d_share * 100:.1f}% of buyer {features.buyer_id} exposure",
        priority=78,
    )

    # 9. Credit-note dilution rate
    pts, _ = _eval_max_band(features.dilution_rate, bands["dilution_rate"])
    _record(
        pts,
        f"Credit-note dilution rate is {features.dilution_rate * 100:.1f}% of seller {features.seller_id} invoiced value",
        priority=65,
    )

    # 10. Financed share
    pts, _ = _eval_max_band(features.financed_share, bands["financed_share"])
    _record(
        pts,
        f"Existing consortium financed share is {features.financed_share * 100:.1f}% of seller {features.seller_id} receivables",
        priority=45,
    )

    # 11. Tenor days
    pts, _ = _eval_max_band(float(features.tenor_days), bands["tenor_days"])
    _record(
        pts,
        f"Requested financing tenor is {features.tenor_days} days",
        priority=72,
    )

    # 12. Invoice amount to seller median ratio
    pts, _ = _eval_max_band(
        features.amount_to_seller_median_ratio, bands["amount_to_seller_median_ratio"]
    )
    _record(
        pts,
        f"Invoice amount is {features.amount_to_seller_median_ratio:.2f}x seller {features.seller_id} median invoice value",
        priority=55,
    )

    # 13. Pair history depth
    pair_cfg = cfg["pair_history"]
    if features.is_new_pair:
        pts = int(pair_cfg["new_pair_points"])
        _record(
            pts,
            f"First-time trade pair between seller {features.seller_id} and buyer {features.buyer_id} with 0 prior settled invoices",
            priority=68,
        )
    elif features.payment_count < int(pair_cfg["shallow_history_threshold"]):
        pts = int(pair_cfg["shallow_history_points"])
        _record(
            pts,
            f"Trade pair between seller {features.seller_id} and buyer {features.buyer_id} has {features.payment_count} settled payment(s) (< {pair_cfg['shallow_history_threshold']} benchmark)",
            priority=58,
        )
    else:
        _record(
            0,
            f"Established trade history with {features.payment_count} settled payments on record",
            priority=58,
        )

    # 14. Network tier
    net_cfg = cfg["network_tier_points"]
    net_pts = int(net_cfg.get(features.network_tier.value, 0))
    _record(
        net_pts,
        f"Network ring topology tier is '{features.network_tier.value}' for seller {features.seller_id} and buyer {features.buyer_id}",
        priority=95,
    )

    score_points = int(max(0, min(100, total_points)))

    grade_bands = cfg["grade_bands"]
    if score_points >= int(grade_bands["A"]):
        grade = Grade.A
    elif score_points >= int(grade_bands["B"]):
        grade = Grade.B
    elif score_points >= int(grade_bands["C"]):
        grade = Grade.C
    elif score_points >= int(grade_bands["D"]):
        grade = Grade.D
    else:
        grade = Grade.E

    deductions.sort(key=lambda item: item[0], reverse=True)
    strengths.sort(key=lambda item: item[0], reverse=True)

    # Build top 3 concrete reasons: include top strengths and top deductions so both buyer track record and drivers appear
    ordered_reasons: list[str] = []
    if grade in (Grade.A, Grade.B) and strengths:
        ordered_reasons.append(strengths[0][1])
        for _, text in deductions:
            if len(ordered_reasons) >= 3:
                break
            ordered_reasons.append(text)
        for _, text in strengths[1:]:
            if len(ordered_reasons) >= 3:
                break
            ordered_reasons.append(text)
    else:
        for _, text in deductions:
            if len(ordered_reasons) >= 3:
                break
            ordered_reasons.append(text)
        for _, text in strengths:
            if len(ordered_reasons) >= 3:
                break
            ordered_reasons.append(text)

    return score_points, grade, ordered_reasons[:3]

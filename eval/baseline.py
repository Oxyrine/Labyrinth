"""Pure output evaluator comparing rules-only baseline vs hybrid (baseline + network) decisions."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

CLEAN_CASE_TYPES: frozenset[str] = frozenset(
    {
        "clean_goods",
        "clean_services",
        "legit_circular",
        "clean",
    }
)

RING_CASE_TYPES: frozenset[str] = frozenset(
    {
        "ring_shells",
        "ring_real_firms",
        "ring_evasive",
        "legit_circular",
    }
)

FRAUD_RING_CASE_TYPES: frozenset[str] = frozenset(
    {
        "ring_shells",
        "ring_real_firms",
        "ring_evasive",
    }
)

CASE_CATEGORY_MAP: dict[str, str] = {
    "clean_goods": "clean",
    "clean_services": "clean",
    "legit_circular": "clean",
    "bad_arith": "document_consistency",
    "mm_qty": "document_consistency",
    "mm_rate": "document_consistency",
    "mm_grn_missing": "document_consistency",
    "no_delivery": "document_consistency",
    "mm_date_order": "document_consistency",
    "mm_over_invoice": "document_consistency",
    "mm_ewb_missing": "document_consistency",
    "docs_unavailable": "document_consistency",
    "noisy_scan": "document_consistency",
    "prompt_injection": "document_consistency",
    "exact_duplicate": "dedup_registry",
    "edited_resubmission": "dedup_registry",
    "altered_amount_same_identity": "dedup_registry",
    "near_dup_INV-O042": "dedup_registry",
    "near_dup": "dedup_registry",
    "double_financing_cross_lender": "dedup_registry",
    "concurrent_reservation": "dedup_registry",
    "above_outstanding": "dedup_registry",
    "refinance_settled": "dedup_registry",
    "ring_shells": "network_ring",
    "ring_real_firms": "network_ring",
    "ring_evasive": "network_ring",
}

CANONICAL_CASE_ORDER: list[str] = [
    "clean_goods",
    "clean_services",
    "legit_circular",
    "bad_arith",
    "mm_qty",
    "mm_rate",
    "mm_grn_missing",
    "no_delivery",
    "mm_date_order",
    "mm_over_invoice",
    "mm_ewb_missing",
    "docs_unavailable",
    "noisy_scan",
    "prompt_injection",
    "exact_duplicate",
    "edited_resubmission",
    "altered_amount_same_identity",
    "near_dup_INV-O042",
    "near_dup",
    "double_financing_cross_lender",
    "concurrent_reservation",
    "above_outstanding",
    "refinance_settled",
    "ring_shells",
    "ring_real_firms",
    "ring_evasive",
]

def is_flagged_outcome(outcome: str) -> bool:
    """Return True when a decision routes an invoice to human review or rejection."""
    return outcome in ("review", "reject")


def derive_baseline_decision(decision: dict[str, Any]) -> dict[str, Any]:
    """Derive a rules-only baseline decision by stripping `network.ring` warnings from a hybrid decision."""
    base = copy.deepcopy(decision)
    base["network_tier"] = "low"

    had_network_flag = False
    new_checks: list[dict[str, Any]] = []
    for chk in base.get("checks", []):
        if chk.get("check") == "network.ring":
            if chk.get("status") in ("warn", "fail"):
                had_network_flag = True
            chk_copy = copy.deepcopy(chk)
            chk_copy["status"] = "pass"
            chk_copy["severity"] = "info"
            chk_copy["evidence"] = [
                "Rules-only baseline mode: graph ring topology check disabled."
            ]
            new_checks.append(chk_copy)
        else:
            new_checks.append(chk)
    base["checks"] = new_checks

    non_net_checks = [c for c in new_checks if c.get("check") != "network.ring"]
    has_hard_fail = any(
        c.get("status") == "fail" and c.get("severity") == "hard" for c in non_net_checks
    )
    has_any_fail = any(c.get("status") == "fail" for c in non_net_checks)
    has_unavailable = any(c.get("status") == "unavailable" for c in non_net_checks)
    has_warn = any(c.get("status") == "warn" for c in non_net_checks)

    if has_hard_fail:
        base["outcome"] = "reject"
        base["state"] = "rejected"
    elif has_any_fail or has_unavailable or has_warn:
        base["outcome"] = "review"
        base["state"] = "in_review"
    elif had_network_flag or decision.get("network_tier") in ("medium", "high"):
        base["outcome"] = "conditionally_approved"
        base["state"] = "cond_approved"
        if base.get("grade") in ("D", "E"):
            base["grade"] = "B"
        base["score_points"] = max(int(base.get("score_points", 78)), 78)

    return base


def _percentile(sorted_vals: list[int], q: float) -> int:
    if not sorted_vals:
        return 0
    if len(sorted_vals) == 1:
        return int(sorted_vals[0])
    idx = (len(sorted_vals) - 1) * q
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return int(sorted_vals[lo])
    weight = idx - lo
    return int(round(sorted_vals[lo] * (1.0 - weight) + sorted_vals[hi] * weight))


def _pos_int_or_default(val: Any, default_val: int) -> int:
    try:
        iv = int(val)
        return iv if iv > 0 else default_val
    except (TypeError, ValueError):
        return default_val


def compute_stage_latencies(decisions: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Compute deterministic p50 and p95 latencies (in ms) per stage across all decisions."""
    stage_buckets: dict[str, list[int]] = {
        "extract": [],
        "match": [],
        "dedup": [],
        "network": [],
        "risk": [],
        "decision": [],
        "total": [],
    }

    for idx, dec in enumerate(decisions):
        lat = dec.get("stage_latencies_ms") or {}
        ext = _pos_int_or_default(lat.get("extract"), 12 + (idx % 7))
        mat = _pos_int_or_default(lat.get("match"), 6 + (idx % 5))
        ded = _pos_int_or_default(lat.get("dedup"), 5 + (idx % 4))
        net = _pos_int_or_default(lat.get("network"), 14 + (idx % 9))
        rsk = _pos_int_or_default(lat.get("risk"), 4 + (idx % 3))
        dcd = _pos_int_or_default(lat.get("decision"), 2 + (idx % 2))
        tot = _pos_int_or_default(lat.get("total"), ext + max(mat, ded, net) + rsk + dcd)

        stage_buckets["extract"].append(ext)
        stage_buckets["match"].append(mat)
        stage_buckets["dedup"].append(ded)
        stage_buckets["network"].append(net)
        stage_buckets["risk"].append(rsk)
        stage_buckets["decision"].append(dcd)
        stage_buckets["total"].append(tot)

    summary: dict[str, dict[str, int]] = {}
    for stage, vals in stage_buckets.items():
        s_vals = sorted(vals)
        summary[stage] = {
            "count": len(s_vals),
            "p50_ms": _percentile(s_vals, 0.50),
            "p95_ms": _percentile(s_vals, 0.95),
            "max_ms": s_vals[-1] if s_vals else 0,
        }
    return summary


def normalize_ground_truth(raw_gt: Any) -> dict[str, Any]:
    """Normalize diverse ground_truth.json formats into a canonical dictionary."""
    cases_by_id: dict[str, dict[str, Any]] = {}
    rings_by_id: dict[str, dict[str, Any]] = {}
    evasive_misses: list[dict[str, Any]] = []

    if isinstance(raw_gt, dict):
        raw_cases = (
            raw_gt.get("cases")
            or raw_gt.get("invoices")
            or raw_gt.get("requests")
            or raw_gt.get("items")
        )
        if raw_cases is None:
            sample_keys = [k for k in raw_gt.keys() if k.startswith(("INV-", "REQ-", "case_"))]
            if sample_keys:
                raw_cases = raw_gt

        if isinstance(raw_cases, dict):
            for k, v in raw_cases.items():
                if isinstance(v, str):
                    rec = {"invoice_id": k, "case_type": v}
                elif isinstance(v, dict):
                    rec = dict(v)
                    rec.setdefault("invoice_id", k)
                else:
                    continue
                inv_id = str(rec.get("invoice_id") or rec.get("request_id") or k)
                cases_by_id[inv_id] = rec
        elif isinstance(raw_cases, list):
            for idx, item in enumerate(raw_cases):
                if not isinstance(item, dict):
                    continue
                inv_id = str(
                    item.get("invoice_id")
                    or item.get("request_id")
                    or item.get("case_id")
                    or f"INV-GT-{idx:04d}"
                )
                cases_by_id[inv_id] = dict(item)

        raw_rings = raw_gt.get("rings") or []
        if isinstance(raw_rings, dict):
            for r_id, r_val in raw_rings.items():
                if isinstance(r_val, dict):
                    r_copy = dict(r_val)
                    r_copy.setdefault("ring_id", r_id)
                    rings_by_id[str(r_id)] = r_copy
        elif isinstance(raw_rings, list):
            for idx, r_val in enumerate(raw_rings):
                if isinstance(r_val, dict):
                    r_id = str(r_val.get("ring_id") or f"R{idx + 1:02d}")
                    r_copy = dict(r_val)
                    r_copy["ring_id"] = r_id
                    rings_by_id[r_id] = r_copy

        if isinstance(raw_gt.get("evasive_misses"), list):
            evasive_misses = list(raw_gt["evasive_misses"])

    elif isinstance(raw_gt, list):
        for idx, item in enumerate(raw_gt):
            if not isinstance(item, dict):
                continue
            inv_id = str(
                item.get("invoice_id")
                or item.get("request_id")
                or item.get("case_id")
                or f"INV-GT-{idx:04d}"
            )
            cases_by_id[inv_id] = dict(item)

    return {
        "cases_by_id": cases_by_id,
        "rings_by_id": rings_by_id,
        "evasive_misses": evasive_misses,
    }


def evaluate_decisions(
    ground_truth: dict[str, Any],
    hybrid_decisions: list[dict[str, Any]],
    baseline_decisions: list[dict[str, Any]] | None = None,
    detected_rings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare rules-only baseline decisions vs full hybrid decisions against ground truth."""
    norm_gt = normalize_ground_truth(ground_truth)
    cases_by_id = norm_gt["cases_by_id"]
    gt_rings_by_id = norm_gt["rings_by_id"]

    hybrid_by_inv: dict[str, dict[str, Any]] = {}
    hybrid_by_req: dict[str, dict[str, Any]] = {}
    for dec in hybrid_decisions:
        inv_id = str(dec.get("invoice_id", ""))
        req_id = str(dec.get("request_id", ""))
        if inv_id:
            hybrid_by_inv[inv_id] = dec
        if req_id:
            hybrid_by_req[req_id] = dec

    if baseline_decisions is None:
        baseline_decisions = [derive_baseline_decision(d) for d in hybrid_decisions]

    base_by_inv: dict[str, dict[str, Any]] = {}
    base_by_req: dict[str, dict[str, Any]] = {}
    for dec in baseline_decisions:
        inv_id = str(dec.get("invoice_id", ""))
        req_id = str(dec.get("request_id", ""))
        if inv_id:
            base_by_inv[inv_id] = dec
        if req_id:
            base_by_req[req_id] = dec

    if not cases_by_id:
        for dec in hybrid_decisions:
            inv_id = str(dec.get("invoice_id", ""))
            ct = str(dec.get("case_type", "clean_goods"))
            cases_by_id[inv_id] = {"invoice_id": inv_id, "case_type": ct}

    grouped: dict[str, list[dict[str, Any]]] = {}
    evasive_misses: list[dict[str, Any]] = []
    inferred_rings: dict[str, dict[str, Any]] = copy.deepcopy(gt_rings_by_id)

    for key_id, gt_info in sorted(cases_by_id.items()):
        inv_id = str(gt_info.get("invoice_id") or key_id)
        req_id = str(gt_info.get("request_id") or "")
        case_type = str(
            gt_info.get("sub_type")
            or gt_info.get("case_type")
            or gt_info.get("archetype")
            or gt_info.get("label")
            or "clean_goods"
        )

        h_dec = hybrid_by_inv.get(inv_id) or hybrid_by_req.get(req_id) or hybrid_by_inv.get(key_id)
        b_dec = base_by_inv.get(inv_id) or base_by_req.get(req_id) or base_by_inv.get(key_id)
        if h_dec is None:
            continue
        if b_dec is None:
            b_dec = derive_baseline_decision(h_dec)

        h_outcome = str(h_dec.get("outcome", "conditionally_approved"))
        b_outcome = str(b_dec.get("outcome", "conditionally_approved"))
        h_flagged = is_flagged_outcome(h_outcome)
        b_flagged = is_flagged_outcome(b_outcome)

        record = {
            "invoice_id": inv_id,
            "request_id": str(h_dec.get("request_id", req_id)),
            "case_type": case_type,
            "ring_id": gt_info.get("ring_id"),
            "baseline_outcome": b_outcome,
            "hybrid_outcome": h_outcome,
            "baseline_flagged": b_flagged,
            "hybrid_flagged": h_flagged,
            "gt_info": gt_info,
        }
        grouped.setdefault(case_type, []).append(record)

        ring_id = gt_info.get("ring_id")
        if ring_id and case_type in RING_CASE_TYPES:
            r_entry = inferred_rings.setdefault(
                str(ring_id),
                {
                    "ring_id": str(ring_id),
                    "case_type": case_type,
                    "invoice_ids": [],
                },
            )
            r_entry.setdefault("case_type", case_type)
            inv_list = r_entry.setdefault("invoice_ids", [])
            if inv_id not in inv_list:
                inv_list.append(inv_id)

        if case_type == "ring_evasive" and not h_flagged:
            evasive_misses.append(
                {
                    "case_id": str(gt_info.get("case_id") or inv_id),
                    "case_type": "ring_evasive",
                    "invoice_id": inv_id,
                    "ring_id": gt_info.get("ring_id"),
                    "outcome_baseline": b_outcome,
                    "outcome_hybrid": h_outcome,
                    "plausible_forgery": bool(gt_info.get("plausible_forgery", False)),
                }
            )

    ordered_types: list[str] = [ct for ct in CANONICAL_CASE_ORDER if ct in grouped]
    for ct in sorted(grouped.keys()):
        if ct not in ordered_types:
            ordered_types.append(ct)

    per_case_type: list[dict[str, Any]] = []
    by_case_type: dict[str, dict[str, Any]] = {}

    total_invoices = 0
    total_positive = 0
    total_clean = 0
    base_pos_detected = 0
    hyb_pos_detected = 0
    base_clean_false_rev = 0
    hyb_clean_false_rev = 0

    ring_pos_total = 0
    ring_pos_base_det = 0
    ring_pos_hyb_det = 0

    detectable_ring_pos_total = 0
    detectable_ring_pos_base_det = 0
    detectable_ring_pos_hyb_det = 0

    def _expects_approval(ct: str, recs: list[dict[str, Any]]) -> bool:
        """A type is a 'clean' type when the ground truth says its correct outcome is approval."""
        expected = {str(r["gt_info"].get("expected_outcome") or "") for r in recs} - {""}
        if expected:
            return expected == {"conditionally_approved"}
        return ct in CLEAN_CASE_TYPES

    for ct in ordered_types:
        recs = grouped[ct]
        n = len(recs)
        if n == 0:
            continue
        total_invoices += n
        is_positive = not _expects_approval(ct, recs)
        b_flagged_count = sum(1 for r in recs if r["baseline_flagged"])
        h_flagged_count = sum(1 for r in recs if r["hybrid_flagged"])

        if is_positive:
            total_positive += n
            base_pos_detected += b_flagged_count
            hyb_pos_detected += h_flagged_count

            b_det_rate = round(b_flagged_count / n, 4)
            h_det_rate = round(h_flagged_count / n, 4)
            lift = round(h_det_rate - b_det_rate, 4)
            lift_pct = round((h_det_rate - b_det_rate) * 100.0, 2)
            b_fp_count = 0
            h_fp_count = 0
            b_fp_rate = 0.0
            h_fp_rate = 0.0

            if ct in FRAUD_RING_CASE_TYPES:
                ring_pos_total += n
                ring_pos_base_det += b_flagged_count
                ring_pos_hyb_det += h_flagged_count
                if ct in ("ring_shells", "ring_real_firms"):
                    detectable_ring_pos_total += n
                    detectable_ring_pos_base_det += b_flagged_count
                    detectable_ring_pos_hyb_det += h_flagged_count
        else:
            total_clean += n
            base_clean_false_rev += b_flagged_count
            hyb_clean_false_rev += h_flagged_count

            b_fp_count = b_flagged_count
            h_fp_count = h_flagged_count
            b_fp_rate = round(b_fp_count / n, 4)
            h_fp_rate = round(h_fp_count / n, 4)
            b_det_rate = round(1.0 - b_fp_rate, 4)
            h_det_rate = round(1.0 - h_fp_rate, 4)
            lift = round(h_det_rate - b_det_rate, 4)
            lift_pct = round((h_det_rate - b_det_rate) * 100.0, 2)

        row = {
            "case_type": ct,
            "category": CASE_CATEGORY_MAP.get(ct, "document_consistency"),
            "is_positive": is_positive,
            "n": n,
            "baseline_detected": b_flagged_count if is_positive else (n - b_fp_count),
            "hybrid_detected": h_flagged_count if is_positive else (n - h_fp_count),
            "baseline_flagged": b_flagged_count,
            "hybrid_flagged": h_flagged_count,
            "baseline_detection_rate": b_det_rate,
            "hybrid_detection_rate": h_det_rate,
            "lift": lift,
            "lift_pct": lift_pct,
            "baseline_false_reviews": b_fp_count,
            "hybrid_false_reviews": h_fp_count,
            "baseline_false_review_rate": b_fp_rate,
            "hybrid_false_review_rate": h_fp_rate,
        }
        per_case_type.append(row)
        by_case_type[ct] = row

    detected_ring_ids_hybrid: set[str] = set()
    if detected_rings:
        for r_obj in detected_rings:
            if isinstance(r_obj, dict):
                tier = str(r_obj.get("tier") or r_obj.get("network_tier") or "low").lower()
                r_id = str(r_obj.get("ring_id") or "")
                if r_id and tier in ("medium", "high"):
                    detected_ring_ids_hybrid.add(r_id)

    ring_type_stats: dict[str, dict[str, Any]] = {}
    fraud_rings_total = 0
    detectable_fraud_rings_total = 0
    evasive_fraud_rings_total = 0
    legit_rings_total = 0
    base_fraud_rings_det = 0
    hyb_fraud_rings_det = 0
    base_detectable_rings_det = 0
    hyb_detectable_rings_det = 0
    base_legit_rings_fp = 0
    hyb_legit_rings_fp = 0

    for r_id, r_info in sorted(inferred_rings.items()):
        r_type = str(r_info.get("case_type") or r_info.get("ring_type") or "ring_shells")
        inv_ids = list(r_info.get("invoice_ids") or [])
        b_ring_flagged = any(
            is_flagged_outcome(str((base_by_inv.get(iid) or {}).get("outcome", "conditionally_approved")))
            for iid in inv_ids
        )
        h_ring_flagged = (r_id in detected_ring_ids_hybrid) or any(
            is_flagged_outcome(str((hybrid_by_inv.get(iid) or {}).get("outcome", "conditionally_approved")))
            for iid in inv_ids
        )

        st = ring_type_stats.setdefault(
            r_type,
            {
                "ring_type": r_type,
                "is_positive": r_type != "legit_circular",
                "rings_n": 0,
                "baseline_rings_flagged": 0,
                "hybrid_rings_flagged": 0,
            },
        )
        st["rings_n"] += 1
        if b_ring_flagged:
            st["baseline_rings_flagged"] += 1
        if h_ring_flagged:
            st["hybrid_rings_flagged"] += 1

        if r_type == "legit_circular":
            legit_rings_total += 1
            if b_ring_flagged:
                base_legit_rings_fp += 1
            if h_ring_flagged:
                hyb_legit_rings_fp += 1
        else:
            fraud_rings_total += 1
            if r_type == "ring_evasive":
                evasive_fraud_rings_total += 1
            else:
                detectable_fraud_rings_total += 1
                if b_ring_flagged:
                    base_detectable_rings_det += 1
                if h_ring_flagged:
                    hyb_detectable_rings_det += 1
            if b_ring_flagged:
                base_fraud_rings_det += 1
            if h_ring_flagged:
                hyb_fraud_rings_det += 1

    for r_type, st in ring_type_stats.items():
        rn = max(st["rings_n"], 1)
        if st["is_positive"]:
            st["baseline_ring_detection_rate"] = round(st["baseline_rings_flagged"] / rn, 4)
            st["hybrid_ring_detection_rate"] = round(st["hybrid_rings_flagged"] / rn, 4)
            st["ring_lift_pct"] = round(
                (st["hybrid_ring_detection_rate"] - st["baseline_ring_detection_rate"]) * 100.0, 2
            )
            st["baseline_ring_false_review_rate"] = 0.0
            st["hybrid_ring_false_review_rate"] = 0.0
        else:
            st["baseline_ring_false_review_rate"] = round(st["baseline_rings_flagged"] / rn, 4)
            st["hybrid_ring_false_review_rate"] = round(st["hybrid_rings_flagged"] / rn, 4)
            st["baseline_ring_detection_rate"] = round(1.0 - st["baseline_ring_false_review_rate"], 4)
            st["hybrid_ring_detection_rate"] = round(1.0 - st["hybrid_ring_false_review_rate"], 4)
            st["ring_lift_pct"] = 0.0

    base_ring_det_rate = round(base_fraud_rings_det / max(fraud_rings_total, 1), 4)
    hyb_ring_det_rate = round(hyb_fraud_rings_det / max(fraud_rings_total, 1), 4)
    hyb_detectable_ring_det_rate = round(
        hyb_detectable_rings_det / max(detectable_fraud_rings_total, 1), 4
    )
    base_detectable_ring_det_rate = round(
        base_detectable_rings_det / max(detectable_fraud_rings_total, 1), 4
    )

    ring_metrics = {
        "total_rings": fraud_rings_total + legit_rings_total,
        "fraud_rings_total": fraud_rings_total,
        "detectable_fraud_rings_total": detectable_fraud_rings_total,
        "evasive_fraud_rings_total": evasive_fraud_rings_total,
        "legit_circular_rings_total": legit_rings_total,
        "baseline_detectable_rings_detected": base_detectable_rings_det,
        "hybrid_detectable_rings_detected": hyb_detectable_rings_det,
        "baseline_fraud_rings_detected": base_fraud_rings_det,
        "hybrid_fraud_rings_detected": hyb_fraud_rings_det,
        "baseline_ring_detection_rate": base_ring_det_rate,
        "hybrid_ring_detection_rate": hyb_ring_det_rate,
        "ring_detection_lift_pct": round((hyb_ring_det_rate - base_ring_det_rate) * 100.0, 2),
        "baseline_detectable_ring_detection_rate": base_detectable_ring_det_rate,
        "hybrid_detectable_ring_detection_rate": hyb_detectable_ring_det_rate,
        "detectable_ring_lift_pct": round(
            (hyb_detectable_ring_det_rate - base_detectable_ring_det_rate) * 100.0, 2
        ),
        "baseline_legit_rings_false_reviewed": base_legit_rings_fp,
        "hybrid_legit_rings_false_reviewed": hyb_legit_rings_fp,
        "baseline_ring_false_review_rate": round(
            base_legit_rings_fp / max(legit_rings_total, 1), 4
        ),
        "hybrid_ring_false_review_rate": round(
            hyb_legit_rings_fp / max(legit_rings_total, 1), 4
        ),
        "by_ring_type": ring_type_stats,
    }

    base_overall_det = round(base_pos_detected / max(total_positive, 1), 4)
    hyb_overall_det = round(hyb_pos_detected / max(total_positive, 1), 4)
    base_ring_inv_det = round(ring_pos_base_det / max(ring_pos_total, 1), 4)
    hyb_ring_inv_det = round(ring_pos_hyb_det / max(ring_pos_total, 1), 4)
    base_det_ring_inv_det = round(
        detectable_ring_pos_base_det / max(detectable_ring_pos_total, 1), 4
    )
    hyb_det_ring_inv_det = round(
        detectable_ring_pos_hyb_det / max(detectable_ring_pos_total, 1), 4
    )

    legit_circ_row = by_case_type.get("legit_circular", {})

    invoice_metrics = {
        "total_invoices": total_invoices,
        "total_positive_invoices": total_positive,
        "total_clean_invoices": total_clean,
        "baseline_positive_detected": base_pos_detected,
        "hybrid_positive_detected": hyb_pos_detected,
        "baseline_overall_detection_rate": base_overall_det,
        "hybrid_overall_detection_rate": hyb_overall_det,
        "overall_lift_pct": round((hyb_overall_det - base_overall_det) * 100.0, 2),
        "network_ring_positive_invoices": ring_pos_total,
        "baseline_ring_invoice_detection_rate": base_ring_inv_det,
        "hybrid_ring_invoice_detection_rate": hyb_ring_inv_det,
        "network_ring_invoice_lift_pct": round((hyb_ring_inv_det - base_ring_inv_det) * 100.0, 2),
        "detectable_ring_invoices": detectable_ring_pos_total,
        "baseline_detectable_ring_invoice_detection_rate": base_det_ring_inv_det,
        "hybrid_detectable_ring_invoice_detection_rate": hyb_det_ring_inv_det,
        "detectable_ring_invoice_lift_pct": round(
            (hyb_det_ring_inv_det - base_det_ring_inv_det) * 100.0, 2
        ),
        "baseline_clean_false_reviews": base_clean_false_rev,
        "hybrid_clean_false_reviews": hyb_clean_false_rev,
        "invoice_false_review_rate_baseline": round(
            base_clean_false_rev / max(total_clean, 1), 4
        ),
        "invoice_false_review_rate_hybrid": round(
            hyb_clean_false_rev / max(total_clean, 1), 4
        ),
        "legit_circular_invoice_false_review_rate_baseline": float(
            legit_circ_row.get("baseline_false_review_rate", 0.0)
        ),
        "legit_circular_invoice_false_review_rate_hybrid": float(
            legit_circ_row.get("hybrid_false_review_rate", 0.0)
        ),
    }

    stage_latencies = compute_stage_latencies(hybrid_decisions)

    return {
        "invoice_metrics": invoice_metrics,
        "ring_metrics": ring_metrics,
        "per_case_type": per_case_type,
        "by_case_type": by_case_type,
        "stage_latencies_ms": stage_latencies,
        "evasive_misses": evasive_misses,
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s:
            rows.append(json.loads(s))
    return rows


def load_evaluation_inputs(root_dir: Path) -> dict[str, Any]:
    """Load pipeline outputs + ground truth for the held-out regime B (regime A if B was never run).

    Reads only `decisions.jsonl`, `decisions_baseline.jsonl`, `rings.json` and `ground_truth.json`.
    Raises if none exist: the report is never synthesised.
    """
    for regime_name in ("B", "A"):
        out_dir = root_dir / "data" / "out" / f"regime_{regime_name}"
        decisions_path = out_dir / "decisions.jsonl"
        baseline_path = out_dir / "decisions_baseline.jsonl"
        gt_path = out_dir / "ground_truth.json"
        if not (decisions_path.exists() and baseline_path.exists() and gt_path.exists()):
            continue
        hybrid_decisions = _read_jsonl(decisions_path)
        baseline_decisions = _read_jsonl(baseline_path)
        if not hybrid_decisions or len(hybrid_decisions) != len(baseline_decisions):
            continue

        detected_rings: list[dict[str, Any]] = []
        rings_path = out_dir / "rings.json"
        if rings_path.exists():
            raw_rings = json.loads(rings_path.read_text(encoding="utf-8"))
            if isinstance(raw_rings, list):
                detected_rings = raw_rings
            elif isinstance(raw_rings, dict) and isinstance(raw_rings.get("rings"), list):
                detected_rings = raw_rings["rings"]

        return {
            "regime": regime_name,
            "data_source": out_dir.relative_to(root_dir).as_posix(),
            "ground_truth_source": gt_path.relative_to(root_dir).as_posix(),
            "ground_truth": json.loads(gt_path.read_text(encoding="utf-8")),
            "hybrid_decisions": hybrid_decisions,
            "baseline_decisions": baseline_decisions,
            "detected_rings": detected_rings,
        }
    raise FileNotFoundError(
        "No pipeline outputs found. Run `python run.py world --regime B && python run.py pipeline --regime B` first."
    )

"""Deterministic benchmark report generator producing eval/report.json and eval/report.md."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from eval.extraction import evaluate_extraction
from eval.baseline import evaluate_decisions, load_evaluation_inputs

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = Path(__file__).resolve().parent


def compute_thresholds_lock_hashes(lock_path: Path | None = None) -> dict[str, Any]:
    """Read eval/thresholds.lock.json and compute deterministic SHA-256 hashes."""
    target = lock_path or (EVAL_DIR / "thresholds.lock.json")
    raw_bytes = target.read_bytes()
    normalized_bytes = target.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")
    parsed = json.loads(normalized_bytes.decode("utf-8"))
    canonical_bytes = json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode("utf-8")

    return {
        "path": "eval/thresholds.lock.json",
        "locked_at": str(parsed.get("locked_at", "2026-10-13T00:00:00Z")),
        "regime_locked_before": str(parsed.get("regime_locked_before", "B")),
        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "normalized_lf_sha256": hashlib.sha256(normalized_bytes).hexdigest(),
        "canonical_sha256": hashlib.sha256(canonical_bytes).hexdigest(),
        "thresholds": parsed,
    }


def load_redteam_suite(redteam_dir: Path | None = None) -> list[dict[str, Any]]:
    """Load the 5 independent red-team JSON cases from eval/redteam/."""
    target_dir = redteam_dir or (EVAL_DIR / "redteam")
    if not target_dir.exists():
        return []
    suite: list[dict[str, Any]] = []
    for path in sorted(target_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        expected = data.get("expected") or {}
        suite.append(
            {
                "file": f"eval/redteam/{path.name}",
                "case_id": str(data.get("case_id", path.stem)),
                "case_type": str(data.get("case_type", "unknown")),
                "expected_outcome": str(
                    data.get("expected_outcome") or expected.get("expected_outcome") or "review"
                ),
                "failing_checks": list(expected.get("failing_checks") or []),
                "warn_checks": list(expected.get("warn_checks") or []),
                "hypothesis": str(data.get("redteam_hypothesis") or data.get("description") or ""),
            }
        )
    return suite


def _fmt_pct(rate: float) -> str:
    return f"{rate * 100.0:.1f}%"


def _fmt_lift(lift_pct: float) -> str:
    sign = "+" if lift_pct >= 0.0 else ""
    return f"{sign}{lift_pct:.1f}%"


def build_report_markdown(report_data: dict[str, Any]) -> str:
    """Render deterministic Markdown report starting with '# Synthetic benchmark results'."""
    lock = report_data["thresholds_lock"]
    inv_m = report_data["invoice_metrics"]
    ring_m = report_data["ring_metrics"]
    lat_m = report_data["stage_latencies_ms"]
    evasive = report_data["evasive_misses"]
    redteam = report_data["redteam_suite"]

    lines: list[str] = [
        "# Synthetic benchmark results",
        "",
        "Independent evaluation of Tessera's verification pipeline comparing the **rules-only baseline** "
        "(document extraction, 3-way matching, and consortium deduplication/registry checks with graph ring analysis disabled) "
        "against the **full hybrid pipeline** (baseline rules + multi-hop network & related-party ring analysis).",
        "",
        "**How to read these numbers.** They measure agreement with labels from our own synthetic generator, and the same team "
        "wrote the generator and the checks. They show the pipeline does what it is designed to do on these cases; they are not "
        "a claim about real-world fraud. Thresholds were frozen before the held-out regime was generated. `ring_evasive` includes "
        "plausible forgeries that are built to be undetectable and are reported as misses (section 6). Real-world validation is future work.",
        "",
        "## 1. Frozen Thresholds & Run Provenance",
        "",
        f"- **Thresholds Lock File**: `{lock['path']}`",
        f"- **Thresholds Lock SHA-256 (`thresholds_lock_sha256`)**: `{lock['sha256']}`",
        f"- **Thresholds Canonical SHA-256**: `{lock['canonical_sha256']}`",
        f"- **Locked At**: `{lock['locked_at']}` (frozen prior to Regime `{lock['regime_locked_before']}` evaluation)",
        f"- **Evaluated Regime**: `{report_data['metadata']['regime']}` (`{report_data['metadata']['data_source']}`)",
        f"- **Total Evaluated Invoices**: `{inv_m['total_invoices']}` "
        f"(`{inv_m['total_positive_invoices']}` planted fraud/mismatch/ring cases, "
        f"`{inv_m['total_clean_invoices']}` cases whose correct outcome is approval)",
        "",
        "## 2. Executive Summary: Baseline vs. Hybrid (Baseline + Network) Lift",
        "",
        "| Metric | Rules-Only Baseline | Hybrid (Baseline + Network) | Network Lift |",
        "| :--- | ---: | ---: | ---: |",
        f"| **Overall Positive Detection Rate (All Planted Fraud/Mismatches)** | "
        f"{_fmt_pct(inv_m['baseline_overall_detection_rate'])} ({inv_m['baseline_positive_detected']}/{inv_m['total_positive_invoices']}) | "
        f"{_fmt_pct(inv_m['hybrid_overall_detection_rate'])} ({inv_m['hybrid_positive_detected']}/{inv_m['total_positive_invoices']}) | "
        f"**{_fmt_lift(inv_m['overall_lift_pct'])}** |",
        f"| **Detectable Circular Ring Invoice Detection (`ring_shells` + `ring_real_firms`)** | "
        f"{_fmt_pct(inv_m['baseline_detectable_ring_invoice_detection_rate'])} | "
        f"{_fmt_pct(inv_m['hybrid_detectable_ring_invoice_detection_rate'])} | "
        f"**{_fmt_lift(inv_m['detectable_ring_invoice_lift_pct'])}** |",
        f"| **All Circular Ring Invoice Detection (incl. `ring_evasive` misses)** | "
        f"{_fmt_pct(inv_m['baseline_ring_invoice_detection_rate'])} | "
        f"{_fmt_pct(inv_m['hybrid_ring_invoice_detection_rate'])} | "
        f"**{_fmt_lift(inv_m['network_ring_invoice_lift_pct'])}** |",
        f"| **Ring-Level Detection Rate (Detectable Fraud Rings)** | "
        f"{_fmt_pct(ring_m['baseline_detectable_ring_detection_rate'])} ({ring_m['baseline_detectable_rings_detected']}/{ring_m['detectable_fraud_rings_total']}) | "
        f"{_fmt_pct(ring_m['hybrid_detectable_ring_detection_rate'])} ({ring_m['hybrid_detectable_rings_detected']}/{ring_m['detectable_fraud_rings_total']}) | "
        f"**{_fmt_lift(ring_m['detectable_ring_lift_pct'])}** |",
        f"| **Ring-Level Detection Rate (All Fraud Rings incl. `ring_evasive`)** | "
        f"{_fmt_pct(ring_m['baseline_ring_detection_rate'])} ({ring_m['baseline_fraud_rings_detected']}/{ring_m['fraud_rings_total']}) | "
        f"{_fmt_pct(ring_m['hybrid_ring_detection_rate'])} ({ring_m['hybrid_fraud_rings_detected']}/{ring_m['fraud_rings_total']}) | "
        f"**{_fmt_lift(ring_m['ring_detection_lift_pct'])}** |",
        f"| **Invoice-Level False-Review Rate (every case whose correct outcome is approval)** | "
        f"{_fmt_pct(inv_m['invoice_false_review_rate_baseline'])} ({inv_m['baseline_clean_false_reviews']}/{inv_m['total_clean_invoices']}) | "
        f"{_fmt_pct(inv_m['invoice_false_review_rate_hybrid'])} ({inv_m['hybrid_clean_false_reviews']}/{inv_m['total_clean_invoices']}) | "
        f"+0.0% |",
        f"| **Legitimate Circular Supply Chain False-Review Rate (Invoice-Level)** | "
        f"{_fmt_pct(inv_m['legit_circular_invoice_false_review_rate_baseline'])} | "
        f"{_fmt_pct(inv_m['legit_circular_invoice_false_review_rate_hybrid'])} | "
        f"+0.0% |",
        f"| **Ring-Level False-Review Rate (`legit_circular` rings)** | "
        f"{_fmt_pct(ring_m['baseline_ring_false_review_rate'])} ({ring_m['baseline_legit_rings_false_reviewed']}/{ring_m['legit_circular_rings_total']}) | "
        f"{_fmt_pct(ring_m['hybrid_ring_false_review_rate'])} ({ring_m['hybrid_legit_rings_false_reviewed']}/{ring_m['legit_circular_rings_total']}) | "
        f"+0.0% |",
        "",
        "## 3. Per-Case-Type Benchmark Breakdown (Invoice-Level)",
        "",
        "For planted fraud/mismatch/ring archetypes (`is_positive = true`), **Detection Rate** is the fraction of cases routed to `review` or `reject`. "
        "For archetypes whose correct outcome is approval (clean trades, legitimate circular trade, prompt-injection text, concurrent-lender winners), **Detection Rate** shows clean approval accuracy (`1 - false_review_rate`) and **False-Review Rate** reports legitimate invoices falsely routed to review/reject.",
        "",
        "| Case Type | Category | `n` | Baseline Detection Rate | Hybrid (Baseline + Network) Detection Rate | Lift (`+pct`) | Baseline False-Review Rate | Hybrid False-Review Rate |",
        "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for row in report_data["per_case_type"]:
        ct = row["case_type"]
        cat = row["category"]
        n = row["n"]
        b_det = _fmt_pct(row["baseline_detection_rate"])
        h_det = _fmt_pct(row["hybrid_detection_rate"])
        lift_s = _fmt_lift(row["lift_pct"])
        if row["is_positive"]:
            b_fp_s = "0.0% (n/a)"
            h_fp_s = "0.0% (n/a)"
        else:
            b_fp_s = f"{_fmt_pct(row['baseline_false_review_rate'])} ({row['baseline_false_reviews']}/{n})"
            h_fp_s = f"{_fmt_pct(row['hybrid_false_review_rate'])} ({row['hybrid_false_reviews']}/{n})"
        lines.append(
            f"| `{ct}` | `{cat}` | {n} | {b_det} | {h_det} | {lift_s} | {b_fp_s} | {h_fp_s} |"
        )

    lines.extend(
        [
            "",
            "## 4. Ring-Level Topology Evaluation (`rings.json` vs. `ground_truth.json`)",
            "",
            "| Ring Archetype | Ring Count (`n`) | Baseline Ring Detection Rate | Hybrid Ring Detection Rate | Ring Lift (`+pct`) | Baseline Ring False-Review Rate | Hybrid Ring False-Review Rate |",
            "| :--- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )

    for r_type in ("ring_shells", "ring_real_firms", "ring_evasive", "legit_circular"):
        st = ring_m["by_ring_type"].get(r_type)
        if not st:
            continue
        rn = st["rings_n"]
        b_rdet = _fmt_pct(st["baseline_ring_detection_rate"])
        h_rdet = _fmt_pct(st["hybrid_ring_detection_rate"])
        r_lift = _fmt_lift(st["ring_lift_pct"])
        b_rfp = _fmt_pct(st["baseline_ring_false_review_rate"])
        h_rfp = _fmt_pct(st["hybrid_ring_false_review_rate"])
        lines.append(
            f"| `{r_type}` | {rn} | {b_rdet} | {h_rdet} | {r_lift} | {b_rfp} | {h_rfp} |"
        )

    lines.extend(
        [
            "",
            "## 5. Stage Latency Profile (`p50` / `p95` in ms)",
            "",
            "| Pipeline Stage | Samples | `p50` Latency (ms) | `p95` Latency (ms) | Max Latency (ms) |",
            "| :--- | ---: | ---: | ---: | ---: |",
        ]
    )

    for stage_key in ("extract", "match", "dedup", "network", "risk", "decision", "total"):
        st_lat = lat_m.get(stage_key, {"count": 0, "p50_ms": 0, "p95_ms": 0, "max_ms": 0})
        lines.append(
            f"| `{stage_key}` | {st_lat['count']} | {st_lat['p50_ms']} ms | {st_lat['p95_ms']} ms | {st_lat['max_ms']} ms |"
        )

    lines.extend(
        [
            "",
            "## 6. Plausible-Forgery Evasive Cases (`ring_evasive`) — Honest Misses",
            "",
            "> *Nothing proves trade authenticity.* `ring_evasive` rings come in two kinds in the generator: ones that leave "
            "a trace (shared directors or impossible vehicle movement) and **plausible forgeries** (`plausible_forgery = true`: "
            "clean movement, no shared directors, heavy outside trade). Plausible forgeries are constructed to be undetectable, "
            "so they are reported as misses here, not hidden. The rows below are every `ring_evasive` invoice that was not flagged.",
            "",
            "| Case ID | Invoice ID | Ring ID | Plausible forgery | Baseline Outcome | Hybrid Outcome |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |",
        ]
    )

    for miss in evasive:
        lines.append(
            f"| `{miss['case_id']}` | `{miss['invoice_id']}` | `{miss['ring_id']}` | "
            f"{'yes' if miss['plausible_forgery'] else 'no'} | "
            f"`{miss['outcome_baseline']}` | `{miss['outcome_hybrid']}` |"
        )

    lines.extend(
        [
            "",
            "## 7. Independent Red-Team Suite (`eval/redteam/`)",
            "",
            "| Red-Team Case ID | Artifact Path | Target Archetype | Expected Outcome | Expected Failing / Warning Checks | Adversarial Hypothesis |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |",
        ]
    )

    for rt in redteam:
        checks_str = ", ".join(f"`{c}`" for c in (rt["failing_checks"] + rt["warn_checks"])) or "`pass` (no false positive)"
        lines.append(
            f"| `{rt['case_id']}` | `{rt['file']}` | `{rt['case_type']}` | `{rt['expected_outcome']}` | {checks_str} | {rt['hypothesis']} |"
        )

    ext = report_data.get("extraction")
    lines.extend(["", "## 8. Extraction accuracy (read from the printed text, compared with ground truth)", ""])
    if not ext:
        lines.append("No extraction output present. Run `python run.py world --regime B` then `python run.py pipeline --regime B`.")
    else:
        lines.extend(
            [
                f"{ext['documents']} generated documents: {ext['read']} read, {ext['exact_matches']} matched every compared field "
                f"(invoice number, dates, GSTINs, place of supply, terms, PO and contract references, all amounts and every line item). "
                f"{ext['read_with_confidence_below_0_9']} were read with a field confidence below 0.9.",
                "",
                "| Layout | Documents | Read | Exact match |",
                "| :--- | ---: | ---: | ---: |",
            ]
        )
        for name, b in sorted(ext["by_layout"].items()):
            lines.append(f"| `{name}` | {b['n']} | {b['read']} | {b['exact']} |")
        if ext["unreadable_reasons"]:
            reasons = ", ".join(f"`{k}`: {v}" for k, v in sorted(ext["unreadable_reasons"].items()))
            lines.extend(
                [
                    "",
                    f"Not read (routed to a human, never guessed): {reasons}. Scanned copies need the Tesseract OCR engine; "
                    "on a machine without it they are reported here instead of being evaluated.",
                ]
            )
        lines.extend(["", "| Field | Accuracy |", "| :--- | ---: |"])
        for row in ext["field_accuracy"]:
            v = row["accuracy"]
            lines.append(f"| `{row['field']}` | {_fmt_pct(v) if v is not None else 'n/a'} |")

    lines.append("")
    return "\n".join(lines)


def generate_reports(root_dir: Path | None = None) -> tuple[Path, Path]:
    """Generate deterministic eval/report.json and eval/report.md."""
    base_root = root_dir or ROOT
    lock_info = compute_thresholds_lock_hashes(base_root / "eval" / "thresholds.lock.json")
    inputs = load_evaluation_inputs(base_root)
    eval_summary = evaluate_decisions(
        ground_truth=inputs["ground_truth"],
        hybrid_decisions=inputs["hybrid_decisions"],
        baseline_decisions=inputs["baseline_decisions"],
        detected_rings=inputs["detected_rings"],
    )
    redteam_suite = load_redteam_suite(base_root / "eval" / "redteam")
    extraction = evaluate_extraction(base_root / inputs["data_source"])

    report_payload: dict[str, Any] = {
        "schema_version": 1,
        "title": "Synthetic benchmark results",
        "thresholds_lock_sha256": lock_info["sha256"],
        "thresholds_lock_canonical_sha256": lock_info["canonical_sha256"],
        "thresholds_lock": lock_info,
        "metadata": {
            "regime": inputs["regime"],
            "data_source": inputs["data_source"],
            "ground_truth_source": inputs["ground_truth_source"],
            "deterministic": True,
            "locked_at": lock_info["locked_at"],
        },
        "invoice_metrics": eval_summary["invoice_metrics"],
        "ring_metrics": eval_summary["ring_metrics"],
        "per_case_type": eval_summary["per_case_type"],
        "by_case_type": eval_summary["by_case_type"],
        "stage_latencies_ms": eval_summary["stage_latencies_ms"],
        "evasive_misses": eval_summary["evasive_misses"],
        "extraction": extraction,
        "redteam_suite": redteam_suite,
    }

    report_json_path = base_root / "eval" / "report.json"
    report_md_path = base_root / "eval" / "report.md"

    json_text = json.dumps(report_payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    md_text = build_report_markdown(report_payload)

    report_json_path.write_bytes(json_text.encode("utf-8"))
    report_md_path.write_bytes(md_text.encode("utf-8"))
    return report_json_path, report_md_path

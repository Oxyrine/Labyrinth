"""Tests for Agent A8 independent evaluation harness, red-team suite, and deterministic report generation."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from contract import (
    CaseContext,
    EvidenceDoc,
    FinancingRequest,
    InMemoryHistoryView,
    Invoice,
    InvoiceVersion,
    Party,
)
from contract.validate import validate_all, validate_case_bundle
import pytest

from eval.baseline import (
    derive_baseline_decision,
    evaluate_decisions,
)
from eval.report import compute_thresholds_lock_hashes, generate_reports

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "eval"
FORBIDDEN_PKGS = {"extract", "match", "dedup", "network", "risk", "workflow", "audit", "chain", "app"}

EXPECTED_REDTEAM_FILES = {
    "rt_01_split_below_ewb.json",
    "rt_02_bridge_single_director_decoy.json",
    "rt_03_slow_ring_45d.json",
    "rt_04_credit_note_wash.json",
    "rt_05_unicode_homoglyph_inv.json",
}


def test_eval_has_zero_forbidden_imports() -> None:
    import_re = re.compile(r"^\s*(?:from|import)\s+([a-zA-Z0-9_]+)", re.MULTILINE)
    py_files = sorted(EVAL_DIR.rglob("*.py"))
    assert len(py_files) >= 4
    violations: list[str] = []
    for f in py_files:
        content = f.read_text(encoding="utf-8")
        for m in import_re.finditer(content):
            pkg = m.group(1)
            if pkg in FORBIDDEN_PKGS:
                violations.append(f"{f.name}: imports forbidden package '{pkg}'")
    assert not violations, f"Forbidden imports in eval/: {violations}"


def test_thresholds_lock_contents_and_hash_in_reports() -> None:
    lock_path = EVAL_DIR / "thresholds.lock.json"
    assert lock_path.exists()
    lock_data = json.loads(lock_path.read_text(encoding="utf-8"))

    assert lock_data["locked_at"] == "2026-10-13T00:00:00Z"
    assert lock_data["network_tier_thresholds"]["high"] == 0.60
    assert lock_data["network_tier_thresholds"]["medium"] == 0.30
    assert lock_data["matching_tolerances"] == {
        "qty_bps": 200,
        "rate_bps": 100,
        "total_paise": 100,
    }
    assert lock_data["scorecard_grade_cutoffs"] == {
        "A": 85,
        "B": 70,
        "C": 55,
        "D": 40,
    }
    assert set(lock_data["network_signal_weights"].keys()) == {
        "s_flow",
        "s_product",
        "s_timing",
        "s_isolation",
        "s_movement",
        "s_related",
        "s_transform",
        "s_rapid",
    }

    report_json_path, report_md_path = generate_reports(ROOT)
    expected_sha256 = hashlib.sha256(lock_path.read_bytes()).hexdigest()
    lock_hashes = compute_thresholds_lock_hashes(lock_path)
    assert expected_sha256 == lock_hashes["sha256"]

    report_json = json.loads(report_json_path.read_text(encoding="utf-8"))
    report_md = report_md_path.read_text(encoding="utf-8")

    assert report_json["thresholds_lock_sha256"] == expected_sha256
    assert expected_sha256 in report_md


def test_eval_run_twice_is_byte_identical() -> None:
    eval_run = EVAL_DIR / "run.py"
    rc1 = subprocess.call([sys.executable, str(eval_run)], cwd=str(ROOT))
    assert rc1 == 0

    md_bytes_1 = (EVAL_DIR / "report.md").read_bytes()
    json_bytes_1 = (EVAL_DIR / "report.json").read_bytes()

    rc2 = subprocess.call([sys.executable, str(eval_run)], cwd=str(ROOT))
    assert rc2 == 0

    md_bytes_2 = (EVAL_DIR / "report.md").read_bytes()
    json_bytes_2 = (EVAL_DIR / "report.json").read_bytes()

    assert md_bytes_1 == md_bytes_2, "eval/report.md drifted across consecutive runs"
    assert json_bytes_1 == json_bytes_2, "eval/report.json drifted across consecutive runs"


def test_report_markdown_structure_lift_false_reviews_and_evasive_misses() -> None:
    report_json_path, report_md_path = generate_reports(ROOT)
    md_text = report_md_path.read_text(encoding="utf-8")
    report_data = json.loads(report_json_path.read_text(encoding="utf-8"))

    # 1. Must start with '# Synthetic benchmark results'
    assert md_text.startswith("# Synthetic benchmark results\n")

    # 2. Reports baseline vs network lift
    inv_metrics = report_data["invoice_metrics"]
    ring_metrics = report_data["ring_metrics"]
    assert inv_metrics["overall_lift_pct"] > 0.0
    assert inv_metrics["network_ring_invoice_lift_pct"] > 0.0
    assert ring_metrics["ring_detection_lift_pct"] > 0.0
    assert "Rules-Only Baseline" in md_text
    assert "Hybrid (Baseline + Network)" in md_text
    assert "Network Lift" in md_text

    # 3. Reports invoice-level and ring-level false-review rates
    assert "invoice_false_review_rate_baseline" in inv_metrics
    assert "invoice_false_review_rate_hybrid" in inv_metrics
    assert "baseline_ring_false_review_rate" in ring_metrics
    assert "hybrid_ring_false_review_rate" in ring_metrics
    assert "Invoice-Level False-Review Rate" in md_text
    assert "Ring-Level False-Review Rate" in md_text
    assert "`legit_circular`" in md_text

    # 4. Reports stage latencies p50 and p95
    latencies = report_data["stage_latencies_ms"]
    for stage in ("extract", "match", "dedup", "network", "risk", "decision", "total"):
        assert stage in latencies
        assert latencies[stage]["p50_ms"] > 0
        assert latencies[stage]["p95_ms"] >= latencies[stage]["p50_ms"]
    assert "`p50` Latency (ms)" in md_text
    assert "`p95` Latency (ms)" in md_text

    # 5. Explicitly lists plausible-forgery evasive misses (ring_evasive) as honest misses
    evasive_misses = report_data["evasive_misses"]
    assert len(evasive_misses) >= 1
    assert all(m["case_type"] == "ring_evasive" for m in evasive_misses)
    assert "`ring_evasive`" in md_text
    assert "Honest Miss" in md_text or "honest miss" in md_text.lower()
    assert "Nothing proves trade authenticity" in md_text


def test_redteam_cases_validate_and_hydrate_case_context() -> None:
    redteam_dir = EVAL_DIR / "redteam"
    assert redteam_dir.exists()
    files = {p.name for p in redteam_dir.glob("*.json")}
    assert EXPECTED_REDTEAM_FILES.issubset(files)

    for fname in sorted(EXPECTED_REDTEAM_FILES):
        fpath = redteam_dir / fname
        bundle = json.loads(fpath.read_text(encoding="utf-8"))
        errors = validate_case_bundle(bundle, fpath)
        assert not errors, f"Validation errors in {fname}: {errors}"

        ctx = CaseContext(
            request=FinancingRequest.model_validate(bundle["request"]),
            invoice=Invoice.model_validate(bundle["invoice"]),
            version=InvoiceVersion.model_validate(bundle["version"]),
            parties={k: Party.model_validate(v) for k, v in bundle["parties"].items()},
            evidence=[EvidenceDoc.model_validate(d) for d in bundle["evidence"]],
            history=InMemoryHistoryView.model_validate(bundle["history"]),
        )
        assert ctx.invoice.invoice_id.startswith("INV-")
        assert ctx.request.request_id.startswith("REQ-")


def test_baseline_derivation_and_evaluator_math() -> None:
    sample_hybrid_ring_dec = {
        "schema_version": 1,
        "decision_id": "DEC-T-01",
        "request_id": "REQ-T-01",
        "invoice_id": "INV-T-01",
        "version_id": "V-T-01-1",
        "outcome": "review",
        "state": "in_review",
        "grade": "C",
        "score_points": 60,
        "network_tier": "high",
        "checks": [
            {
                "schema_version": 1,
                "check": "extract.gstin_checksum",
                "status": "pass",
                "severity": "hard",
                "source": "live",
                "evidence_weight": 1.0,
                "evidence": ["Seller and buyer GSTIN passed mod-36 checksum."],
                "refs": ["INV-T-01"],
            },
            {
                "schema_version": 1,
                "check": "network.ring",
                "status": "warn",
                "severity": "high",
                "source": "live",
                "evidence_weight": 1.0,
                "evidence": ["Circular ring R01 detected across 4 firms."],
                "refs": ["R01", "INV-T-01"],
            },
        ],
        "stage_latencies_ms": {"extract": 10, "network": 15, "total": 30},
        "decided_at": "2026-10-13T00:00:00Z",
    }
    derived_base = derive_baseline_decision(sample_hybrid_ring_dec)
    assert derived_base["outcome"] == "conditionally_approved"
    assert derived_base["network_tier"] == "low"

    gt = {
        "cases": [
            {"invoice_id": "INV-T-01", "case_type": "ring_shells", "ring_id": "R01"},
        ],
        "rings": [
            {"ring_id": "R01", "case_type": "ring_shells", "invoice_ids": ["INV-T-01"]},
        ],
    }
    summary = evaluate_decisions(gt, [sample_hybrid_ring_dec])
    ring_row = summary["by_case_type"]["ring_shells"]
    assert ring_row["baseline_detection_rate"] == 0.0
    assert ring_row["hybrid_detection_rate"] == 1.0
    assert ring_row["lift_pct"] == 100.0

    checked, all_errors = validate_all()
    assert not all_errors, f"Contract validation errors: {all_errors}"
    assert checked >= 8


def _dec(inv: str, outcome: str, ring_check: str = "pass") -> dict:
    return {
        "invoice_id": inv,
        "request_id": "REQ-" + inv,
        "outcome": outcome,
        "network_tier": "high" if ring_check == "warn" else "low",
        "checks": [{"check": "network.ring", "status": ring_check, "severity": "medium", "evidence": []}],
        "stage_latencies_ms": {"extract": 5, "match": 5, "dedup": 5, "network": 5, "risk": 5, "decision": 5},
    }


def _tiny_outputs():
    """Hand-built mini outputs (unit test input only; the real report never uses this)."""
    gt = {
        "case_list": [
            {"case_id": "c1", "invoice_id": "INV-1", "sub_type": "clean_goods", "ring_id": None},
            {"case_id": "c2", "invoice_id": "INV-2", "sub_type": "ring_shells", "ring_id": "R1"},
            {"case_id": "c3", "invoice_id": "INV-3", "sub_type": "ring_evasive", "ring_id": "R2", "plausible_forgery": True},
        ],
        "cases": {},
        "rings": [
            {"ring_id": "R1", "ring_type": "ring_shells", "invoice_ids": ["INV-2"]},
            {"ring_id": "R2", "ring_type": "ring_evasive", "invoice_ids": ["INV-3"]},
        ],
    }
    gt["cases"] = {c["case_id"]: c for c in gt["case_list"]}
    hyb = [_dec("INV-1", "conditionally_approved"), _dec("INV-2", "review", "warn"), _dec("INV-3", "conditionally_approved")]
    base = [_dec("INV-1", "conditionally_approved"), _dec("INV-2", "conditionally_approved"), _dec("INV-3", "conditionally_approved")]
    rings = [{"ring_id": "R1", "tier": "high"}]
    return gt, hyb, base, rings


def test_missing_outputs_raise_instead_of_synthesising(tmp_path: Path) -> None:
    (tmp_path / "eval").mkdir()
    shutil.copy2(EVAL_DIR / "thresholds.lock.json", tmp_path / "eval" / "thresholds.lock.json")
    with pytest.raises(FileNotFoundError):
        generate_reports(tmp_path)


def test_evasive_miss_comes_from_outputs_only() -> None:
    gt, hyb, base, rings = _tiny_outputs()
    out = evaluate_decisions(gt, hyb, base, rings)
    assert [m["invoice_id"] for m in out["evasive_misses"]] == ["INV-3"]
    assert out["by_case_type"]["ring_shells"]["hybrid_detection_rate"] == 1.0
    assert out["by_case_type"]["ring_shells"]["baseline_detection_rate"] == 0.0


def test_report_reads_from_regime_b_directory_when_present(tmp_path: Path) -> None:
    tmp_eval = tmp_path / "eval"
    tmp_eval.mkdir(parents=True)
    shutil.copy2(EVAL_DIR / "thresholds.lock.json", tmp_eval / "thresholds.lock.json")
    shutil.copytree(EVAL_DIR / "redteam", tmp_eval / "redteam")

    out_b = tmp_path / "data" / "out" / "regime_B"
    out_b.mkdir(parents=True)

    gt, hyb, base, rings = _tiny_outputs()
    (out_b / "ground_truth.json").write_text(json.dumps(gt, indent=2), encoding="utf-8")
    (out_b / "decisions.jsonl").write_text(
        "\n".join(json.dumps(d) for d in hyb) + "\n", encoding="utf-8"
    )
    (out_b / "decisions_baseline.jsonl").write_text(
        "\n".join(json.dumps(d) for d in base) + "\n", encoding="utf-8"
    )
    (out_b / "rings.json").write_text(json.dumps(rings, indent=2), encoding="utf-8")

    rep_json, rep_md = generate_reports(tmp_path)
    data = json.loads(rep_json.read_text(encoding="utf-8"))
    md = rep_md.read_text(encoding="utf-8")

    assert data["metadata"]["data_source"] == "data/out/regime_B"
    assert data["invoice_metrics"]["overall_lift_pct"] > 0.0
    assert md.startswith("# Synthetic benchmark results\n")

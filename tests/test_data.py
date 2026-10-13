"""Comprehensive tests for Labyrinth synthetic economy generator (data/) and hand-written fixtures (fixtures/)."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pypdf
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from contract import (
    CaseContext,
    EvidenceDoc,
    FinancingRequest,
    InMemoryHistoryView,
    Invoice,
    InvoiceVersion,
    Party,
    verify_gstin_checksum,
)
from contract.validate import validate_all, validate_case_bundle
from data.generate import generate_regime
from data.generator.cities import CITIES, CITY_BY_PINCODE, haversine_km, road_distance_km
from data.generator.pdfs import LAYOUTS, render_invoice_pdf_bytes, rs
from data.generator.scans import render_noisy_scan_pdf_bytes, render_noisy_scan_png_bytes
from data.generator.world import derive_party_keypair, generate_world, sign_with_party_key

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "fixtures"
DATA_OUT_DIR = ROOT / "data" / "out"

REQUIRED_FIXTURES: dict[str, tuple[str, list[str], list[str]]] = {
    "clean_goods": ("conditionally_approved", [], []),
    "clean_services": ("conditionally_approved", [], []),
    "bad_arith": ("reject", ["extract.arith_total"], []),
    "exact_duplicate": ("reject", ["dedup.registry"], []),
    "edited_resubmission": ("review", [], ["dedup.identity"]),
    "altered_amount_same_identity": ("review", [], ["dedup.identity"]),
    "double_financing_cross_lender": ("reject", ["dedup.registry"], []),
    "concurrent_reservation": ("conditionally_approved", [], []),
    "no_delivery": ("reject", ["match.three_way.grn_presence"], []),
    "above_outstanding": ("reject", ["dedup.registry"], []),
    "refinance_settled": ("reject", ["dedup.registry"], []),
    "ring_shells": ("review", [], ["network.ring"]),
    "ring_real_firms": ("review", [], ["network.ring"]),
    "ring_evasive": ("review", [], ["network.ring"]),
    "legit_circular": ("conditionally_approved", [], []),
    "docs_unavailable": ("review", [], []),
    "noisy_scan": ("conditionally_approved", [], []),
    "near_dup_INV-O042": ("review", [], ["dedup.near_dup"]),
    "prompt_injection": ("conditionally_approved", [], []),
    "mm_qty": ("reject", ["match.three_way.qty"], []),
    "mm_rate": ("reject", ["match.three_way.rate"], []),
    "mm_grn_missing": ("reject", ["match.three_way.grn_presence"], []),
    "mm_date_order": ("reject", ["match.ordering.dates"], []),
    "mm_over_invoice": ("reject", ["match.over_invoicing"], []),
    "mm_ewb_missing": ("reject", ["match.ewaybill"], []),
}


def test_cities_catalog_and_distances() -> None:
    assert len(CITIES) == 60
    assert "410501" in CITY_BY_PINCODE
    assert "412220" in CITY_BY_PINCODE
    assert "600001" in CITY_BY_PINCODE
    assert "110001" in CITY_BY_PINCODE
    assert haversine_km("410501", "410501") == 0.0
    pune_to_chennai_hav = haversine_km("410501", "600001")
    pune_to_chennai_road = road_distance_km("410501", "600001")
    assert 850.0 <= pune_to_chennai_hav <= 980.0
    assert round(pune_to_chennai_hav * 1.3, 2) == pune_to_chennai_road


def test_world_gstin_checksums_aliases_and_hkdf_keys() -> None:
    w1 = generate_world(seed=2601, regime="A")
    w2 = generate_world(seed=2601, regime="A")
    assert len(w1.firms) == 200
    assert len(w1.lenders) == 3
    assert len(w1.alias_pairs) >= 15
    assert len(w1.director_clusters) >= 10

    for party in w1.parties:
        assert verify_gstin_checksum(party.gstin), f"Invalid GSTIN checksum for {party.party_id}: {party.gstin}"
        assert party.public_key_hex is not None and len(party.public_key_hex) == 64

    # Deterministic HKDF key derivation & Ed25519 verification
    priv1, pub1 = derive_party_keypair(2601, "P-0001")
    priv2, pub2 = derive_party_keypair(2601, "P-0001")
    assert (priv1, pub1) == (priv2, pub2)
    msg = b"labyrinth-signature-test"
    sig_hex = sign_with_party_key(2601, "P-0001", msg)
    pub_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub1))
    pub_key.verify(bytes.fromhex(sig_hex), msg)

    # Byte-identical world serialization
    assert [p.model_dump(mode="json") for p in w1.parties] == [p.model_dump(mode="json") for p in w2.parties]


def test_all_25_hand_written_fixtures_and_schema_validation() -> None:
    checked, errors = validate_all()
    assert errors == [], f"Schema validation errors: {errors}"
    assert checked >= 50

    for case_id, (exp_outcome, exp_fails, exp_warns) in REQUIRED_FIXTURES.items():
        case_dir = FIXTURES_DIR / case_id
        assert case_dir.is_dir(), f"Missing fixture folder: {case_dir}"
        case_file = case_dir / "case.json"
        expected_file = case_dir / "expected.json"
        assert case_file.is_file(), f"Missing case.json in {case_dir}"
        assert expected_file.is_file(), f"Missing expected.json in {case_dir}"

        bundle = json.loads(case_file.read_text(encoding="utf-8"))
        bundle_errors = validate_case_bundle(bundle, case_file)
        assert bundle_errors == []

        expected = json.loads(expected_file.read_text(encoding="utf-8"))
        assert expected["case_id"] == case_id
        assert expected["expected_outcome"] == exp_outcome
        assert expected["failing_checks"] == exp_fails
        assert expected["warn_checks"] == exp_warns

        # Verify full instantiation into CaseContext & InMemoryHistoryView
        ctx = CaseContext(
            request=FinancingRequest.model_validate(bundle["request"]),
            invoice=Invoice.model_validate(bundle["invoice"]),
            version=InvoiceVersion.model_validate(bundle["version"]),
            parties={pid: Party.model_validate(p) for pid, p in bundle["parties"].items()},
            evidence=[EvidenceDoc.model_validate(d) for d in bundle["evidence"]],
            history=InMemoryHistoryView.model_validate(bundle["history"]),
        )
        assert ctx.invoice.seller_id in ctx.parties
        assert ctx.invoice.buyer_id in ctx.parties

    # Check the 4 fixture PDFs exist and match version.file_sha256
    for pdf_case in ("clean_goods", "bad_arith", "noisy_scan", "prompt_injection"):
        pdf_path = FIXTURES_DIR / pdf_case / "invoice.pdf"
        assert pdf_path.is_file(), f"Missing fixture PDF: {pdf_path}"
        case_data = json.loads((FIXTURES_DIR / pdf_case / "case.json").read_text(encoding="utf-8"))
        actual_sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        assert case_data["version"]["file_sha256"] == actual_sha


def test_same_seed_byte_identical_json_outputs(tmp_path: Path) -> None:
    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    generate_regime(regime="A", seed=2601, json_only=True, out_root=out1)
    generate_regime(regime="A", seed=2601, json_only=True, out_root=out2)

    for fname in (
        "world.json",
        "invoices.json",
        "versions.json",
        "evidence_docs.json",
        "payments.json",
        "requests.json",
        "buyer_acceptances.json",
        "cases.json",
        "ground_truth.json",
        "keys_manifest.json",
    ):
        b1 = (out1 / "regime_A" / fname).read_bytes()
        b2 = (out2 / "regime_A" / fname).read_bytes()
        assert b1 == b2, f"Non-deterministic JSON output for {fname}"


def test_ground_truth_minimum_case_counts() -> None:
    gt_path = DATA_OUT_DIR / "regime_A" / "ground_truth.json"
    assert gt_path.is_file(), "Regime A ground_truth.json must exist in data/out/regime_A/"
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    counts = gt["counts"]

    # Minimum ring counts (>= 10 per ring type)
    assert counts["ring_shells"] >= 10
    assert counts["ring_real_firms"] >= 10
    assert counts["ring_evasive"] >= 10
    assert counts["ring_evasive_caught"] == 7
    assert counts["ring_evasive_plausible_forgery"] == 3

    # Minimum duplicate counts (>= 20 per duplicate type)
    assert counts["exact_duplicate"] >= 20
    assert counts["edited_resubmission"] >= 20
    assert counts["altered_amount_same_identity"] >= 20
    assert counts["double_financing_cross_lender"] >= 20
    assert counts["near_dup"] >= 20

    # Minimum legit circular (>= 30) and alias pairs (> 0, >= 15)
    assert counts["legit_circular"] >= 30
    assert counts["alias_pairs"] >= 15
    assert len(gt["alias_pairs"]) >= 15


def test_pdf_hashes_fast_and_single_invoice_determinism() -> None:
    hashes_path = DATA_OUT_DIR / "pdf_hashes.json"
    assert hashes_path.is_file(), "data/out/pdf_hashes.json must exist"
    recorded_hashes = json.loads(hashes_path.read_text(encoding="utf-8"))
    assert len(recorded_hashes) >= 50

    # Verify every file in regime_A/pdfs and regime_A/scans matches its recorded SHA-256
    for rel_path, expected_sha in recorded_hashes.items():
        if not rel_path.startswith("regime_A/"):
            continue
        file_path = DATA_OUT_DIR / rel_path
        assert file_path.is_file(), f"Missing generated PDF/scan file: {file_path}"
        actual_sha = hashlib.sha256(file_path.read_bytes()).hexdigest()
        assert actual_sha == expected_sha, f"Hash mismatch for {rel_path}"

    # Verify pypdf extracts machine-parsable fields cleanly across all 3 layouts + noisy scan
    clean_case = json.loads((FIXTURES_DIR / "clean_goods" / "case.json").read_text(encoding="utf-8"))
    inv = Invoice.model_validate(clean_case["invoice"])
    seller = Party.model_validate(clean_case["parties"][inv.seller_id])
    buyer = Party.model_validate(clean_case["parties"][inv.buyer_id])

    for layout in LAYOUTS:
        b1 = render_invoice_pdf_bytes(inv, seller, buyer, layout=layout)
        b2 = render_invoice_pdf_bytes(inv, seller, buyer, layout=layout)
        assert b1 == b2, f"PDF layout {layout} is not byte-identical across runs"
        text = pypdf.PdfReader(io.BytesIO(b1)).pages[0].extract_text()
        assert inv.invoice_no in text and inv.seller_gstin in text and inv.buyer_gstin in text
        assert rs(inv.grand_total_paise) in text
        # Printed invoices carry no internal ids and no machine-readable answer key.
        assert inv.invoice_id not in text and "LABYRINTH_INVOICE_JSON" not in text and "LINE_ITEM|" not in text

    scan1 = render_noisy_scan_pdf_bytes(inv, seller, buyer, seed=2601)
    scan2 = render_noisy_scan_pdf_bytes(inv, seller, buyer, seed=2601)
    assert scan1 == scan2
    png1 = render_noisy_scan_png_bytes(inv, seller, buyer, seed=2601)
    png2 = render_noisy_scan_png_bytes(inv, seller, buyer, seed=2601)
    assert png1 == png2


@pytest.mark.slow
def test_full_pdf_and_scan_rerender_byte_identical(tmp_path: Path) -> None:
    out1 = tmp_path / "pdf_run1"
    out2 = tmp_path / "pdf_run2"
    generate_regime(regime="A", seed=2601, json_only=False, out_root=out1)
    generate_regime(regime="A", seed=2601, json_only=False, out_root=out2)

    h1 = json.loads((out1 / "pdf_hashes.json").read_text(encoding="utf-8"))
    h2 = json.loads((out2 / "pdf_hashes.json").read_text(encoding="utf-8"))
    assert h1 == h2
    for rel_key in h1:
        assert (out1 / rel_key).read_bytes() == (out2 / rel_key).read_bytes()

"""CLI for deterministic synthetic economy generation: python data/generate.py --regime A|B --seed N [--json-only]."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.generator.docs import generate_legitimate_economy
from data.generator.fraud import inject_fraud_and_mismatches
from data.generator.pdfs import LAYOUTS, write_invoice_pdf
from data.generator.scans import render_noisy_scan_png_bytes, write_noisy_scan_pdf
from data.generator.world import generate_world, sign_with_party_key, write_world_keys
from contract.models import canonical_json

DEFAULT_SEEDS = {"A": 2601, "B": 2602}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


_SIG_FIELDS = ("seller_signature_hex", "signature_hex")


def _sig_for(seed: int, party_id: str, raw: dict[str, Any]) -> str:
    """Ed25519 signature over canonical JSON with signature fields stripped (what the registry verifies)."""
    body = {k: v for k, v in raw.items() if k not in _SIG_FIELDS}
    return sign_with_party_key(seed, party_id, canonical_json(body, allow_floats=True).encode("utf-8"))


def _resign_all(ds: Any, seed: int) -> None:
    """Fraud injectors edit requests after creation; re-sign every request/acceptance so signatures match content."""
    for i, req in enumerate(ds.requests):
        raw = req.model_dump(mode="json")
        ds.requests[i] = req.model_copy(update={"seller_signature_hex": _sig_for(seed, req.seller_id, raw)})
    for i, acc in enumerate(ds.buyer_acceptances):
        raw = acc.model_dump(mode="json")
        ds.buyer_acceptances[i] = acc.model_copy(update={"signature_hex": _sig_for(seed, acc.buyer_id, raw)})
    for case in ds.cases:
        rq = case.get("request")
        if isinstance(rq, dict) and rq.get("seller_id"):
            rq["seller_signature_hex"] = _sig_for(seed, rq["seller_id"], rq)


def generate_regime(
    regime: str = "A",
    seed: int | None = None,
    json_only: bool = False,
    out_root: Path | None = None,
) -> dict[str, Any]:
    """Generate a full synthetic economy regime deterministically and write artifacts to disk."""
    regime_norm = regime.strip().upper()
    if regime_norm not in ("A", "B"):
        raise ValueError(f"Unsupported regime {regime!r}; expected 'A' or 'B'")
    use_seed = seed if seed is not None else DEFAULT_SEEDS[regime_norm]
    base_out = out_root or (ROOT / "data" / "out")
    regime_dir = base_out / f"regime_{regime_norm}"
    keys_dir = base_out / "keys"
    regime_dir.mkdir(parents=True, exist_ok=True)

    world = generate_world(seed=use_seed, regime=regime_norm)
    write_world_keys(world, regime_dir=regime_dir, keys_root_dir=keys_dir)

    ds = generate_legitimate_economy(world)
    ground_truth = inject_fraud_and_mismatches(world, ds)
    _resign_all(ds, use_seed)

    world_payload = {
        "schema_version": 1,
        "regime": regime_norm,
        "seed": use_seed,
        "parties": [p.model_dump(mode="json") for p in world.parties],
        "parties_by_id": {p.party_id: p.model_dump(mode="json") for p in world.parties},
        "alias_pairs": world.alias_pairs,
        "director_clusters": world.director_clusters,
    }
    _write_json(regime_dir / "world.json", world_payload)
    _write_json(regime_dir / "invoices.json", [inv.model_dump(mode="json") for inv in ds.invoices])
    _write_json(regime_dir / "versions.json", [ver.model_dump(mode="json") for ver in ds.versions])
    _write_json(regime_dir / "evidence_docs.json", [doc.model_dump(mode="json") for doc in ds.evidence_docs])
    _write_json(regime_dir / "payments.json", [pay.model_dump(mode="json") for pay in ds.payments])
    _write_json(regime_dir / "requests.json", [req.model_dump(mode="json") for req in ds.requests])
    _write_json(
        regime_dir / "buyer_acceptances.json",
        [acc.model_dump(mode="json") for acc in ds.buyer_acceptances],
    )
    _write_json(regime_dir / "cases.json", ds.cases)
    _write_json(regime_dir / "ground_truth.json", ground_truth)

    pdf_hashes: dict[str, str] = {}
    if not json_only:
        pdfs_dir = regime_dir / "pdfs"
        scans_dir = regime_dir / "scans"
        pdfs_dir.mkdir(parents=True, exist_ok=True)
        scans_dir.mkdir(parents=True, exist_ok=True)

        # 30 PDFs: 10 per layout (tabular_gst, letterhead, tally)
        for layout_idx, layout in enumerate(LAYOUTS):
            for i in range(10):
                inv = ds.invoices[layout_idx * 10 + i]
                seller = world.parties_by_id[inv.seller_id]
                buyer = world.parties_by_id[inv.buyer_id]
                fname = f"{inv.invoice_id}_{layout}.pdf"
                out_pdf = pdfs_dir / fname
                sha = write_invoice_pdf(out_pdf, inv, seller, buyer, layout=layout)
                rel_key = f"regime_{regime_norm}/pdfs/{fname}"
                pdf_hashes[rel_key] = sha

        # 10 noisy rasterized scan PDFs + companion PNGs
        for i in range(10):
            inv = ds.invoices[30 + i]
            seller = world.parties_by_id[inv.seller_id]
            buyer = world.parties_by_id[inv.buyer_id]
            pdf_name = f"{inv.invoice_id}_scan.pdf"
            png_name = f"{inv.invoice_id}_scan.png"
            out_scan_pdf = scans_dir / pdf_name
            out_scan_png = scans_dir / png_name

            pdf_sha = write_noisy_scan_pdf(out_scan_pdf, inv, seller, buyer, seed=use_seed)
            png_bytes = render_noisy_scan_png_bytes(inv, seller, buyer, seed=use_seed)
            out_scan_png.write_bytes(png_bytes)
            png_sha = hashlib.sha256(png_bytes).hexdigest()

            pdf_hashes[f"regime_{regime_norm}/scans/{pdf_name}"] = pdf_sha
            pdf_hashes[f"regime_{regime_norm}/scans/{png_name}"] = png_sha

        _write_json(regime_dir / "pdf_hashes.json", pdf_hashes)

        # Merge into top-level data/out/pdf_hashes.json
        top_hashes_path = base_out / "pdf_hashes.json"
        merged_hashes: dict[str, str] = {}
        if top_hashes_path.exists():
            try:
                existing = json.loads(top_hashes_path.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    merged_hashes.update(existing)
            except Exception:
                pass
        merged_hashes.update(pdf_hashes)
        _write_json(top_hashes_path, merged_hashes)

    return {
        "regime": regime_norm,
        "seed": use_seed,
        "regime_dir": str(regime_dir),
        "counts": ground_truth["counts"],
        "pdf_count": len(pdf_hashes),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Labyrinth deterministic synthetic economy.")
    parser.add_argument("--regime", choices=["A", "B"], default="A", help="Economy regime (A or B)")
    parser.add_argument("--seed", type=int, default=None, help="Deterministic RNG seed (default: 2601 for A, 2602 for B)")
    parser.add_argument("--json-only", action="store_true", help="Skip PDF and scan rendering")
    parser.add_argument("--out-dir", type=str, default=None, help="Optional output root directory")
    args = parser.parse_args()

    out_root = Path(args.out_dir) if args.out_dir else None
    summary = generate_regime(regime=args.regime, seed=args.seed, json_only=args.json_only, out_root=out_root)
    print(
        f"Generated Regime {summary['regime']} (seed={summary['seed']}): "
        f"{summary['counts']['total_cases']} cases, {summary['pdf_count']} PDF/scan artifacts -> {summary['regime_dir']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

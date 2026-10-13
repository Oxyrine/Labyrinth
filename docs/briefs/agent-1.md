# Agent A1 Brief — Synthetic Economy Generator & Hand-Written Fixtures (`data/`, `fixtures/`)

## Ownership & Boundaries
- **Owned folders**: `fixtures/`, `data/`
- **Forbidden folders**: `extract/`, `match/`, `dedup/`, `network/`, `risk/`, `workflow/`, `audit/`, `chain/`, `app/`, `eval/`
- **Shared contract**: `contract/models.py`, `contract/context.py`, `contract/WIRE_PROTOCOL.md`
- **Clean-room rule**: Never read or reference any prior repo outside `labyrinth`.

## Deliverables
1. **23 Hand-Written Fixtures (`fixtures/<case_name>/`)**:
   Each fixture folder contains `case.json` (validating against `contract/validate.py`) and `expected.json` (`expected_outcome`, `failing_checks`, `warn_checks`):
   - `clean_goods`, `clean_services`, `bad_arith`, `exact_duplicate`, `edited_resubmission`, `altered_amount_same_identity`, `double_financing_cross_lender`, `concurrent_reservation`, `no_delivery`, `above_outstanding`, `refinance_settled`, `ring_shells`, `ring_real_firms`, `ring_evasive`, `legit_circular`, `docs_unavailable`, `noisy_scan`, `near_dup_INV-O042`, `prompt_injection`, `mm_qty`, `mm_rate`, `mm_grn_missing`, `mm_date_order`, `mm_over_invoice`, `mm_ewb_missing`.
2. **Deterministic Generator (`data/generate.py --regime A|B --seed N [--json-only]`)**:
   - `data/generator/cities.py`: ~60 Indian cities with `pincode`, `state_code`, `lat`, `lon`.
   - `data/generator/world.py`: ~200 firms with valid mod-36 GSTINs, embedded PAN, state code, director clusters (2–6 firms sharing directors), name/GSTIN aliasing (same PAN or Pvt Ltd vs Private Limited).
   - `data/generator/docs.py`: 12 months of sector-flow trades, POs, GRNs, service acceptances, invoices, e-way bills (`from_pincode`, `to_pincode`, `distance_km`, `vehicle_no`, `generated_at`), payments, credit notes, contracts.
   - `data/generator/pdfs.py` & `scans.py`: 3 deterministic ReportLab layouts (`invariant = 1`) + 10 noisy raster scans.
   - `data/generator/fraud.py`: Injects all fraud/mismatch/ring archetypes with `ground_truth.json`.
   - Deterministic Ed25519 keys derived via `HKDF(seed, info=party_id)`.

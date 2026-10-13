# Agent A3 Brief — Three-Way Matching & Consortium Dedup Registry (`match/`, `dedup/`)

## Ownership & Boundaries
- **Owned folders**: `match/`, `dedup/`, `tests/test_match.py`, `tests/test_dedup.py`
- **Forbidden folders**: All other module folders
- **Seams**: `match/check.py:run(ctx: CaseContext) -> list[CheckResult]` and `dedup/check.py:run(ctx: CaseContext) -> list[CheckResult]`

## Deliverables
1. **Matching (`match/`)**:
   - `three_way.py`, `services.py`, `ordering.py`, `contract_terms.py`, `partials.py`, `ewaybill.py` + `rules/tolerances.toml` + `rules/ewaybill.toml`.
   - Missing input is `unavailable`, never `pass`. Goods checks on services are `not_applicable`.
2. **Deduplication & Registry (`dedup/`)**:
   - `identity.py`: `HMAC(k, gstin|fy|doc_type|invoice_no)` + version classifier + field-by-field diff on `unexplained_change`.
   - `near_dup.py`: Aggressive normalization (`O->0`, `I->1`, `l->1`) + same parties + 1% amount + 7d date + line-item Jaccard `>= 0.8` → `warn`.
   - `registry.py`: SQLite WAL with `BEGIN IMMEDIATE`, partial unique index on `invoice_identity WHERE state IN ('reserved','financed','settled')`, expire-on-read inside transaction, Ed25519 signature checks, idempotency via `ops(op_id)`, capacity verification.

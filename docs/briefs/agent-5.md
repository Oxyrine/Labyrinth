# Agent A5 Brief — Buyer/Receivables Risk, Pricing & Workflow Engine (`risk/`, `workflow/`)

## Ownership & Boundaries
- **Owned folders**: `risk/`, `workflow/`, `tests/test_risk.py`, `tests/test_workflow.py`
- **Forbidden folders**: All other module internals (workflow calls each module's public `check.run`)
- **Seam**: `risk/check.py:run(ctx: CaseContext) -> list[CheckResult]` and `workflow/pipeline.py:run_pipeline(...) -> Decision`

## Deliverables
1. **Risk (`risk/`)**:
   - `features.py`, `scorecard.py` + `scorecard.toml` (points → Grade A–E, top-3 numerical reasons), `pricing.py` (Advance: A 90%, B 85%, C 75%, D 60%, E decline; Annual rate: A 10%, B 12%, C 14%, D 17%; `discount = advance * rate_bps * days / (10000 * 365)` round-half-up in integer paise).
   - Exact test assertion: `₹10,00,000` @ 85% = `85_000_000` paise; 12% for 60d = `1_676_712` paise (`₹16,767.12`).
2. **Workflow (`workflow/`)**:
   - `pipeline.py`: Concurrent stage runner (`extract` → `match || dedup || network` → `risk` → `decision`), auto-cancels reservation on `reject`, wraps module exceptions as `unavailable`.
   - `decision.py`: Strict precedence (1: hard fail → `reject`, 2: required `unavailable` → `review`, 3: `warn`/near-dup/unexplained-change/network tier >= medium → `review`, 4: Grade A–C → `conditionally_approved`, D → `review`, E → `reject`).
   - `states.py`, `overrides.py` (dual distinct reviewer for hard-fail override, invariant re-check in `disburse()`), `card.py`.

# Agent A8 Brief — Independent Evaluation & Red-Team Harness (`eval/`)

## Ownership & Boundaries
- **Owned folders**: `eval/`, `tests/test_eval.py`
- **Forbidden imports**: `eval/` must NEVER import from `extract`, `match`, `dedup`, `network`, `risk`, `workflow`, `audit`, `chain`, or `app` (enforced by CI guard). It may only use Python stdlib and `contract` schema validation if needed, reading `decisions.jsonl`, `rings.json`, and `ground_truth.json`.

## Deliverables
- `eval/thresholds.lock.json`: Frozen weights, tier thresholds, and matching tolerances committed before Regime B evaluation.
- `eval/baseline.py`: Evaluates rules-only baseline (`decisions_baseline.jsonl`, network disabled) vs full hybrid pipeline (`decisions.jsonl`, baseline + network).
- `eval/report.py` → `eval/report.md` + `eval/report.json`:
  - Title: `# Synthetic benchmark results`
  - Per case type `n`, detection rate, false-review rate (invoice-level and ring-level), baseline vs baseline+network lift, stage latency p50/p95, plausible-forgery evasive cases explicitly reported as misses, `thresholds.lock.json` SHA-256 hash.
- `eval/redteam/*.json`: Independent red-team cases authored strictly from the specification.

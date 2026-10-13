# Agent A4 Brief — Network Authenticity & Ring Analysis (`network/`)

## Ownership & Boundaries
- **Owned folders**: `network/`, `tests/test_network.py`
- **Forbidden folders**: All other module folders (and strictly no Circe repo access)
- **Seam**: `network/check.py:run(ctx: CaseContext) -> list[CheckResult]`

## Deliverables
- `canonicalize.py`: Exact GSTIN or normalized name+address exact match; union directors.
- `graph.py`: Directed multi-invoice edges (`test_l7_all_invoices_per_edge`).
- `scc.py` & `cycles.py`: Iterative Tarjan + depth-limited DFS (`<= 7`), canonical rotation key, loud `BudgetExceeded` on budget breach.
- `incremental.py`: Online cycle discovery on invoice arrival (`<= 6` hops) + related-party closure requiring `>= 2` independent corporate links (`test_l4_single_link_no_bridge`).
- `signals/`: `flow.py` (interior firms only, no div-by-zero), `product.py` (rotation-invariant including closing hop), `timing.py` (sorted hop dates), `isolation.py`, `movement.py` (pincode city haversine × 1.3 road factor, > 80 km/h speed), `related.py` (rarity weight `1/log2(1+n)`), `transform.py`, `rapid.py`.
- `guard.py`, `score.py` (weighted geometric mean, `< 2` signals capped at `medium`, review priority sort `(tier, exposure)`), `evidence.py` (concrete numerical sentences in Indian format).

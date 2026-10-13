# Tessera — Invoice Financing with Hybrid Audit

Tessera is a consortium invoice-financing and hybrid-audit platform for MSME receivables discounting. It combines deterministic document extraction, three-way PO/GRN/contract matching, atomic consortium double-financing prevention, graph-based circular-trade & related-party ring analysis, transparent buyer/receivables risk pricing, and off-chain AES-256-GCM document storage anchored via RFC-6962 Merkle proofs to an on-chain `Anchor` contract.

## Three Distinct Verification Properties (Never Conflated)

| Property | Established by | What it proves (and does not prove) |
|---|---|---|
| **Document consistency** | Extraction validators + three-way matching (`extract/`, `match/`) | Figures, GSTINs, tax regimes, dates, quantities, and rates agree across Invoice, PO, GRN, and Contract. Does **not** prove the trade is genuine. |
| **Document integrity** | Append-only SHA-256 hash chain + salted RFC-6962 Merkle anchoring (`audit/`, `chain/`) | Submitted files, extracted field versions, and workflow events have not been altered or truncated since capture. Does **not** prove the underlying invoice was truthful when uploaded. |
| **Trade authenticity** | Nothing proves it; network ring signals + multi-source delivery evidence (`network/`, `match/ewaybill.py`) raise fabrication cost and route suspicious rings to human review | Identifies circular trade loops, corporate-bridge closures, impossible vehicle transit speeds, and low value retention to prioritize human audit. |

---

## Architecture & Module Seam

Every module communicates exclusively through `contract/models.py` (`schema_version: 1`) and implements the uniform check interface in `contract/context.py`:

```python
def run(ctx: CaseContext) -> list[CheckResult]
```

- **Money & Rates**: Strict integer **paise** (`1 ₹ = 100 paise`) and **basis points** (`1% = 100 bps`) everywhere.
- **Check Status Semantics**: `pass`, `warn`, `fail`, `unavailable`, `not_applicable`. Missing evidence or unreachable sources always emit `unavailable` (routing required checks to human review) — **never** `pass`.
- **Per-Module Source Flag**: Every `CheckResult` carries `source: "live" | "fixture"`, surfaced directly on the lender explanation card.
- **Privacy by Default**: External LLM extraction fallback (`claude-haiku-5-5`) and optional card summary (`claude-sonnet-5-5`) are disabled by default (`TESSERA_ALLOW_EXTERNAL_LLM=0`).

---

## Repository Layout

```
tessera/
  contract/   Pydantic v2 models, JSON Schemas, CaseContext, WIRE_PROTOCOL.md, validate.py
  fixtures/   Hand-crafted test cases for every fraud, mismatch, and legitimate scenario
  data/       Deterministic synthetic economy generator (Regime A dev, Regime B held-out)
  extract/    PDF text layer, 3 deterministic layout parsers, OCR scan path, tamper hints, validators
  match/      Three-way PO/GRN match, SAC service milestones, ordering, cumulative partials, e-way bill
  dedup/      HMAC invoice identity, version diffing, near-duplicate check, SQLite BEGIN IMMEDIATE registry
  network/    Party canonicalization, Tarjan SCC, bounded cycle search, 8 ring signals, legitimacy guard
  risk/       Buyer payment history, receivables concentration/dilution, points scorecard, bps pricing
  workflow/   Concurrent stage runner, precedence decision engine, state machine, dual-reviewer overrides
  audit/      AES-256-GCM blob store, event hash chain, salted RFC-6962 Merkle proofs, selective disclosure
  chain/      Solidity Anchor.sol, web3.py RPC client, non-blocking background batch anchorer
  app/        FastAPI server + static vanilla JS UI (Seller, Lender, Reviewer Queue, Verifier, Offline mode)
  eval/       Independent evaluation harness (reads only pipeline outputs + ground_truth.json)
```

---

## Quickstart

```bash
python run.py test               # Run contract validation + full fast test suite
python run.py world --regime A   # Deterministically generate Regime A economy, PDFs, scans, and keys
python run.py pipeline           # Run all stages across Regime A and emit decisions.jsonl + rings.json
python run.py report             # Generate synthetic benchmark report (eval/report.md + eval/report.json)
TESSERA_DEMO=1 python run.py demo # Start FastAPI server with interactive demo & guided tour
```

> *Pricing Notice*: All advance rates and discount quotes in Tessera are **illustrative prototype policy, not validated lending prices**.

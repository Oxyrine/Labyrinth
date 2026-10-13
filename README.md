# Labyrinth — Invoice Financing with Hybrid Audit

Labyrinth is an invoice-financing prototype for Synesis 26, Problem 2. One financing request moves through
extraction, document matching, duplicate and cross-lender checks, a network screen, buyer/receivables risk and a
decision, and every state change is written to a hash-chained, Merkle-anchored audit log. Uncertain cases go to a person.

## Three properties, kept apart

| Property | Question | Established by | What it does **not** show |
|---|---|---|---|
| Document consistency | Do the records agree with each other? | `extract/`, `match/` | That the trade happened |
| Document integrity | Has a record changed since we received it? | `audit/`, `chain/` | That the original was truthful |
| Trade authenticity | Did the trade happen? | Nothing proves it; `network/` and delivery evidence raise the cost of fabrication and surface it for review | Anything about colluders who forge plausible movement documents |

## Run it

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # Windows; use .venv/bin on Linux/macOS
python run.py test                    # contract validation + fast tests (python run.py test --all adds slow tests)
python run.py world --regime A        # deterministic synthetic economy, PDFs, scans
python run.py pipeline --regime A     # real pipeline over every case -> decisions.jsonl, rings.json, extractions.jsonl
python run.py report                  # eval/report.md and eval/report.json, computed from those outputs
LABYRINTH_DEMO=1 python run.py demo     # UI at http://127.0.0.1:8000 (demo-only tamper / network-off routes need LABYRINTH_DEMO=1)
python app/build_data.py              # refresh the offline snapshot app/static/data.js from a real engine run
```

## What is real

- **Extraction** (`extract/`): reads the *visible* text of three invoice layouts (tabular GST, letterhead, Tally voucher).
  Nothing is invented: a document that cannot be read raises an error and goes to a human. Platform ids (invoice id,
  party ids by GSTIN) are assigned by the platform, not read from the file. Scanned copies need OCR.
- **Matching, duplicates, registry** (`match/`, `dedup/`): three-way match, services path, e-way rules, HMAC invoice identity,
  near-duplicates, and a SQLite consortium registry with atomic reserve / confirm, idempotent `op_id`s, expire-on-read,
  seller and buyer Ed25519 signatures, and a capacity cap.
- **Network** (`network/`): canonicalisation, iterative SCC, bounded ring search, eight signals, legitimate-loop guard,
  evidence sentences built from numbers. Review priority, not expected loss.
- **Risk and workflow** (`risk/`, `workflow/`): points scorecard, integer paise / bps pricing, precedence-based decisions,
  an explicit state machine, dual-reviewer overrides, and disbursement invariants that no override can lift.
- **Audit** (`audit/`, `chain/`): AES-256-GCM blobs with per-seller wrapped keys, SQLite event hash chain, salted RFC-6962
  field trees, selective disclosure, Merkle batches, truncation detection. Integrity and anchoring are answered separately.
- **Evaluation** (`eval/`): reads pipeline outputs and ground truth only, never module code. Thresholds are frozen in
  `eval/thresholds.lock.json` before the held-out regime B is generated.
- **Demo app** (`app/`): `app/live.py` runs fixtures and uploads through the real modules; the server is a thin HTTP layer.

## Demo simplifications (the UI says so where it matters)

- **Anchoring is a local simulated contract** unless `LABYRINTH_RPC` and `LABYRINTH_ANCHOR_KEY` point at a funded testnet
  account. `chain/Anchor.sol` and the web3 path exist but have not been run against a testnet here.
- **Keys**: party, buyer and lender signing keys are derived from a public demo seed, and the demo server signs on
  behalf of the selected demo role. Production needs a KMS, separate key roles and real identity. Role headers are not authentication.
- **E-way bills, GRNs and buyer confirmations are fixtures.** Real access needs authorised APIs and taxpayer consent.
- **The registry is a trusted service** that sees invoice identities. A real deployment needs an oblivious PRF or private
  set intersection, consortium governance and legal agreements, and it only covers participating lenders.
- **Grades and rates are illustrative policy**, not a validated credit model. The discount arithmetic is exact.
- **Benchmark results are synthetic**, from a generator the same team wrote, so they show the system does what it is
  designed to do, not how it performs on real fraud. Plausible forgeries are generated to be undetectable and are
  reported as misses.
- **OCR**: scanned copies are read with Tesseract when installed. It is not installed on the development machine, so the
  OCR path has not been exercised here; without it, scans are reported as unread (`ocr_engine`) rather than guessed.
- **Not implemented**: RBI TReDS rules, GST e-invoice IRP integration, data localisation, consent flows.

## Layout

```
contract/   Pydantic models, JSON Schemas, CaseContext, WIRE_PROTOCOL.md
fixtures/   25 hand-written cases (one per fraud / mismatch / legitimate type), built by build_fixtures.py
data/       deterministic generator (world, documents, visible-text PDFs, scans, fraud injection); data/out is committed
extract/    visible-text parsers, validators, tamper hints, prompt-injection handling, optional pinned Claude fallback (off by default)
match/ dedup/ network/ risk/ workflow/ audit/ chain/   as above
app/        live engine, FastAPI server, static UI, offline snapshot
eval/       baseline, report, extraction accuracy, red-team cases, frozen thresholds
```

All money is integer paise and rates are basis points. Missing input is `unavailable`, never `pass`. Contract changes
after the freeze are additive only. Pricing is labelled illustrative everywhere it appears.

## Interface

A landing screen, then five views in a numbered stepper: Finance (decision and evidence), Submit (upload a PDF and see what was
read), Review (queue, one-invoice-one-lender race, audited overrides), Network (map of the 200-firm generated market: invoices,
related firms, flagged loops), Verify (integrity and anchoring, break-it sandbox,
benchmark). The look (warm ink, gold seal, serif display type) takes its direction from the Circe console; the code is new.
Fonts load from Google Fonts and fall back to system fonts offline; the CSP allows only `fonts.googleapis.com` and
`fonts.gstatic.com` for that. Without the server, `file://` opens a read-only snapshot.

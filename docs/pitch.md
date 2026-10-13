# Tessera — Pitch & Q&A Framing (Synesis 26 · Problem 2)

## Core Framing: Three Properties Never Conflated

1. **Document consistency** (`extract/`, `match/`): Do the Invoice, PO, GRN, and Contract agree on parties, HSN/SAC, quantities, unit rates, tax regime, and dates?
2. **Document integrity** (`audit/`, `chain/`): Are the off-chain encrypted blobs, extracted field versions, and workflow events unaltered since submission, and can a lender or auditor verify inclusion against an on-chain Merkle root without seeing confidential fields?
3. **Trade authenticity** (`network/`, `match/ewaybill.py`): **Nothing proves trade authenticity.** Instead, graph ring topology, related-party closure, and physical movement signals raise the cost of fabrication and route high-risk rings to human review with concrete numerical evidence.

---

## 6-Step Live Demo Script (270 seconds)

1. **Consistent Ring (90 s)** — `ring_real_firms`:
   - Every document check passes (PO, GRN, arithmetic, GSTIN checksums all green).
   - The network check flags a 5-firm loop: under 1% value retained by the interior firms, the same vehicle on e-way bills in Pune and Chennai under three hours apart, and firms that share directors, a bank account and an address → **Review**. Read the numbers off the card; they come from the real signals.
   - Explicitly state on stage: *"E-way bills and buyer confirmations in this demo come from deterministic fixtures (`source: fixture`)."*
2. **Double Financing & Concurrency (45 s)**:
   - `double_financing_cross_lender` → rejected immediately (`held by another participant`).
   - `concurrent_reservation` → fire two parallel lender reservation requests simultaneously → SQLite `BEGIN IMMEDIATE` + partial unique index yields **exactly one winner**.
   - `refinance_settled` → terminal `settled` state blocks re-reservation forever.
3. **Edited Resubmission & Near-Duplicate (30 s)**:
   - `altered_amount_same_identity` → same HMAC identity (`GSTIN|FY|doc_type|invoice_no`), different SHA-256 and grand total, no linked credit/debit note → routed to **Review** with field-by-field diff.
   - `near_dup_INV-O042` (`INV-O042` vs `INV-0042`, O→0 normalization + Jaccard `>= 0.8`) → routed to **Review**.
4. **Clean End-to-End Financing (45 s)** — `clean_goods`:
   - Upload PDF → extraction from the printed text with confidence chips → 3-way match passes → Grade B (`85%` advance on `₹10,00,000` = `₹8,50,000`; `12%` p.a. for `60` days = `₹16,767.12` discount) → **Conditionally Approved** → Buyer signed acceptance → Reservation confirmed → **Disbursed**.
5. **Tamper Catch & Degraded RPC (30 s)**:
   - Flip 1 byte in an encrypted blob (`POST /demo/tamper`) → Audit Verifier pinpoints **Integrity: FAILED** naming the exact file (`D-...`).
   - Toggle RPC offline (`POST /demo/network-off`) → Audit Verifier reports **Integrity: VERIFIED · Anchoring: UNAVAILABLE** — distinct statuses, never conflated.
6. **Synthetic Benchmark & Break-It (30 s)**:
   - Show `eval/report.md` (synthetic results; say so): per-type detection rates, rules-only baseline vs. baseline + network lift, false-review rate on legitimate circular supply chains (`legit_circular`), and honest miss rate on plausible evasive forgeries (`ring_evasive`).
   - Invite judges to submit a custom ring on `/breakit` and inspect the live network response.

---

## Verbatim Q&A Lines

- **On proving real trade**: *"No software proves that physical goods moved if seller, buyer, and transporter collude. Tessera separates document consistency and cryptographic integrity from trade authenticity: it forces colluders to forge consistent multi-party logistics and corporate histories across time, and routes anomalies to human reviewers with exact numerical evidence."*
- **On blockchain usage**: *"Confidential commercial invoices never touch the chain. Files stay encrypted off-chain with AES-256-GCM; only salted RFC-6962 Merkle roots of event batches are anchored on Polygon Amoy asynchronously off the request path."*
- **On pricing**: *"The scorecard and bps discount table are illustrative prototype policy to demonstrate risk-adjusted advance rates, not validated commercial lending prices."*
- **On the benchmark**: *"These are synthetic benchmark results from a generator we wrote, with thresholds frozen before the held-out run. They show the system does what it is designed to do; they are not a claim about real fraud. Plausible forgeries are built to be undetectable and we report them as misses. Try to break it yourself in the Break-It box."*
- **On extraction**: *"We read the printed text of three invoice layouts and never invent a field: unreadable documents go to a person. Scanned copies need OCR, which is an installed dependency, not a guess."*
- **On keys and the registry**: *"Demo keys come from a public seed and the demo server signs for the selected role. The registry is a trusted service that sees invoice identities; production needs an oblivious PRF or private set intersection, a KMS and consortium governance."*
- **On missing external APIs**: *"When GSTN/e-way bill or ERP endpoints are unreachable, Tessera emits `unavailable`, never `pass`, and routes required checks to human review."*

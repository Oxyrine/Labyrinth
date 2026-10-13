# Labyrinth Wire Protocol (`schema_version: 1`)

## 1. Core Invariants

1. **Money & Rates**:
   - All monetary amounts are strict integers in **paise** (`1 ₹ = 100 paise`).
   - All interest/tax/advance rates are strict integers in **basis points** (`1% = 100 bps`, `100% = 10000 bps`).
   - Float literals in money or bps fields are rejected by Pydantic validation and CI guards.
2. **Timestamps & Dates**:
   - Calendar dates use ISO-8601 `YYYY-MM-DD`.
   - Event and version timestamps use UTC ISO-8601 ending with `Z` (`YYYY-MM-DDTHH:MM:SSZ`).
3. **Check Status Semantics (`unavailable` ≠ `fail` ≠ `pass`)**:
   - `pass`: Check executed and satisfied policy/consistency rules.
   - `warn`: Soft anomaly or tamper hint requiring human review.
   - `fail`: Confirmed violation (e.g., arithmetic error, registry conflict, confirmed-absent required document).
   - `unavailable`: Missing input, unreachable external source, or module exception. **Missing input is NEVER `pass`.** Required checks returning `unavailable` route the request to `review` with condition `"request missing evidence"`.
   - `not_applicable`: Only emitted when the check domain does not apply (e.g., goods GRN/e-way bill checks on a pure SAC services invoice).
4. **Evidence Weight**:
   - Derived strictly from `EvidenceDoc.verification_status` (never free-form):
     - `unverified`: `0.4`
     - `signature_checked`: `0.7`
     - `independently_confirmed`: `1.0`
5. **Canonical JSON for Hashing & Signatures**:
   - Sorted keys (`sort_keys=True`), compact separators (`separators=(",", ":")`), UTF-8 encoding, no floats (`contract.canonical_json`).
6. **IDs**:
   - Party: `P-xxxx` (lenders: `L1..L3`)
   - Invoice: `INV-...`
   - Version: `V-...`
   - Evidence Document: `D-...`
   - Financing Request: `REQ-...`
   - Reservation: `RSV-...`
   - Ring: `R##` (e.g. `R01`)
7. **Schema Evolution**:
   - Additive-only changes after M0 freeze; bump `schema_version` minor only.

---

## 2. Registry of Dotted Check IDs

Every `CheckResult.check` must be one of the registered dotted identifiers below:

### Stage 1: Extraction (`extract.*`)
- `extract.gstin_format` — Seller and buyer GSTIN 15-character structure and state code validity
- `extract.gstin_checksum` — Modulo-36 checksum verification on seller and buyer GSTINs
- `extract.arith_lines` — Line-item arithmetic (`qty × rate_paise == taxable_paise` and tax calculation)
- `extract.arith_total` — Invoice subtotal + tax sum == `grand_total_paise` (±100 paise rounding tolerance)
- `extract.tax_regime` — Intra-state CGST+SGST vs inter-state IGST consistency (SEZ/exports → `warn` review, not `fail`)
- `extract.date_valid` — Invoice date not in future and falls within printed `financial_year`
- `extract.hsn_sac` — HSN codes on goods lines (`is_service=False`), SAC codes (`99...`) on service lines (`is_service=True`)
- `extract.tamper_metadata` — PDF metadata & incremental-save (`%%EOF`) inspection (at most `warn`, never `fail`)

### Stage 2a: Cross-Document Matching (`match.*`)
- `match.three_way.parties` — Invoice, PO, and GRN seller/buyer alignment
- `match.three_way.hsn` — Invoice, PO, and GRN HSN/SAC code alignment
- `match.three_way.qty` — Invoice quantity within ±2% tolerance of PO and GRN quantity
- `match.three_way.rate` — Invoice unit rate within ±1% tolerance of PO rate
- `match.three_way.total` — Invoice total consistent with PO and GRN accepted value
- `match.three_way.grn_presence` — Required GRN presence for goods invoices (`not_applicable` for services)
- `match.services.acceptance` — Contract milestone + service completion/acceptance record (`not_applicable` for goods)
- `match.ordering.dates` — Chronological ordering: `PO date <= GRN/acceptance date <= invoice date` and PO/contract validity
- `match.contract.terms` — Unit rate `<= contract rate cap`, payment terms match, contract in force
- `match.over_invoicing` — Cumulative invoiced quantity/value across partial shipments `<= PO quantity/value`
- `match.ewaybill` — E-way bill requirement & verification under simplified rules (`rules/ewaybill.toml`)

### Stage 2b: Deduplication & Consortium Registry (`dedup.*`)
- `dedup.identity` — Invoice identity HMAC version classifier (initial, resubmission, unexplained amendment diff, extraction revision)
- `dedup.near_dup` — Aggressive normalized invoice number + party + amount (1%) + date (7d) + line-item Jaccard (`>= 0.8`) check (`warn` only)
- `dedup.registry` — Atomic consortium reservation (`reserve`), capacity check (`amount <= outstanding`), and double-financing prevention

### Stage 2c: Network Authenticity (`network.*`)
- `network.ring` — Circular trade & related-party closure detection (`s_flow`, `s_product`, `s_timing`, `s_isolation`, `s_movement`, `s_related`, `s_transform`, `s_rapid` + legitimacy guard)

### Stage 3: Buyer & Receivables Risk (`risk.*`)
- `risk.grade` — Buyer payment history, receivables concentration/dilution, invoice anomalies, and network tier scorecard (`A`–`E`) and pricing quote

---

## 3. Evidence Sentence Rule

`CheckResult.evidence` must contain human-readable sentences constructed from concrete numbers, dates, IDs, and rupee amounts (formatted via `format_inr`), **never** bare strings like `"score: 0.91"`.

# Synthetic benchmark results

Independent evaluation of Labyrinth's verification pipeline comparing the **rules-only baseline** (document extraction, 3-way matching, and consortium deduplication/registry checks with graph ring analysis disabled) against the **full hybrid pipeline** (baseline rules + multi-hop network & related-party ring analysis).

**How to read these numbers.** They measure agreement with labels from our own synthetic generator, and the same team wrote the generator and the checks. They show the pipeline does what it is designed to do on these cases; they are not a claim about real-world fraud. Thresholds were frozen before the held-out regime was generated. `ring_evasive` includes plausible forgeries that are built to be undetectable and are reported as misses (section 6). Real-world validation is future work.

## 1. Frozen Thresholds & Run Provenance

- **Thresholds Lock File**: `eval/thresholds.lock.json`
- **Thresholds Lock SHA-256 (`thresholds_lock_sha256`)**: `7043da2050658627536947bfd34a7e462e31b58d6e53cc919a237477715e76c9`
- **Thresholds Canonical SHA-256**: `6a3387b366162fea1e64e1ee3394a3db862af51a49bcd96d70d04b50ed4cb586`
- **Locked At**: `2026-10-13T00:00:00Z` (frozen prior to Regime `B` evaluation)
- **Evaluated Regime**: `B` (`data/out/regime_B`)
- **Total Evaluated Invoices**: `404` (`278` planted fraud/mismatch/ring cases, `126` cases whose correct outcome is approval)

## 2. Executive Summary: Baseline vs. Hybrid (Baseline + Network) Lift

| Metric | Rules-Only Baseline | Hybrid (Baseline + Network) | Network Lift |
| :--- | ---: | ---: | ---: |
| **Overall Positive Detection Rate (All Planted Fraud/Mismatches)** | 87.8% (244/278) | 98.9% (275/278) | **+11.2%** |
| **Detectable Circular Ring Invoice Detection (`ring_shells` + `ring_real_firms`)** | 0.0% | 100.0% | **+100.0%** |
| **All Circular Ring Invoice Detection (incl. `ring_evasive` misses)** | 0.0% | 91.2% | **+91.2%** |
| **Ring-Level Detection Rate (Detectable Fraud Rings)** | 0.0% (0/24) | 100.0% (24/24) | **+100.0%** |
| **Ring-Level Detection Rate (All Fraud Rings incl. `ring_evasive`)** | 0.0% (0/34) | 91.2% (31/34) | **+91.2%** |
| **Invoice-Level False-Review Rate (every case whose correct outcome is approval)** | 0.0% (0/126) | 0.0% (0/126) | +0.0% |
| **Legitimate Circular Supply Chain False-Review Rate (Invoice-Level)** | 0.0% | 0.0% | +0.0% |
| **Ring-Level False-Review Rate (`legit_circular` rings)** | 0.0% (0/32) | 0.0% (0/32) | +0.0% |

## 3. Per-Case-Type Benchmark Breakdown (Invoice-Level)

For planted fraud/mismatch/ring archetypes (`is_positive = true`), **Detection Rate** is the fraction of cases routed to `review` or `reject`. For archetypes whose correct outcome is approval (clean trades, legitimate circular trade, prompt-injection text, concurrent-lender winners), **Detection Rate** shows clean approval accuracy (`1 - false_review_rate`) and **False-Review Rate** reports legitimate invoices falsely routed to review/reject.

| Case Type | Category | `n` | Baseline Detection Rate | Hybrid (Baseline + Network) Detection Rate | Lift (`+pct`) | Baseline False-Review Rate | Hybrid False-Review Rate |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| `clean_goods` | `clean` | 50 | 100.0% | 100.0% | +0.0% | 0.0% (0/50) | 0.0% (0/50) |
| `clean_services` | `clean` | 12 | 100.0% | 100.0% | +0.0% | 0.0% (0/12) | 0.0% (0/12) |
| `legit_circular` | `clean` | 32 | 100.0% | 100.0% | +0.0% | 0.0% (0/32) | 0.0% (0/32) |
| `bad_arith` | `document_consistency` | 10 | 100.0% | 100.0% | +0.0% | 0.0% (n/a) | 0.0% (n/a) |
| `mm_qty` | `document_consistency` | 10 | 100.0% | 100.0% | +0.0% | 0.0% (n/a) | 0.0% (n/a) |
| `mm_rate` | `document_consistency` | 10 | 100.0% | 100.0% | +0.0% | 0.0% (n/a) | 0.0% (n/a) |
| `mm_grn_missing` | `document_consistency` | 10 | 100.0% | 100.0% | +0.0% | 0.0% (n/a) | 0.0% (n/a) |
| `no_delivery` | `document_consistency` | 10 | 100.0% | 100.0% | +0.0% | 0.0% (n/a) | 0.0% (n/a) |
| `mm_date_order` | `document_consistency` | 10 | 100.0% | 100.0% | +0.0% | 0.0% (n/a) | 0.0% (n/a) |
| `mm_over_invoice` | `document_consistency` | 10 | 100.0% | 100.0% | +0.0% | 0.0% (n/a) | 0.0% (n/a) |
| `mm_ewb_missing` | `document_consistency` | 10 | 100.0% | 100.0% | +0.0% | 0.0% (n/a) | 0.0% (n/a) |
| `docs_unavailable` | `document_consistency` | 10 | 100.0% | 100.0% | +0.0% | 0.0% (n/a) | 0.0% (n/a) |
| `prompt_injection` | `document_consistency` | 10 | 100.0% | 100.0% | +0.0% | 0.0% (0/10) | 0.0% (0/10) |
| `exact_duplicate` | `dedup_registry` | 22 | 100.0% | 100.0% | +0.0% | 0.0% (n/a) | 0.0% (n/a) |
| `edited_resubmission` | `dedup_registry` | 22 | 100.0% | 100.0% | +0.0% | 0.0% (n/a) | 0.0% (n/a) |
| `altered_amount_same_identity` | `dedup_registry` | 22 | 100.0% | 100.0% | +0.0% | 0.0% (n/a) | 0.0% (n/a) |
| `near_dup` | `dedup_registry` | 22 | 100.0% | 100.0% | +0.0% | 0.0% (n/a) | 0.0% (n/a) |
| `double_financing_cross_lender` | `dedup_registry` | 22 | 100.0% | 100.0% | +0.0% | 0.0% (n/a) | 0.0% (n/a) |
| `concurrent_reservation` | `dedup_registry` | 22 | 100.0% | 100.0% | +0.0% | 0.0% (0/22) | 0.0% (0/22) |
| `above_outstanding` | `dedup_registry` | 22 | 100.0% | 100.0% | +0.0% | 0.0% (n/a) | 0.0% (n/a) |
| `refinance_settled` | `dedup_registry` | 22 | 100.0% | 100.0% | +0.0% | 0.0% (n/a) | 0.0% (n/a) |
| `ring_shells` | `network_ring` | 12 | 0.0% | 100.0% | +100.0% | 0.0% (n/a) | 0.0% (n/a) |
| `ring_real_firms` | `network_ring` | 12 | 0.0% | 100.0% | +100.0% | 0.0% (n/a) | 0.0% (n/a) |
| `ring_evasive` | `network_ring` | 10 | 0.0% | 70.0% | +70.0% | 0.0% (n/a) | 0.0% (n/a) |

## 4. Ring-Level Topology Evaluation (`rings.json` vs. `ground_truth.json`)

| Ring Archetype | Ring Count (`n`) | Baseline Ring Detection Rate | Hybrid Ring Detection Rate | Ring Lift (`+pct`) | Baseline Ring False-Review Rate | Hybrid Ring False-Review Rate |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| `ring_shells` | 12 | 0.0% | 100.0% | +100.0% | 0.0% | 0.0% |
| `ring_real_firms` | 12 | 0.0% | 100.0% | +100.0% | 0.0% | 0.0% |
| `ring_evasive` | 10 | 0.0% | 70.0% | +70.0% | 0.0% | 0.0% |
| `legit_circular` | 32 | 100.0% | 100.0% | +0.0% | 0.0% | 0.0% |

## 5. Stage Latency Profile (`p50` / `p95` in ms)

| Pipeline Stage | Samples | `p50` Latency (ms) | `p95` Latency (ms) | Max Latency (ms) |
| :--- | ---: | ---: | ---: | ---: |
| `extract` | 404 | 15 ms | 18 ms | 18 ms |
| `match` | 404 | 9 ms | 11 ms | 125 ms |
| `dedup` | 404 | 4 ms | 6 ms | 120 ms |
| `network` | 404 | 16 ms | 22 ms | 222 ms |
| `risk` | 404 | 5 ms | 6 ms | 6 ms |
| `decision` | 404 | 2 ms | 3 ms | 3 ms |
| `total` | 404 | 38 ms | 45 ms | 227 ms |

## 6. Plausible-Forgery Evasive Cases (`ring_evasive`) — Honest Misses

> *Nothing proves trade authenticity.* `ring_evasive` rings come in two kinds in the generator: ones that leave a trace (shared directors or impossible vehicle movement) and **plausible forgeries** (`plausible_forgery = true`: clean movement, no shared directors, heavy outside trade). Plausible forgeries are constructed to be undetectable, so they are reported as misses here, not hidden. The rows below are every `ring_evasive` invoice that was not flagged.

| Case ID | Invoice ID | Ring ID | Plausible forgery | Baseline Outcome | Hybrid Outcome |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `ring_evasive_008` | `INV-2602-10136` | `R32` | yes | `conditionally_approved` | `conditionally_approved` |
| `ring_evasive_009` | `INV-2602-10148` | `R33` | yes | `conditionally_approved` | `conditionally_approved` |
| `ring_evasive_010` | `INV-2602-10160` | `R34` | yes | `conditionally_approved` | `conditionally_approved` |

## 7. Independent Red-Team Suite (`eval/redteam/`)

| Red-Team Case ID | Artifact Path | Target Archetype | Expected Outcome | Expected Failing / Warning Checks | Adversarial Hypothesis |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `rt_01_split_below_ewb` | `eval/redteam/rt_01_split_below_ewb.json` | `mm_over_invoice` | `reject` | `match.over_invoicing`, `dedup.near_dup` | Splitting an invoice into multiple sub-₹50,000 tranches to bypass mandatory e-way bill verification is caught by cumulative PO over-invoicing and near-duplicate guards. |
| `rt_02_bridge_single_director_decoy` | `eval/redteam/rt_02_bridge_single_director_decoy.json` | `clean_goods` | `conditionally_approved` | `pass` (no false positive) | A single shared independent director between two otherwise unrelated firms must not close a synthetic related-party bridge (requires >= 2 independent corporate links). |
| `rt_03_slow_ring_45d` | `eval/redteam/rt_03_slow_ring_45d.json` | `ring_real_firms` | `review` | `network.ring` | Slowing a 4-hop circular ring down to 45 days suppresses s_rapid, but multi-signal combination (s_flow, s_product, s_isolation, s_transform) still exceeds medium/high network threshold and routes to review. |
| `rt_04_credit_note_wash` | `eval/redteam/rt_04_credit_note_wash.json` | `above_outstanding` | `reject` | `dedup.registry` | Issuing a post-invoice credit note to wash 80% of the receivable while requesting financing on the full original face value is blocked by net-outstanding capacity enforcement. |
| `rt_05_unicode_homoglyph_inv` | `eval/redteam/rt_05_unicode_homoglyph_inv.json` | `near_dup_INV-O042` | `review` | `dedup.near_dup` | Confusable homoglyph substitution ('l' for 'I' and 'O' for '0' in 'lNV-O088') with a 0.4% amount perturbation is caught by aggressive character normalization and Jaccard line-item matching in dedup.near_dup. |

## 8. Extraction accuracy (read from the printed text, compared with ground truth)

40 generated documents: 30 read, 30 matched every compared field (invoice number, dates, GSTINs, place of supply, terms, PO and contract references, all amounts and every line item). 10 were read with a field confidence below 0.9.

| Layout | Documents | Read | Exact match |
| :--- | ---: | ---: | ---: |
| `letterhead` | 10 | 10 | 10 |
| `scan` | 10 | 0 | 0 |
| `tabular_gst` | 10 | 10 | 10 |
| `tally` | 10 | 10 | 10 |

Not read (routed to a human, never guessed): `ocr_engine`: 10. Scanned copies need the Tesseract OCR engine; on a machine without it they are reported here instead of being evaluated.

| Field | Accuracy |
| :--- | ---: |
| `invoice_no` | 100.0% |
| `invoice_date` | 100.0% |
| `due_date` | 100.0% |
| `financial_year` | 100.0% |
| `seller_gstin` | 100.0% |
| `buyer_gstin` | 100.0% |
| `place_of_supply_state` | 100.0% |
| `payment_terms_days` | 100.0% |
| `po_id` | 100.0% |
| `contract_id` | 100.0% |
| `subtotal_paise` | 100.0% |
| `cgst_paise` | 100.0% |
| `sgst_paise` | 100.0% |
| `igst_paise` | 100.0% |
| `total_tax_paise` | 100.0% |
| `grand_total_paise` | 100.0% |
| `line_items` | 100.0% |

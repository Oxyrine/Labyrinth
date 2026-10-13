"""Comprehensive unit and lesson tests for Stage 2c Network Authenticity (`network/`)."""

from __future__ import annotations

import random
import re
from dataclasses import fields

import pytest

from contract.context import CaseContext, InMemoryHistoryView
from contract.models import (
    CheckStatus,
    DocSource,
    DocType,
    EvidenceDoc,
    FinancingRequest,
    IndustryClass,
    Invoice,
    InvoiceVersion,
    LineItem,
    Party,
    Severity,
    Tier,
    VerificationStatus,
    canonical_json,
    make_gstin,
)
from network import (
    BudgetExceeded,
    IncrementalRingEngine,
    RingCandidate,
    ScoredRing,
    TradeGraph,
    canonicalize_parties,
    find_cycles,
    rank_rings,
    run,
    s_flow,
    s_isolation,
    s_movement,
    s_product,
    s_rapid,
    s_related,
    s_timing,
    s_transform,
    score_ring,
    score_signals,
)
import network.score as score_module


def _make_party(
    party_id: str,
    *,
    name: str | None = None,
    pan: str | None = None,
    state_code: str = "27",
    entity_code: str = "1",
    gstin: str | None = None,
    industry_class: IndustryClass = IndustryClass.TRADING,
    address: str | None = None,
    pincode: str = "400001",
    city: str = "Mumbai",
    phone: str | None = None,
    bank_account_hash: str | None = None,
    directors: list[str] | None = None,
) -> Party:
    suffix = re.sub(r"[^0-9]", "", party_id) or "0001"
    num = int(suffix) % 10000
    resolved_pan = pan or f"AABCT{num:04d}F"
    resolved_gstin = gstin or make_gstin(state_code, resolved_pan, entity_code)
    return Party(
        party_id=party_id,
        name=name or f"Firm {party_id} Private Limited",
        gstin=resolved_gstin,
        pan=resolved_pan,
        state_code=state_code,
        industry_class=industry_class,
        address=address or f"Plot {num}, MIDC Industrial Road, {city}",
        pincode=pincode,
        city=city,
        phone=phone or f"98200{num:05d}",
        bank_account_hash=bank_account_hash or f"BANK-HASH-{party_id}",
        directors=directors if directors is not None else [f"Director {party_id}"],
    )


def _make_invoice(
    invoice_id: str,
    seller: Party,
    buyer: Party,
    *,
    amount_paise: int = 10_00_000_00,
    invoice_date: str = "2025-01-10",
    due_date: str = "2025-03-11",
    payment_terms_days: int = 60,
    hsn_sac: str = "72081000",
) -> Invoice:
    return Invoice(
        invoice_id=invoice_id,
        invoice_no=f"NO-{invoice_id}",
        financial_year="2024-25",
        doc_type=DocType.INVOICE,
        invoice_date=invoice_date,
        due_date=due_date,
        seller_id=seller.party_id,
        seller_gstin=seller.gstin,
        buyer_id=buyer.party_id,
        buyer_gstin=buyer.gstin,
        place_of_supply_state=buyer.state_code,
        payment_terms_days=payment_terms_days,
        line_items=[
            LineItem(
                line_no=1,
                description="Hot Rolled Steel Coils",
                hsn_sac=hsn_sac,
                is_service=False,
                qty=100,
                unit="MT",
                rate_paise=amount_paise // 100,
                taxable_paise=amount_paise,
                igst_bps=0,
                tax_paise=0,
                total_paise=amount_paise,
            )
        ],
        subtotal_paise=amount_paise,
        total_tax_paise=0,
        grand_total_paise=amount_paise,
    )


def _make_context(
    target_invoice: Invoice,
    parties: dict[str, Party],
    all_invoices: list[Invoice],
    evidence: list[EvidenceDoc] | None = None,
) -> CaseContext:
    ver = InvoiceVersion(
        version_id="V-001",
        invoice_id=target_invoice.invoice_id,
        version_no=1,
        invoice_identity=f"ID-{target_invoice.invoice_id}",
        file_sha256="a" * 64,
        field_root="b" * 64,
        created_at="2025-01-15T10:00:00Z",
    )
    req = FinancingRequest(
        request_id="REQ-001",
        invoice_id=target_invoice.invoice_id,
        version_id=ver.version_id,
        seller_id=target_invoice.seller_id,
        buyer_id=target_invoice.buyer_id,
        lender_id="L1",
        requested_amount_paise=target_invoice.grand_total_paise,
        tenor_days=60,
        seller_signature_hex="c" * 64,
        submitted_at="2025-01-15T10:05:00Z",
    )
    history = InMemoryHistoryView(
        invoices=all_invoices,
        versions=[ver],
        evidence_docs=list(evidence or []),
    )
    return CaseContext(
        request=req,
        invoice=target_invoice,
        version=ver,
        parties=parties,
        evidence=list(evidence or []),
        history=history,
    )


# ---------------------------------------------------------------------------
# 1. Lesson 1: test_l1_priority_not_loss
# ---------------------------------------------------------------------------
def test_l1_priority_not_loss() -> None:
    """Sort key is explicitly named review_priority = (tier_rank, ring_exposure_paise), NEVER expected_loss."""
    field_names = {f.name for f in fields(ScoredRing)}
    assert "review_priority" in field_names
    assert "expected_loss" not in field_names
    assert hasattr(score_module, "review_priority")
    assert not hasattr(score_module, "expected_loss")

    p1 = _make_party("P-101")
    p2 = _make_party("P-102")
    p3 = _make_party("P-103")
    p4 = _make_party("P-201")
    p5 = _make_party("P-202")
    p6 = _make_party("P-203")
    parties = {p.party_id: p for p in (p1, p2, p3, p4, p5, p6)}

    # Ring 1: Smaller exposure (₹30,00,000 total)
    r1_invs = [
        _make_invoice("INV-101", p1, p2, amount_paise=10_00_000_00, invoice_date="2025-01-01"),
        _make_invoice("INV-102", p2, p3, amount_paise=10_00_000_00, invoice_date="2025-01-03"),
        _make_invoice("INV-103", p3, p1, amount_paise=10_00_000_00, invoice_date="2025-01-05"),
    ]
    # Ring 2: Larger exposure (₹90,00,000 total), same HIGH tier
    r2_invs = [
        _make_invoice("INV-201", p4, p5, amount_paise=30_00_000_00, invoice_date="2025-01-01"),
        _make_invoice("INV-202", p5, p6, amount_paise=30_00_000_00, invoice_date="2025-01-03"),
        _make_invoice("INV-203", p6, p4, amount_paise=30_00_000_00, invoice_date="2025-01-05"),
    ]

    graph = TradeGraph(parties=parties, invoices=r1_invs + r2_invs)
    cands = find_cycles(graph)
    scored = [score_ring(c, graph) for c in cands]
    ranked = rank_rings(scored)

    assert len(ranked) == 2
    assert ranked[0].tier == Tier.HIGH
    assert ranked[1].tier == Tier.HIGH
    assert ranked[0].ring_exposure_paise == 90_00_000_00
    assert ranked[1].ring_exposure_paise == 30_00_000_00
    assert ranked[0].review_priority == (3, 90_00_000_00)
    assert ranked[1].review_priority == (3, 30_00_000_00)
    assert ranked[0].review_priority > ranked[1].review_priority

    ui_dict = ranked[0].to_ui_dict(graph)
    assert "review_priority" in ui_dict
    assert "expected_loss" not in ui_dict


# ---------------------------------------------------------------------------
# 2. Lesson 3a: test_l3_rotation_invariance
# ---------------------------------------------------------------------------
def test_l3_rotation_invariance() -> None:
    """Rotating the starting node of a cycle produces identical signals and ring scores."""
    p1 = _make_party("P-001", industry_class=IndustryClass.MANUFACTURING)
    p2 = _make_party("P-002", industry_class=IndustryClass.TRADING)
    p3 = _make_party("P-003", industry_class=IndustryClass.TRADING)
    p4 = _make_party("P-004", industry_class=IndustryClass.TRADING)
    parties = {p.party_id: p for p in (p1, p2, p3, p4)}

    # Intentionally varying HSNs (exact, 4-digit, 2-digit) and out-of-order dates across hops
    invoices = [
        _make_invoice("INV-1", p1, p2, amount_paise=10_00_000_00, invoice_date="2025-01-12", hsn_sac="72081000"),
        _make_invoice("INV-2", p2, p3, amount_paise=9_90_000_00, invoice_date="2025-01-03", hsn_sac="72089000"),
        _make_invoice("INV-3", p3, p4, amount_paise=9_80_000_00, invoice_date="2025-01-09", hsn_sac="72141000"),
        _make_invoice("INV-4", p4, p1, amount_paise=9_75_000_00, invoice_date="2025-01-06", hsn_sac="72081000"),
    ]
    graph = TradeGraph(parties=parties, invoices=invoices)

    base_nodes = ("P-001", "P-002", "P-003", "P-004")
    rotations = [base_nodes[i:] + base_nodes[:i] for i in range(len(base_nodes))]

    ref_scored: ScoredRing | None = None
    for rot in rotations:
        hops = tuple((rot[i], rot[(i + 1) % len(rot)]) for i in range(len(rot)))
        # Construct RingCandidate directly with rotated members & rotated hop order
        rotated_cand = RingCandidate(
            ring_id="R01",
            canonical_key=RingCandidate.from_cycle(rot).canonical_key,
            members=rot,
            invoice_hops=hops,
            bridges=(),
        )
        scored = score_ring(rotated_cand, graph)
        if ref_scored is None:
            ref_scored = scored
        else:
            assert scored.canonical_key == ref_scored.canonical_key
            assert scored.signals["s_product"] == ref_scored.signals["s_product"]
            assert scored.signals["s_timing"] == ref_scored.signals["s_timing"]
            assert scored.signals["s_flow"] == ref_scored.signals["s_flow"]
            assert scored.signals["s_isolation"] == ref_scored.signals["s_isolation"]
            assert scored.score == ref_scored.score


# ---------------------------------------------------------------------------
# 3. Lesson 3b: test_l3_relabel_invariance
# ---------------------------------------------------------------------------
def test_l3_relabel_invariance() -> None:
    """Random permutation of party IDs produces identical ring signals and scores."""
    base_ids = ["P-010", "P-020", "P-030", "P-040"]

    def build_and_score(id_map: dict[str, str]) -> ScoredRing:
        p_a = _make_party(
            id_map["P-010"],
            pan="AAAAA1111A",
            industry_class=IndustryClass.MANUFACTURING,
            directors=["Anita Desai"],
            bank_account_hash="SHARED-BANK-99",
        )
        p_b = _make_party(
            id_map["P-020"],
            pan="BBBBB2222B",
            industry_class=IndustryClass.TRADING,
            directors=["Vikram Seth"],
        )
        p_c = _make_party(
            id_map["P-030"],
            pan="CCCCC3333C",
            industry_class=IndustryClass.TRADING,
            directors=["Anita Desai"],
            bank_account_hash="SHARED-BANK-99",
        )
        p_d = _make_party(
            id_map["P-040"],
            pan="DDDDD4444D",
            industry_class=IndustryClass.TRADING,
            directors=["Rohan Kulkarni"],
        )
        parties = {p.party_id: p for p in (p_a, p_b, p_c, p_d)}
        invs = [
            _make_invoice("INV-1", p_a, p_b, amount_paise=12_00_000_00, invoice_date="2025-01-02", hsn_sac="72081000"),
            _make_invoice("INV-2", p_b, p_c, amount_paise=11_90_000_00, invoice_date="2025-01-05", hsn_sac="72081000"),
            _make_invoice("INV-3", p_c, p_d, amount_paise=11_80_000_00, invoice_date="2025-01-08", hsn_sac="72082000"),
            _make_invoice("INV-4", p_d, p_a, amount_paise=12_10_000_00, invoice_date="2025-01-11", hsn_sac="72081000"),
        ]
        g = TradeGraph(parties=parties, invoices=invs)
        cands = find_cycles(g)
        assert len(cands) == 1
        return score_ring(cands[0], g)

    identity_map = {pid: pid for pid in base_ids}
    baseline = build_and_score(identity_map)

    rng = random.Random(42)
    pool = [f"P-{n:04d}" for n in range(100, 950, 37)]
    for _ in range(10):
        chosen = rng.sample(pool, len(base_ids))
        perm_map = dict(zip(base_ids, chosen))
        relabeled = build_and_score(perm_map)
        assert relabeled.signals == baseline.signals
        assert relabeled.score == baseline.score
        assert relabeled.tier == baseline.tier
        assert relabeled.review_priority == baseline.review_priority


# ---------------------------------------------------------------------------
# 4. Lesson 4: test_l4_single_link_no_bridge
# ---------------------------------------------------------------------------
def test_l4_single_link_no_bridge() -> None:
    """A single shared attribute (< 2 independent links) NEVER creates a corporate bridge."""
    # P-1 -> P-2 -> P-3 with only 1 shared attribute (shared_director) between P-1 and P-3
    p1 = _make_party("P-001", directors=["Common Director"], bank_account_hash="BANK-1", phone="9811100001")
    p2 = _make_party("P-002", directors=["Other Director"], bank_account_hash="BANK-2", phone="9811100002")
    p3 = _make_party("P-003", directors=["Common Director"], bank_account_hash="BANK-3", phone="9811100003")

    invs = [
        _make_invoice("INV-1", p1, p2, amount_paise=10_00_000_00, invoice_date="2025-01-01"),
        _make_invoice("INV-2", p2, p3, amount_paise=9_90_000_00, invoice_date="2025-01-03"),
    ]
    graph = TradeGraph(parties={p.party_id: p for p in (p1, p2, p3)}, invoices=invs)

    # Confirm 1 link is tracked in related_links, but is_corporate_bridge is False
    links = graph.related_links("P-001", "P-003")
    assert list(links.keys()) == ["shared_director"]
    assert not graph.is_corporate_bridge("P-001", "P-003")
    assert graph.corporate_bridges() == []

    # Neither batch cycle discovery nor incremental discovery forms a ring
    assert find_cycles(graph) == []
    engine = IncrementalRingEngine(parties=[p1, p2, p3], invoices=invs)
    assert engine.rings() == []


# ---------------------------------------------------------------------------
# 5. Lesson 4/Flow: test_flow_bridge_ring_no_div_zero
# ---------------------------------------------------------------------------
def test_flow_bridge_ring_no_div_zero() -> None:
    """In a corporate-bridge-closed ring, entry firm (in==0) and exit firm (out==0) are excluded from s_flow
    and never cause division by zero.
    """
    # P-1 -> P-2 -> P-3 where P-1 and P-3 share 2 independent corporate links (director + bank_hash)
    p1 = _make_party(
        "P-001",
        directors=["Rajesh Sharma"],
        bank_account_hash="SHARED-BANK-XYZ",
    )
    p2 = _make_party(
        "P-002",
        directors=["Unrelated Director"],
        bank_account_hash="UNIQUE-BANK-2",
    )
    p3 = _make_party(
        "P-003",
        directors=["Rajesh Sharma"],
        bank_account_hash="SHARED-BANK-XYZ",
    )
    invs = [
        _make_invoice("INV-1", p1, p2, amount_paise=10_00_000_00, invoice_date="2025-01-02"),
        _make_invoice("INV-2", p2, p3, amount_paise=9_85_000_00, invoice_date="2025-01-05"),
    ]
    graph = TradeGraph(parties={p.party_id: p for p in (p1, p2, p3)}, invoices=invs)
    assert graph.is_corporate_bridge("P-001", "P-003")

    cands = find_cycles(graph)
    assert len(cands) == 1
    ring = cands[0]
    assert ring.is_bridge_closed

    flow_val = s_flow(ring, graph)
    assert flow_val is not None
    assert 0.85 < flow_val <= 1.0

    scored = score_ring(ring, graph)
    # Only interior firm P-002 appears in retained_pct_by_firm; entry P-001 (in=0) and exit P-003 (out=0) excluded
    assert list(scored.retained_pct_by_firm.keys()) == ["P-002"]
    assert scored.retained_pct_by_firm["P-002"] == pytest.approx(1.5, abs=0.01)


# ---------------------------------------------------------------------------
# 6. Lesson 6: test_l6_evidence_has_numbers
# ---------------------------------------------------------------------------
def test_l6_evidence_has_numbers() -> None:
    """Evidence sentences contain concrete Indian-formatted rupee figures, percentages, HSNs, vehicles, and cities."""
    p1 = _make_party("P-001", pincode="400001", city="Mumbai", directors=["Kiran Rao"], bank_account_hash="BH-1")
    p2 = _make_party("P-002", pincode="110001", city="Delhi", directors=["Kiran Rao"], bank_account_hash="BH-1")
    p3 = _make_party("P-003", pincode="560001", city="Bengaluru")
    parties = {p.party_id: p for p in (p1, p2, p3)}

    invs = [
        _make_invoice("INV-1", p1, p2, amount_paise=10_00_000_00, invoice_date="2025-01-10", hsn_sac="72081000"),
        _make_invoice("INV-2", p2, p3, amount_paise=9_90_000_00, invoice_date="2025-01-11", hsn_sac="72081000"),
        _make_invoice("INV-3", p3, p1, amount_paise=9_85_000_00, invoice_date="2025-01-12", hsn_sac="72081000"),
    ]
    ewbs = [
        EvidenceDoc(
            doc_id="D-EWB-1",
            doc_type=DocType.EWAY_BILL,
            doc_no="EWB-1001",
            doc_date="2025-01-10",
            seller_id="P-001",
            buyer_id="P-002",
            invoice_id="INV-1",
            source=DocSource.GSTN_EWAY_FIXTURE,
            verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
            total_paise=10_00_000_00,
            vehicle_no="MH12AB1234",
            transporter_id="TRANS-99",
            from_pincode="400001",
            to_pincode="110001",
            generated_at="2025-01-10T08:00:00Z",
        ),
        EvidenceDoc(
            doc_id="D-EWB-2",
            doc_type=DocType.EWAY_BILL,
            doc_no="EWB-1002",
            doc_date="2025-01-10",
            seller_id="P-002",
            buyer_id="P-003",
            invoice_id="INV-2",
            source=DocSource.GSTN_EWAY_FIXTURE,
            verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
            total_paise=9_90_000_00,
            vehicle_no="MH12AB1234",
            transporter_id="TRANS-99",
            from_pincode="110001",
            to_pincode="560001",
            generated_at="2025-01-10T11:00:00Z",
        ),
    ]

    ctx = _make_context(invs[0], parties, invs, evidence=ewbs)
    results = run(ctx)
    assert len(results) == 1
    res = results[0]
    assert res.check == "network.ring"
    assert res.status == CheckStatus.WARN
    assert res.severity == Severity.HIGH

    joined = " ".join(res.evidence)
    # Must contain Indian formatted rupee figures (₹29,75,000 or ₹10,00,000)
    assert "₹29,75,000" in joined
    assert "₹10,00,000" in joined
    # Must contain percentages, HSN code, vehicle number, cities, timestamps
    assert "%" in joined
    assert "72081000" in joined
    assert "MH12AB1234" in joined
    assert "Mumbai" in joined
    assert "Delhi" in joined
    assert "2025-01-10T08:00:00Z" in joined
    assert "km/h" in joined

    for line in res.evidence:
        assert not line.strip().lower().startswith("score:")
        assert any(ch.isdigit() for ch in line)


# ---------------------------------------------------------------------------
# 7. Lesson 7: test_l7_all_invoices_per_edge
# ---------------------------------------------------------------------------
def test_l7_all_invoices_per_edge() -> None:
    """Directed multigraph stores ALL invoices on edge (u, v) without overwriting earlier ones."""
    p1 = _make_party("P-001")
    p2 = _make_party("P-002")
    p3 = _make_party("P-003")

    # Three distinct invoices on P-001 -> P-002 totaling ₹10,00,000
    inv1a = _make_invoice("INV-1A", p1, p2, amount_paise=3_00_000_00, invoice_date="2025-01-01")
    inv1b = _make_invoice("INV-1B", p1, p2, amount_paise=3_00_000_00, invoice_date="2025-01-02")
    inv1c = _make_invoice("INV-1C", p1, p2, amount_paise=4_00_000_00, invoice_date="2025-01-03")
    inv2 = _make_invoice("INV-2", p2, p3, amount_paise=9_90_000_00, invoice_date="2025-01-04")
    inv3 = _make_invoice("INV-3", p3, p1, amount_paise=9_85_000_00, invoice_date="2025-01-05")

    graph = TradeGraph(parties=[p1, p2, p3], invoices=[inv1a, inv1b, inv1c, inv2, inv3])
    edge_invs = graph.invoices_on_edge("P-001", "P-002")
    assert [i.invoice_id for i in edge_invs] == ["INV-1A", "INV-1B", "INV-1C"]
    assert graph.edge_total_paise("P-001", "P-002") == 10_00_000_00

    cands = find_cycles(graph)
    assert len(cands) == 1
    scored = score_ring(cands[0], graph)
    # Total exposure across the ring includes all 3 invoices on P-001 -> P-002
    assert scored.ring_exposure_paise == 29_75_000_00
    # And P-002's inflow is ₹10,00,000 vs ₹9,90,000 outflow (1.0% retention delta)
    assert scored.retained_pct_by_firm["P-002"] == pytest.approx(1.0, abs=0.01)


# ---------------------------------------------------------------------------
# 8. Incremental Corporate Ring: test_corporate_ring_found_incrementally
# ---------------------------------------------------------------------------
def test_corporate_ring_found_incrementally() -> None:
    """When invoices arrive one at a time, IncrementalRingEngine discovers a corporate-bridge-closed ring
    as soon as the path between >=2-link related endpoints completes, and re-scores when outside volume arrives.
    """
    # P-1 and P-4 share 2 corporate links (shared_director + same_bank_hash)
    p1 = _make_party("P-001", directors=["Suresh Patel"], bank_account_hash="CORP-BANK-77")
    p2 = _make_party("P-002")
    p3 = _make_party("P-003")
    p4 = _make_party("P-004", directors=["Suresh Patel"], bank_account_hash="CORP-BANK-77")
    p_outside = _make_party("P-999")

    inv1 = _make_invoice("INV-1", p1, p2, amount_paise=10_00_000_00, invoice_date="2025-01-01")
    inv2 = _make_invoice("INV-2", p2, p3, amount_paise=9_90_000_00, invoice_date="2025-01-03")
    inv3 = _make_invoice("INV-3", p3, p4, amount_paise=9_80_000_00, invoice_date="2025-01-05")

    engine = IncrementalRingEngine(parties=[p1, p2, p3, p4, p_outside])
    assert engine.add_invoice(inv1) == []
    assert engine.add_invoice(inv2) == []

    # Third invoice completes P-001 -> P-002 -> P-003 -> P-004 with corporate bridge (P-004, P-001)
    rings_after_3 = engine.add_invoice(inv3)
    assert len(rings_after_3) == 1
    r = rings_after_3[0]
    assert r.ring_id == "R01"
    assert r.bridges == (("P-004", "P-001"),)
    assert r.signals["s_isolation"] == pytest.approx(1.0)
    score_before_outside = r.score

    # Adding an outside trade invoice touching P-002 automatically re-scores s_isolation and ring score
    outside_inv = _make_invoice("INV-OUT-1", p2, p_outside, amount_paise=30_00_000_00, invoice_date="2025-01-06")
    rings_after_outside = engine.add_invoice(outside_inv)
    assert len(rings_after_outside) == 1
    r_updated = rings_after_outside[0]
    assert r_updated.signals["s_isolation"] < 0.55
    assert r_updated.score < score_before_outside


# ---------------------------------------------------------------------------
# 9. Budget Enforcement: test_budget_raises
# ---------------------------------------------------------------------------
def test_budget_raises() -> None:
    """find_cycles raises BudgetExceeded loudly when max_cycles or max_steps is exceeded."""
    # Complete directed graph on 7 nodes has many cycles of length <= 7
    nodes = [f"P-{i:03d}" for i in range(1, 8)]
    dense_adj = {u: [v for v in nodes if v != u] for u in nodes}

    with pytest.raises(BudgetExceeded, match="max_cycles"):
        find_cycles(dense_adj, max_hops=7, max_cycles=5, max_steps=2_000_000)

    with pytest.raises(BudgetExceeded, match="max_steps"):
        find_cycles(dense_adj, max_hops=7, max_cycles=50_000, max_steps=10)


# ---------------------------------------------------------------------------
# 10. Determinism: test_rerun_byte_identical
# ---------------------------------------------------------------------------
def test_rerun_byte_identical() -> None:
    """Re-running network.check.run across shuffled party/invoice input orderings produces byte-identical JSON."""
    p1 = _make_party("P-001", directors=["Meera Nair"], bank_account_hash="BH-SHARED")
    p2 = _make_party("P-002")
    p3 = _make_party("P-003", directors=["Meera Nair"], bank_account_hash="BH-SHARED")
    p4 = _make_party("P-004")
    parties_list = [p1, p2, p3, p4]

    invoices = [
        _make_invoice("INV-1", p1, p2, amount_paise=15_00_000_00, invoice_date="2025-01-02"),
        _make_invoice("INV-2", p2, p3, amount_paise=14_85_000_00, invoice_date="2025-01-04"),
        _make_invoice("INV-3", p3, p1, amount_paise=14_70_000_00, invoice_date="2025-01-06"),
        _make_invoice("INV-4", p2, p4, amount_paise=5_00_000_00, invoice_date="2025-01-07"),
    ]

    ctx_base = _make_context(invoices[0], {p.party_id: p for p in parties_list}, invoices)
    res_base = run(ctx_base)
    bytes_base = canonical_json([r.model_dump(mode="json") for r in res_base], allow_floats=True).encode("utf-8")

    rng = random.Random(99)
    for _ in range(8):
        shuffled_parties = list(parties_list)
        shuffled_invs = list(invoices)
        rng.shuffle(shuffled_parties)
        rng.shuffle(shuffled_invs)
        ctx_perm = _make_context(
            invoices[0],
            {p.party_id: p for p in shuffled_parties},
            shuffled_invs,
        )
        res_perm = run(ctx_perm)
        bytes_perm = canonical_json([r.model_dump(mode="json") for r in res_perm], allow_floats=True).encode("utf-8")
        assert bytes_perm == bytes_base


# ---------------------------------------------------------------------------
# 11. Legitimacy Guard: test_legit_loop_guard_lowers_score
# ---------------------------------------------------------------------------
def test_legit_loop_guard_lowers_score() -> None:
    """Legitimacy guard applies multiplier < 1.0 (in [0.35, 0.65]) and lowers ring score when:
    - Manufacturers transform input HSN chapter to a different output HSN chapter (72 -> 8708 -> 8703),
    - Trading/distribution firms earn > 10% margins,
    - Long-standing pairs (> 180 days history) have substantial outside trade (s_isolation < 0.35).
    """
    p_steel = _make_party("P-001", industry_class=IndustryClass.MANUFACTURING)
    p_parts = _make_party("P-002", industry_class=IndustryClass.MANUFACTURING)
    p_auto = _make_party("P-003", industry_class=IndustryClass.DISTRIBUTION)
    p_external = _make_party("P-900", industry_class=IndustryClass.TRADING)

    invs = [
        # Ring hops with HSN transformation (7208 -> 8708 -> 8703), > 10% margins, and > 180 day history
        _make_invoice("INV-1", p_steel, p_parts, amount_paise=10_00_000_00, invoice_date="2024-05-01", hsn_sac="72081000"),
        _make_invoice("INV-2", p_parts, p_auto, amount_paise=12_00_000_00, invoice_date="2024-08-15", hsn_sac="87089900"),
        _make_invoice("INV-3", p_auto, p_steel, amount_paise=14_00_000_00, invoice_date="2024-12-20", hsn_sac="87032310"),
        # Substantial outside trade so s_isolation < 0.35
        _make_invoice("INV-EXT-1", p_steel, p_external, amount_paise=50_00_000_00, invoice_date="2024-06-10", hsn_sac="72081000"),
        _make_invoice("INV-EXT-2", p_parts, p_external, amount_paise=50_00_000_00, invoice_date="2024-09-10", hsn_sac="87089900"),
    ]

    graph = TradeGraph(parties=[p_steel, p_parts, p_auto, p_external], invoices=invs)
    cands = find_cycles(graph)
    assert len(cands) == 1
    scored = score_ring(cands[0], graph)

    assert 0.35 <= scored.guard_multiplier <= 0.65
    assert len(scored.guard_reasons) >= 2
    unguarded_score, _ = score_signals(scored.signals, guard_multiplier=1.0)
    assert scored.score < unguarded_score
    assert scored.tier == Tier.LOW


# ---------------------------------------------------------------------------
# Additional coverage tests for canonicalization, signals, and edge cases
# ---------------------------------------------------------------------------
def test_party_canonicalization_merges_and_preserves_attributes() -> None:
    """canonicalize_parties merges exact GSTIN and exact normalized (name, address) without fuzzy matching."""
    p1 = _make_party(
        "P-001",
        name="Apex Metals Pvt. Ltd.",
        address="Plot 14, MIDC Indl. Rd., Pune",
        directors=["Sunil Gavaskar"],
        phone="9820011111",
        bank_account_hash="BH-A",
    )
    # Different GSTIN, but normalized name ("apex metals private limited") and address ("plot 14 midc industrial road pune") match
    p2 = _make_party(
        "P-002",
        name="APEX METALS PRIVATE LIMITED",
        address="Plot 14, MIDC Industrial Road, Pune!",
        pan="ZZZZZ9999Z",
        directors=["Kapil Dev"],
        phone="9820022222",
        bank_account_hash="BH-B",
    )
    # Exact GSTIN match with p1
    p3 = _make_party(
        "P-003",
        name="Apex Metals Unit II",
        gstin=p1.gstin,
        address="Different Address",
        directors=["Ravi Shastri"],
    )

    res = canonicalize_parties([p1, p2, p3])
    assert res.merges_count == 2
    assert len(res.parties) == 1
    assert res.alias_map == {"P-001": "P-001", "P-002": "P-001", "P-003": "P-001"}

    cp = res.parties["P-001"]
    assert set(cp.directors) == {"Sunil Gavaskar", "Kapil Dev", "Ravi Shastri"}
    assert set(cp.gstins) == {p1.gstin, p2.gstin}
    assert set(cp.bank_account_hashes) == {"BH-A", "BH-B", "BANK-HASH-P-003"}


def test_self_loops_and_malformed_records_dropped() -> None:
    """TradeGraph drops and counts self-loops and malformed invoices."""
    p1 = _make_party("P-001")
    p2 = _make_party("P-002", gstin=p1.gstin)  # canonicalizes to P-001

    self_inv = _make_invoice("INV-SELF", p1, p2, amount_paise=5_00_000_00)
    graph = TradeGraph(parties=[p1, p2], invoices=[self_inv, None, {"bad": "record"}])

    assert graph.self_loops_dropped == 1
    assert graph.malformed_dropped == 2
    assert graph.all_invoices() == []


def test_single_signal_capped_at_medium_tier() -> None:
    """When < 2 non-abstained signals exist, score is capped at 0.59 (Tier.MEDIUM)."""
    only_one = {
        "s_flow": None,
        "s_product": None,
        "s_timing": None,
        "s_isolation": 1.0,
        "s_movement": None,
        "s_related": None,
        "s_transform": None,
        "s_rapid": None,
    }
    score_val, tier = score_signals(only_one, guard_multiplier=1.0)
    assert score_val == pytest.approx(0.59)
    assert tier == Tier.MEDIUM


def test_movement_grn_transit_post_grn_and_evidence_weight() -> None:
    """s_movement detects transit faster than distance_km/60kmh, post-GRN generation, single transporter,
    and weights by doc.evidence_weight while abstaining when no e-way bills exist.
    """
    p1 = _make_party("P-001", pincode="400001", city="Mumbai")
    p2 = _make_party("P-002", pincode="110001", city="Delhi")
    p3 = _make_party("P-003", pincode="700001", city="Kolkata")
    invs = [
        _make_invoice("INV-1", p1, p2, invoice_date="2025-01-10"),
        _make_invoice("INV-2", p2, p3, invoice_date="2025-01-11"),
        _make_invoice("INV-3", p3, p1, invoice_date="2025-01-12"),
    ]

    # 1. No e-way bills -> s_movement is None
    g_empty = TradeGraph(parties=[p1, p2, p3], invoices=invs)
    ring = find_cycles(g_empty)[0]
    assert s_movement(ring, g_empty) is None

    # 2. Post-GRN e-way bill + fast transit + single transporter weighted by verification_status
    ewb_confirmed = EvidenceDoc(
        doc_id="D-EWB-1",
        doc_type=DocType.EWAY_BILL,
        doc_no="EWB-1",
        doc_date="2025-01-12",
        seller_id="P-001",
        buyer_id="P-002",
        invoice_id="INV-1",
        source=DocSource.GSTN_EWAY_FIXTURE,
        verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
        total_paise=10_00_000_00,
        transporter_id="TRANS-SINGLE",
        from_pincode="400001",
        to_pincode="110001",
        distance_km=1400,
        generated_at="2025-01-12T10:00:00Z",
    )
    grn_early = EvidenceDoc(
        doc_id="D-GRN-1",
        doc_type=DocType.GRN,
        doc_no="GRN-1",
        doc_date="2025-01-10",
        seller_id="P-001",
        buyer_id="P-002",
        invoice_id="INV-1",
        source=DocSource.BUYER_PORTAL,
        verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
        total_paise=10_00_000_00,
    )
    ewb_unverified = ewb_confirmed.model_copy(
        update={
            "doc_id": "D-EWB-UNVER",
            "verification_status": VerificationStatus.UNVERIFIED,
        }
    )

    g_conf = TradeGraph(parties=[p1, p2, p3], invoices=invs, evidence_docs=[ewb_confirmed, grn_early])
    g_unver = TradeGraph(parties=[p1, p2, p3], invoices=invs, evidence_docs=[ewb_unverified, grn_early])

    score_conf = s_movement(ring, g_conf)
    score_unver = s_movement(ring, g_unver)
    assert score_conf is not None and score_unver is not None
    assert score_conf > score_unver
    assert score_conf == pytest.approx(0.95)
    assert score_unver == pytest.approx(0.95 * 0.4)


def test_related_transform_and_rapid_signals() -> None:
    """s_related weights shared attributes by 1/log2(1+n), s_transform flags manufacturing same-HSN markup,
    and s_rapid flags ring elapsed days < minimum payment_terms_days.
    """
    p1 = _make_party(
        "P-001",
        industry_class=IndustryClass.MANUFACTURING,
        directors=["Shared Dir"],
    )
    p2 = _make_party(
        "P-002",
        industry_class=IndustryClass.MANUFACTURING,
        directors=["Shared Dir"],
    )
    p3 = _make_party("P-003", industry_class=IndustryClass.TRADING)

    invs = [
        _make_invoice("INV-1", p1, p2, amount_paise=10_00_000_00, invoice_date="2025-01-01", payment_terms_days=60, hsn_sac="72081000"),
        # P-002 is MANUFACTURING and sells exact same 8-digit HSN 72081000 at 5% markup
        _make_invoice("INV-2", p2, p3, amount_paise=10_50_000_00, invoice_date="2025-01-03", payment_terms_days=60, hsn_sac="72081000"),
        _make_invoice("INV-3", p3, p1, amount_paise=10_50_000_00, invoice_date="2025-01-06", payment_terms_days=60, hsn_sac="72081000"),
    ]
    g = TradeGraph(parties=[p1, p2, p3], invoices=invs)
    ring = find_cycles(g)[0]

    rel = s_related(ring, g)
    tr = s_transform(ring, g)
    rap = s_rapid(ring, g)
    assert rel is not None and rel == pytest.approx(1.0 / 1.5849625, rel=1e-3)
    assert tr is not None and tr >= 0.88
    assert rap is not None and rap > 0.90


def test_check_run_clean_invoice_has_pass_and_no_raw_candidate_counts() -> None:
    """network.check.run returns PASS/INFO when no ring touches ctx.invoice and omits raw candidate counts."""
    p1 = _make_party("P-001")
    p2 = _make_party("P-002")
    inv = _make_invoice("INV-CLEAN-1", p1, p2, amount_paise=5_00_000_00)
    ctx = _make_context(inv, {"P-001": p1, "P-002": p2}, [inv])

    results = run(ctx)
    assert len(results) == 1
    res = results[0]
    assert res.check == "network.ring"
    assert res.status == CheckStatus.PASS
    assert res.severity == Severity.INFO
    assert res.details["ring"] is None
    assert res.details["tier"] == Tier.LOW.value
    assert "candidate_count" not in res.details
    assert "raw_candidates" not in res.details


"""Tests for Stage 2b deduplication and consortium registry (`dedup/`)."""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from contract.context import CaseContext, InMemoryHistoryView
from contract.models import (
    BuyerAcceptance,
    CheckStatus,
    DocType,
    FinancingRequest,
    IndustryClass,
    Invoice,
    InvoiceVersion,
    LineItem,
    Party,
    PaymentRecord,
    Reservation,
    ReservationState,
    Severity,
    VersionKind,
    make_gstin,
)
from dedup.check import compute_outstanding_paise, run as run_dedup
from dedup.identity import classify_version, compute_invoice_identity
from dedup.near_dup import aggressive_normalize_inv_no, check_near_duplicate
from dedup.registry import (
    ConsortiumRegistry,
    sign_buyer_acceptance,
    sign_financing_request,
)


def _make_keypair() -> tuple[Ed25519PrivateKey, str]:
    priv = Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes_raw().hex()
    return priv, pub_hex


def _make_party_pair(seller_pub_hex: str | None = None) -> dict[str, Party]:
    seller = Party(
        party_id="P-SELLER-01",
        name="Suryoday Auto Parts Pvt Ltd",
        gstin=make_gstin("27", "AABCS1429B"),
        pan="AABCS1429B",
        state_code="27",
        industry_class=IndustryClass.MANUFACTURING,
        address="Plot 10, Chakan",
        pincode="410501",
        city="Pune",
        phone="+91-20-27650000",
        bank_account_hash="1" * 64,
        public_key_hex=seller_pub_hex,
    )
    buyer = Party(
        party_id="P-BUYER-01",
        name="Deccan Motors Ltd",
        gstin=make_gstin("27", "AABCB5678C"),
        pan="AABCB5678C",
        state_code="27",
        industry_class=IndustryClass.MANUFACTURING,
        address="Worli",
        pincode="400018",
        city="Mumbai",
        phone="+91-22-24980000",
        bank_account_hash="2" * 64,
    )
    return {seller.party_id: seller, buyer.party_id: buyer}


def _make_invoice(
    *,
    invoice_id: str = "INV-101",
    invoice_no: str = "INV-2026-001",
    invoice_date: str = "2026-01-10",
    grand_total_paise: int = 80_000_000,  # ₹8,00,000
    hsn_sac: str = "87089900",
    description: str = "Forged Steel Crankshaft Assembly",
    linked_invoice_id: str | None = None,
) -> Invoice:
    parties = _make_party_pair()
    li = LineItem(
        line_no=1,
        description=description,
        hsn_sac=hsn_sac,
        is_service=False,
        qty=100,
        unit="NOS",
        rate_paise=grand_total_paise // 100,
        taxable_paise=grand_total_paise,
        cgst_bps=0,
        sgst_bps=0,
        igst_bps=0,
        tax_paise=0,
        total_paise=grand_total_paise,
    )
    return Invoice(
        invoice_id=invoice_id,
        invoice_no=invoice_no,
        financial_year="2025-26",
        doc_type=DocType.INVOICE,
        invoice_date=invoice_date,
        due_date="2026-03-11",
        seller_id="P-SELLER-01",
        seller_gstin=parties["P-SELLER-01"].gstin,
        buyer_id="P-BUYER-01",
        buyer_gstin=parties["P-BUYER-01"].gstin,
        place_of_supply_state="27",
        payment_terms_days=60,
        line_items=[li],
        subtotal_paise=grand_total_paise,
        cgst_paise=0,
        sgst_paise=0,
        igst_paise=0,
        total_tax_paise=0,
        grand_total_paise=grand_total_paise,
        linked_invoice_id=linked_invoice_id,
    )


def test_identity_hmac_and_version_classifications():
    inv1 = _make_invoice(invoice_id="INV-101", grand_total_paise=80_000_000)
    id_a = compute_invoice_identity(inv1.seller_gstin, inv1.financial_year, inv1.doc_type, "  inv-2026-001 ")
    id_b = compute_invoice_identity(inv1.seller_gstin, inv1.financial_year, "INVOICE", "INV-2026-001")
    assert id_a == id_b

    ver1 = InvoiceVersion(
        version_id="V-101",
        invoice_id=inv1.invoice_id,
        version_no=1,
        kind=VersionKind.INITIAL,
        invoice_identity=id_a,
        file_sha256="11" * 32,
        field_root="22" * 32,
        created_at="2026-01-10T10:00:00Z",
    )

    # 1. Resubmission (same identity + same file_sha256): dedup.identity passes, dedup.registry stops duplicate
    ver_resub = InvoiceVersion(
        version_id="V-102",
        invoice_id=inv1.invoice_id,
        version_no=2,
        kind=VersionKind.RESUBMISSION,
        invoice_identity=id_a,
        file_sha256="11" * 32,
        field_root="22" * 32,
        created_at="2026-01-10T11:00:00Z",
    )
    active_rsv = Reservation(
        reservation_id="RSV-EXISTING-01",
        invoice_identity=id_a,
        request_id="REQ-100",
        lender_id="L2",
        seller_id="P-SELLER-01",
        amount_paise=60_000_000,
        state=ReservationState.RESERVED,
        created_at="2026-01-10T10:00:00Z",
        expires_at="2026-01-11T10:00:00Z",
    )
    req_resub = FinancingRequest(
        request_id="REQ-101",
        invoice_id=inv1.invoice_id,
        version_id=ver_resub.version_id,
        seller_id="P-SELLER-01",
        buyer_id="P-BUYER-01",
        lender_id="L1",
        requested_amount_paise=60_000_000,
        tenor_days=60,
        seller_signature_hex="aa" * 32,
        submitted_at="2026-01-10T11:05:00Z",
    )
    ctx_resub = CaseContext(
        request=req_resub,
        invoice=inv1,
        version=ver_resub,
        parties=_make_party_pair(),
        history=InMemoryHistoryView(
            invoices=[inv1],
            versions=[ver1],
            reservations=[active_rsv],
        ),
    )
    resub_results = {r.check: r for r in run_dedup(ctx_resub)}
    assert resub_results["dedup.identity"].status == CheckStatus.PASS
    assert resub_results["dedup.identity"].details["classification"] == "resubmission"
    assert resub_results["dedup.registry"].status == CheckStatus.FAIL

    # 2. Unexplained change (same identity + different file_sha256, no credit/debit note) -> WARN with field diff
    inv_changed = _make_invoice(invoice_id="INV-102", grand_total_paise=100_000_000)  # ₹10,00,000
    ver_changed = InvoiceVersion(
        version_id="V-103",
        invoice_id=inv_changed.invoice_id,
        version_no=2,
        kind=VersionKind.UNEXPLAINED_CHANGE,
        invoice_identity=id_a,
        file_sha256="99" * 32,
        field_root="88" * 32,
        created_at="2026-01-11T10:00:00Z",
    )
    ctx_unexplained = CaseContext(
        request=req_resub,
        invoice=inv_changed,
        version=ver_changed,
        parties=_make_party_pair(),
        history=InMemoryHistoryView(
            invoices=[inv1],
            versions=[ver1],
        ),
    )
    unexp_res = classify_version(ctx_unexplained)
    assert unexp_res.status == CheckStatus.WARN
    assert unexp_res.severity == Severity.HIGH
    joined_ev = " ".join(unexp_res.evidence)
    assert "unexplained change" in joined_ev.lower()
    assert "₹8,00,000" in joined_ev and "₹10,00,000" in joined_ev

    # 3. Extraction revision (same file_sha256, updated reading) -> PASS (Severity.INFO), no flag
    ver_ext = InvoiceVersion(
        version_id="V-104",
        invoice_id=inv1.invoice_id,
        version_no=2,
        kind=VersionKind.EXTRACTION_REVISION,
        invoice_identity=id_a,
        file_sha256="11" * 32,
        field_root="77" * 32,
        created_at="2026-01-11T12:00:00Z",
    )
    ctx_ext = CaseContext(
        request=req_resub,
        invoice=inv1,
        version=ver_ext,
        parties=_make_party_pair(),
        history=InMemoryHistoryView(
            invoices=[inv1],
            versions=[ver1],
        ),
    )
    ext_res = classify_version(ctx_ext)
    assert ext_res.status == CheckStatus.PASS
    assert ext_res.severity == Severity.INFO
    assert ext_res.details["classification"] == "extraction_revision"


def test_near_duplicate_flags_homoglyph_and_ignores_legitimate_second_invoice():
    assert aggressive_normalize_inv_no("INV-2026-O0l") == aggressive_normalize_inv_no("BILL/2026/00I")

    inv_orig = _make_invoice(
        invoice_id="INV-201",
        invoice_no="INV-2026-001",
        invoice_date="2026-01-10",
        grand_total_paise=80_000_000,
        hsn_sac="87089900",
        description="Forged Steel Crankshaft Assembly",
    )
    # Near-duplicate: homoglyph invoice_no, +0.5% amount, 3 days later, identical line items
    inv_neardup = _make_invoice(
        invoice_id="INV-202",
        invoice_no="INV/2026/O0l",
        invoice_date="2026-01-13",
        grand_total_paise=80_400_000,
        hsn_sac="87089900",
        description="Forged Steel Crankshaft Assembly",
    )
    ver_nd = InvoiceVersion(
        version_id="V-202",
        invoice_id=inv_neardup.invoice_id,
        version_no=1,
        kind=VersionKind.INITIAL,
        invoice_identity="c" * 64,
        file_sha256="33" * 32,
        field_root="44" * 32,
        created_at="2026-01-13T10:00:00Z",
    )
    req_nd = FinancingRequest(
        request_id="REQ-202",
        invoice_id=inv_neardup.invoice_id,
        version_id=ver_nd.version_id,
        seller_id="P-SELLER-01",
        buyer_id="P-BUYER-01",
        lender_id="L1",
        requested_amount_paise=60_000_000,
        tenor_days=60,
        seller_signature_hex="aa" * 32,
        submitted_at="2026-01-13T10:05:00Z",
    )
    ctx_nd = CaseContext(
        request=req_nd,
        invoice=inv_neardup,
        version=ver_nd,
        parties=_make_party_pair(),
        history=InMemoryHistoryView(invoices=[inv_orig]),
    )
    res_nd = check_near_duplicate(ctx_nd)
    assert res_nd.status == CheckStatus.WARN
    assert res_nd.severity == Severity.HIGH
    assert "INV-201" in res_nd.refs

    # Legitimate second invoice between same parties (different items and amount) -> PASS
    inv_legit = _make_invoice(
        invoice_id="INV-203",
        invoice_no="INV-2026-002",
        invoice_date="2026-01-12",
        grand_total_paise=45_000_000,
        hsn_sac="73181500",
        description="High Tensile Hex Bolts M12",
    )
    ctx_legit = CaseContext(
        request=req_nd,
        invoice=inv_legit,
        version=ver_nd,
        parties=_make_party_pair(),
        history=InMemoryHistoryView(invoices=[inv_orig]),
    )
    res_legit = check_near_duplicate(ctx_legit)
    assert res_legit.status == CheckStatus.PASS


def test_registry_lifecycle_settled_stale_expired_reject_and_idempotent_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    reg = ConsortiumRegistry(tmp_path / "lifecycle.db")
    seller_priv, seller_pub = _make_keypair()
    buyer_priv, buyer_pub = _make_keypair()

    inv_id_1 = "id_settled_" + "1" * 53
    req1 = FinancingRequest(
        request_id="REQ-S1",
        invoice_id="INV-S1",
        version_id="V-S1",
        seller_id="P-SELLER-01",
        buyer_id="P-BUYER-01",
        lender_id="L1",
        requested_amount_paise=50_000_000,
        tenor_days=60,
        seller_signature_hex="00" * 32,
        submitted_at="2026-01-10T10:00:00Z",
    )
    req1 = req1.model_copy(update={"seller_signature_hex": sign_financing_request(req1, seller_priv)})

    # Reserve -> confirm (idempotent retry) -> settle -> attempt refinance_settled
    r1 = reg.reserve(
        op_id="op-res-s1",
        request=req1,
        invoice_identity=inv_id_1,
        seller_public_key_hex=seller_pub,
        now_iso="2026-01-10T10:00:00Z",
        outstanding_paise=80_000_000,
    )
    assert r1["ok"] is True
    rsv_id = r1["reservation_id"]

    acc1 = BuyerAcceptance(
        acceptance_id="ACC-S1",
        invoice_id="INV-S1",
        version_id="V-S1",
        buyer_id="P-BUYER-01",
        decision="accept",
        accepted_amount_paise=80_000_000,
        accepted_at="2026-01-10T12:00:00Z",
        signature_hex="00" * 32,
    )
    acc1 = acc1.model_copy(update={"signature_hex": sign_buyer_acceptance(acc1, buyer_priv)})

    c1 = reg.confirm(
        op_id="op-conf-s1",
        reservation_id=rsv_id,
        buyer_acceptance=acc1,
        outstanding_paise=80_000_000,
        buyer_public_key_hex=buyer_pub,
        now_iso="2026-01-10T12:00:00Z",
    )
    assert c1["ok"] is True
    assert c1["state"] == "financed"
    assert reg.get_allocated_paise("INV-S1") == 50_000_000

    # Retried confirm with same op_id finances ONCE (does not double-allocate)
    c1_retry = reg.confirm(
        op_id="op-conf-s1",
        reservation_id=rsv_id,
        buyer_acceptance=acc1,
        outstanding_paise=80_000_000,
        buyer_public_key_hex=buyer_pub,
        now_iso="2026-01-10T12:05:00Z",
    )
    assert c1_retry == c1
    assert reg.get_allocated_paise("INV-S1") == 50_000_000

    # Settle locks invoice_identity permanently
    s1 = reg.settle(op_id="op-settle-s1", reservation_id=rsv_id, now_iso="2026-03-01T10:00:00Z")
    assert s1["ok"] is True
    assert s1["state"] == "settled"

    req_refinance = req1.model_copy(update={"request_id": "REQ-S2", "lender_id": "L2"})
    req_refinance = req_refinance.model_copy(
        update={"seller_signature_hex": sign_financing_request(req_refinance, seller_priv)}
    )
    monkeypatch.setenv("TESSERA_DISCLOSE_LENDER", "0")
    r_refinance = reg.reserve(
        op_id="op-res-s2",
        request=req_refinance,
        invoice_identity=inv_id_1,
        seller_public_key_hex=seller_pub,
        now_iso="2026-03-02T10:00:00Z",
        outstanding_paise=80_000_000,
    )
    assert r_refinance["ok"] is False
    assert r_refinance["state"] == "settled"
    assert r_refinance["reason"] == "already_settled"
    assert r_refinance["holder_lender"] is None

    # Stale 'reserved' row past expires_at does NOT block a new reserve even WITHOUT expire_sweep running
    inv_id_stale = "id_stale_" + "2" * 55
    r_stale = reg.reserve(
        op_id="op-stale-1",
        request=req1,
        invoice_identity=inv_id_stale,
        seller_public_key_hex=seller_pub,
        now_iso="2026-01-01T00:00:00Z",
        ttl_hours=24,
        outstanding_paise=80_000_000,
    )
    assert r_stale["ok"] is True
    # 48 hours later (past expires_at), without calling expire_sweep:
    r_fresh = reg.reserve(
        op_id="op-stale-2",
        request=req_refinance,
        invoice_identity=inv_id_stale,
        seller_public_key_hex=seller_pub,
        now_iso="2026-01-03T00:00:00Z",
        ttl_hours=24,
        outstanding_paise=80_000_000,
    )
    assert r_fresh["ok"] is True
    assert r_fresh["lender_id"] == "L2"

    # Expired reservation cannot confirm
    inv_id_exp = "id_exp_" + "3" * 57
    r_exp = reg.reserve(
        op_id="op-exp-1",
        request=req1,
        invoice_identity=inv_id_exp,
        seller_public_key_hex=seller_pub,
        now_iso="2026-01-01T00:00:00Z",
        ttl_hours=24,
        outstanding_paise=80_000_000,
    )
    assert r_exp["ok"] is True
    c_exp = reg.confirm(
        op_id="op-exp-confirm",
        reservation_id=r_exp["reservation_id"],
        buyer_acceptance=acc1,
        outstanding_paise=80_000_000,
        buyer_public_key_hex=buyer_pub,
        now_iso="2026-01-05T00:00:00Z",
    )
    assert c_exp["ok"] is False
    assert c_exp["state"] == "expired"

    # Reject auto-cancel frees the invoice for a subsequent reservation
    inv_id_rej = "id_rej_" + "4" * 57
    r_rej1 = reg.reserve(
        op_id="op-rej-1",
        request=req1,
        invoice_identity=inv_id_rej,
        seller_public_key_hex=seller_pub,
        now_iso="2026-01-10T10:00:00Z",
        outstanding_paise=80_000_000,
    )
    assert r_rej1["ok"] is True
    reg.reject(op_id="op-rej-cancel", reservation_id=r_rej1["reservation_id"], now_iso="2026-01-10T11:00:00Z")
    r_rej2 = reg.reserve(
        op_id="op-rej-2",
        request=req_refinance,
        invoice_identity=inv_id_rej,
        seller_public_key_hex=seller_pub,
        now_iso="2026-01-10T12:00:00Z",
        outstanding_paise=80_000_000,
    )
    assert r_rej2["ok"] is True

    # Above outstanding refused & invalid/missing seller signature refused
    r_above = reg.reserve(
        op_id="op-above-out",
        request=req1,
        invoice_identity="id_cap_" + "5" * 57,
        seller_public_key_hex=seller_pub,
        now_iso="2026-01-10T10:00:00Z",
        outstanding_paise=10_000_000,  # less than requested 50_000_000
    )
    assert r_above["ok"] is False
    assert r_above["reason"] == "above_outstanding"

    req_bad_sig = req1.model_copy(update={"request_id": "REQ-BADSIG", "seller_signature_hex": ""})
    r_badsig = reg.reserve(
        op_id="op-bad-sig",
        request=req_bad_sig,
        invoice_identity="id_sig_" + "6" * 57,
        seller_public_key_hex=seller_pub,
        now_iso="2026-01-10T10:00:00Z",
        outstanding_paise=80_000_000,
    )
    assert r_badsig["ok"] is False
    assert r_badsig["reason"] == "invalid_signature"

    assert len(reg.audit_events) >= 6
    reg.close()


def test_outstanding_capacity_calculation_with_payments_credit_notes_and_allocations():
    inv = _make_invoice(invoice_id="INV-CAP-01", grand_total_paise=100_000_000)  # ₹10,00,000
    cn = _make_invoice(
        invoice_id="INV-CN-01",
        invoice_no="CN-2026-001",
        grand_total_paise=20_000_000,
        linked_invoice_id="INV-CAP-01",
    ).model_copy(update={"doc_type": DocType.CREDIT_NOTE})
    pmt = PaymentRecord(
        payment_id="PMT-01",
        invoice_id="INV-CAP-01",
        seller_id="P-SELLER-01",
        buyer_id="P-BUYER-01",
        amount_paise=30_000_000,
        due_date="2026-03-11",
        paid_date="2026-01-12",
    )
    ver = InvoiceVersion(
        version_id="V-CAP-01",
        invoice_id=inv.invoice_id,
        version_no=1,
        kind=VersionKind.INITIAL,
        invoice_identity="d" * 64,
        file_sha256="55" * 32,
        field_root="66" * 32,
        created_at="2026-01-12T10:00:00Z",
    )
    # Remaining capacity = 100M - 30M (payment) - 20M (credit note) - 10M (allocation) = 40M paise
    # Requesting 50M paise (> 40M) must fail dedup.registry with above_outstanding
    req = FinancingRequest(
        request_id="REQ-CAP-01",
        invoice_id=inv.invoice_id,
        version_id=ver.version_id,
        seller_id="P-SELLER-01",
        buyer_id="P-BUYER-01",
        lender_id="L1",
        requested_amount_paise=50_000_000,
        tenor_days=60,
        seller_signature_hex="aa" * 32,
        submitted_at="2026-01-12T10:05:00Z",
    )
    ctx = CaseContext(
        request=req,
        invoice=inv,
        version=ver,
        parties=_make_party_pair(),
        history=InMemoryHistoryView(
            invoices=[inv, cn],
            versions=[ver],
            payments=[pmt],
            allocations_paise={"INV-CAP-01": 10_000_000},
        ),
    )
    assert compute_outstanding_paise(ctx) == 40_000_000
    results = {r.check: r for r in run_dedup(ctx)}
    assert results["dedup.registry"].status == CheckStatus.FAIL
    assert results["dedup.registry"].details["reason"] == "above_outstanding"


def test_concurrent_reserve_200_iterations(tmp_path: Path):
    """2 threads x reserve concurrency test across 200 iterations on a SQLite WAL file DB:
    every single iteration has at least 1 winner and never 2 winners (`len(winners) == 1`).
    """
    db_file = tmp_path / "concurrent_registry.db"
    registry = ConsortiumRegistry(db_file)

    try:
        for i in range(200):
            inv_identity = hashlib.sha256(f"concurrent-invoice-{i}".encode("utf-8")).hexdigest()
            barrier = threading.Barrier(2)

            req_l1 = FinancingRequest(
                request_id=f"REQ-{i}-L1",
                invoice_id=f"INV-{i}",
                version_id=f"V-{i}",
                seller_id="P-SELLER-01",
                buyer_id="P-BUYER-01",
                lender_id="L1",
                requested_amount_paise=50_000_000,
                tenor_days=60,
                seller_signature_hex="aa" * 32,
                submitted_at="2026-01-10T10:00:00Z",
            )
            req_l2 = FinancingRequest(
                request_id=f"REQ-{i}-L2",
                invoice_id=f"INV-{i}",
                version_id=f"V-{i}",
                seller_id="P-SELLER-01",
                buyer_id="P-BUYER-01",
                lender_id="L2",
                requested_amount_paise=50_000_000,
                tenor_days=60,
                seller_signature_hex="bb" * 32,
                submitted_at="2026-01-10T10:00:00Z",
            )

            def _worker(req: FinancingRequest, op_key: str) -> dict:
                barrier.wait()
                return registry.reserve(
                    op_id=op_key,
                    request=req,
                    invoice_identity=inv_identity,
                    now_iso="2026-01-10T10:00:00Z",
                    outstanding_paise=80_000_000,
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                f1 = pool.submit(_worker, req_l1, f"op-{i}-L1")
                f2 = pool.submit(_worker, req_l2, f"op-{i}-L2")
                res1 = f1.result()
                res2 = f2.result()

            winners = [r for r in (res1, res2) if r.get("ok") is True]
            losers = [r for r in (res1, res2) if r.get("ok") is False]

            assert len(winners) == 1, f"Iteration {i}: expected exactly 1 winner, got {len(winners)} ({res1}, {res2})"
            assert len(losers) == 1
            assert losers[0]["held_by_other"] is True
            assert losers[0]["state"] == "reserved"
    finally:
        registry.close()

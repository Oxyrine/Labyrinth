"""Tests for Stage 2a cross-document matching (`match/`)."""

from __future__ import annotations

import pytest

from contract.context import CaseContext, InMemoryHistoryView
from contract.models import (
    CheckSource,
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
    VerificationStatus,
    VersionKind,
    make_gstin,
)
from match.check import run as run_match


def _make_parties() -> dict[str, Party]:
    seller_gstin = make_gstin("27", "AABCS1429B")
    buyer_gstin = make_gstin("27", "AABCB5678C")
    seller = Party(
        party_id="P-SELLER-01",
        name="Apex Precision Components Pvt Ltd",
        gstin=seller_gstin,
        pan="AABCS1429B",
        state_code="27",
        industry_class=IndustryClass.MANUFACTURING,
        address="Plot 14, MIDC Chakan",
        pincode="410501",
        city="Pune",
        phone="+91-20-27654321",
        bank_account_hash="b" * 64,
    )
    buyer = Party(
        party_id="P-BUYER-01",
        name="Bharat Heavy Motors Ltd",
        gstin=buyer_gstin,
        pan="AABCB5678C",
        state_code="27",
        industry_class=IndustryClass.MANUFACTURING,
        address="Tower A, Worli",
        pincode="400018",
        city="Mumbai",
        phone="+91-22-24987654",
        bank_account_hash="c" * 64,
    )
    return {seller.party_id: seller, buyer.party_id: buyer}


def _make_goods_line(
    qty: int = 100,
    rate_paise: int = 80_000,
    hsn_sac: str = "87089900",
    description: str = "CNC Machined Transmission Shafts",
) -> LineItem:
    taxable = qty * rate_paise
    cgst = (taxable * 900) // 10000
    sgst = (taxable * 900) // 10000
    tax = cgst + sgst
    return LineItem(
        line_no=1,
        description=description,
        hsn_sac=hsn_sac,
        is_service=False,
        qty=qty,
        unit="NOS",
        rate_paise=rate_paise,
        taxable_paise=taxable,
        cgst_bps=900,
        sgst_bps=900,
        igst_bps=0,
        tax_paise=tax,
        total_paise=taxable + tax,
    )


def _make_base_goods_case(
    *,
    inv_qty: int = 100,
    inv_rate_paise: int = 80_000,
    po_qty: int = 100,
    po_rate_paise: int = 80_000,
    grn_qty: int = 100,
    include_grn: bool = True,
    include_ewb: bool = True,
    po_date: str = "2026-01-05",
    grn_date: str = "2026-01-10",
    inv_date: str = "2026-01-12",
    prior_invoices: list[Invoice] | None = None,
    metadata: dict | None = None,
    ewb_verification: VerificationStatus = VerificationStatus.INDEPENDENTLY_CONFIRMED,
) -> CaseContext:
    parties = _make_parties()
    seller = parties["P-SELLER-01"]
    buyer = parties["P-BUYER-01"]

    inv_li = _make_goods_line(qty=inv_qty, rate_paise=inv_rate_paise)
    po_li = _make_goods_line(qty=po_qty, rate_paise=po_rate_paise)
    grn_li = _make_goods_line(qty=grn_qty, rate_paise=po_rate_paise)

    inv_cgst = (inv_li.taxable_paise * 900) // 10000
    inv_sgst = (inv_li.taxable_paise * 900) // 10000

    invoice = Invoice(
        invoice_id="INV-2026-001",
        invoice_no="INV/2026/001",
        financial_year="2025-26",
        doc_type=DocType.INVOICE,
        invoice_date=inv_date,
        due_date="2026-03-13",
        seller_id=seller.party_id,
        seller_gstin=seller.gstin,
        buyer_id=buyer.party_id,
        buyer_gstin=buyer.gstin,
        place_of_supply_state="27",
        po_id="D-PO-001",
        contract_id="D-CNT-001",
        payment_terms_days=60,
        line_items=[inv_li],
        subtotal_paise=inv_li.taxable_paise,
        cgst_paise=inv_cgst,
        sgst_paise=inv_sgst,
        igst_paise=0,
        total_tax_paise=inv_li.tax_paise,
        grand_total_paise=inv_li.total_paise,
    )

    version = InvoiceVersion(
        version_id="V-2026-001",
        invoice_id=invoice.invoice_id,
        version_no=1,
        kind=VersionKind.INITIAL,
        invoice_identity="a" * 64,
        file_sha256="f" * 64,
        field_root="e" * 64,
        created_at="2026-01-12T10:00:00Z",
    )

    request = FinancingRequest(
        request_id="REQ-2026-001",
        invoice_id=invoice.invoice_id,
        version_id=version.version_id,
        seller_id=seller.party_id,
        buyer_id=buyer.party_id,
        lender_id="L1",
        requested_amount_paise=(invoice.grand_total_paise * 80) // 100,
        tenor_days=60,
        seller_signature_hex="aa" * 32,
        submitted_at="2026-01-12T10:05:00Z",
    )

    po_doc = EvidenceDoc(
        doc_id="D-PO-001",
        doc_type=DocType.PURCHASE_ORDER,
        doc_no="PO/2026/001",
        doc_date=po_date,
        seller_id=seller.party_id,
        buyer_id=buyer.party_id,
        invoice_id=invoice.invoice_id,
        po_id="D-PO-001",
        source=DocSource.BUYER_PORTAL,
        verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
        line_items=[po_li],
        total_paise=po_li.total_paise,
        valid_from="2026-01-01",
        valid_until="2026-06-30",
        payment_terms_days=60,
    )

    contract_doc = EvidenceDoc(
        doc_id="D-CNT-001",
        doc_type=DocType.CONTRACT,
        doc_no="MSA/2026/01",
        doc_date="2026-01-01",
        seller_id=seller.party_id,
        buyer_id=buyer.party_id,
        invoice_id=invoice.invoice_id,
        contract_id="D-CNT-001",
        source=DocSource.BUYER_PORTAL,
        verification_status=VerificationStatus.SIGNATURE_CHECKED,
        valid_from="2026-01-01",
        valid_until="2026-12-31",
        payment_terms_days=60,
        rate_caps_paise={"87089900": po_rate_paise},
    )

    evidence: list[EvidenceDoc] = [po_doc, contract_doc]

    if include_grn:
        grn_doc = EvidenceDoc(
            doc_id="D-GRN-001",
            doc_type=DocType.GRN,
            doc_no="GRN/2026/001",
            doc_date=grn_date,
            seller_id=seller.party_id,
            buyer_id=buyer.party_id,
            invoice_id=invoice.invoice_id,
            po_id="D-PO-001",
            source=DocSource.ERP_WEBHOOK,
            verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
            line_items=[grn_li],
            total_paise=grn_li.total_paise,
        )
        evidence.append(grn_doc)

    if include_ewb:
        ewb_doc = EvidenceDoc(
            doc_id="D-EWB-001",
            doc_type=DocType.EWAY_BILL,
            doc_no="EWB-27100998877",
            doc_date=grn_date,
            seller_id=seller.party_id,
            buyer_id=buyer.party_id,
            invoice_id=invoice.invoice_id,
            po_id="D-PO-001",
            source=DocSource.GSTN_EWAY_FIXTURE,
            verification_status=ewb_verification,
            total_paise=invoice.grand_total_paise,
            vehicle_no="MH12AB1234",
            from_pincode="410501",
            to_pincode="400018",
            distance_km=150,
            generated_at=f"{grn_date}T08:00:00Z",
        )
        evidence.append(ewb_doc)

    all_invs = list(prior_invoices or []) + [invoice]
    history = InMemoryHistoryView(
        invoices=all_invs,
        versions=[version],
        evidence_docs=evidence,
        metadata=metadata or {},
    )

    return CaseContext(
        request=request,
        invoice=invoice,
        version=version,
        parties=parties,
        evidence=evidence,
        history=history,
    )


def _failing_checks(results):
    return [r.check for r in results if r.status == CheckStatus.FAIL]


def test_clean_goods_passes_all_applicable_checks():
    ctx = _make_base_goods_case(ewb_verification=VerificationStatus.SIGNATURE_CHECKED)
    results = run_match(ctx)
    by_id = {r.check: r for r in results}

    assert _failing_checks(results) == []
    assert by_id["match.services.acceptance"].status == CheckStatus.NOT_APPLICABLE
    assert by_id["match.services.acceptance"].severity == Severity.INFO
    assert by_id["match.three_way.grn_presence"].status == CheckStatus.PASS
    assert by_id["match.ewaybill"].status == CheckStatus.PASS
    assert by_id["match.ewaybill"].evidence_weight == pytest.approx(0.7)
    assert any("simplified rules table" in line for line in by_id["match.ewaybill"].evidence)


@pytest.mark.parametrize(
    "scenario,expected_failed_check",
    [
        ("mm_qty", "match.three_way.qty"),
        ("mm_rate", "match.three_way.rate"),
        ("mm_grn_missing", "match.three_way.grn_presence"),
        ("mm_date_order", "match.ordering.dates"),
        ("mm_over_invoice", "match.over_invoicing"),
        ("mm_ewb_missing", "match.ewaybill"),
    ],
)
def test_six_mm_scenarios_yield_exactly_one_failing_check(scenario: str, expected_failed_check: str):
    if scenario == "mm_qty":
        # +10% quantity mismatch (110 vs 100); must fail ONLY match.three_way.qty
        ctx = _make_base_goods_case(inv_qty=110, po_qty=100, grn_qty=100)
    elif scenario == "mm_rate":
        # +5% unit rate mismatch (84,000 vs 80,000); must fail ONLY match.three_way.rate
        ctx = _make_base_goods_case(inv_rate_paise=84_000, po_rate_paise=80_000)
    elif scenario == "mm_grn_missing":
        # GRN missing while PO is present; must fail ONLY match.three_way.grn_presence
        ctx = _make_base_goods_case(include_grn=False)
    elif scenario == "mm_date_order":
        # GRN date (2026-01-15) after invoice date (2026-01-12); must fail ONLY match.ordering.dates
        ctx = _make_base_goods_case(po_date="2026-01-05", grn_date="2026-01-15", inv_date="2026-01-12")
    elif scenario == "mm_over_invoice":
        # Prior partial invoice of 60 units + current partial invoice of 60 units = 120 > PO 100 units
        parties = _make_parties()
        prior_li = _make_goods_line(qty=60, rate_paise=80_000)
        prior_inv = Invoice(
            invoice_id="INV-2026-000",
            invoice_no="INV/2026/000",
            financial_year="2025-26",
            doc_type=DocType.INVOICE,
            invoice_date="2026-01-08",
            due_date="2026-03-09",
            seller_id="P-SELLER-01",
            seller_gstin=parties["P-SELLER-01"].gstin,
            buyer_id="P-BUYER-01",
            buyer_gstin=parties["P-BUYER-01"].gstin,
            place_of_supply_state="27",
            po_id="D-PO-001",
            payment_terms_days=60,
            line_items=[prior_li],
            subtotal_paise=prior_li.taxable_paise,
            cgst_paise=(prior_li.taxable_paise * 900) // 10000,
            sgst_paise=(prior_li.taxable_paise * 900) // 10000,
            igst_paise=0,
            total_tax_paise=prior_li.tax_paise,
            grand_total_paise=prior_li.total_paise,
        )
        ctx = _make_base_goods_case(
            inv_qty=60,
            po_qty=100,
            grn_qty=60,
            prior_invoices=[prior_inv],
        )
    elif scenario == "mm_ewb_missing":
        # Goods invoice > ₹50,000 missing EWB; must fail ONLY match.ewaybill
        ctx = _make_base_goods_case(include_ewb=False)
    else:
        raise ValueError(f"Unknown scenario {scenario}")

    results = run_match(ctx)
    failed = _failing_checks(results)
    assert failed == [expected_failed_check], (
        f"Scenario {scenario} expected ONLY [{expected_failed_check}] to fail, got {failed}"
    )


def test_clean_services_passes_with_goods_checks_not_applicable():
    parties = _make_parties()
    seller = parties["P-SELLER-01"]
    buyer = parties["P-BUYER-01"]

    taxable = 6_000_000  # ₹60,000 (> ₹50,000 threshold, but pure service SAC!)
    cgst = (taxable * 900) // 10000
    sgst = (taxable * 900) // 10000
    svc_li = LineItem(
        line_no=1,
        description="Cloud ERP Integration Milestone 1",
        hsn_sac="998314",
        is_service=True,
        qty=1,
        unit="LOT",
        rate_paise=taxable,
        taxable_paise=taxable,
        cgst_bps=900,
        sgst_bps=900,
        igst_bps=0,
        tax_paise=cgst + sgst,
        total_paise=taxable + cgst + sgst,
    )

    invoice = Invoice(
        invoice_id="INV-SVC-001",
        invoice_no="SVC/2026/001",
        financial_year="2025-26",
        doc_type=DocType.INVOICE,
        invoice_date="2026-01-20",
        due_date="2026-03-21",
        seller_id=seller.party_id,
        seller_gstin=seller.gstin,
        buyer_id=buyer.party_id,
        buyer_gstin=buyer.gstin,
        place_of_supply_state="27",
        contract_id="D-CNT-SVC-01",
        payment_terms_days=60,
        line_items=[svc_li],
        subtotal_paise=svc_li.taxable_paise,
        cgst_paise=cgst,
        sgst_paise=sgst,
        igst_paise=0,
        total_tax_paise=svc_li.tax_paise,
        grand_total_paise=svc_li.total_paise,
    )

    version = InvoiceVersion(
        version_id="V-SVC-001",
        invoice_id=invoice.invoice_id,
        version_no=1,
        kind=VersionKind.INITIAL,
        invoice_identity="b" * 64,
        file_sha256="d" * 64,
        field_root="c" * 64,
        created_at="2026-01-20T10:00:00Z",
    )

    request = FinancingRequest(
        request_id="REQ-SVC-001",
        invoice_id=invoice.invoice_id,
        version_id=version.version_id,
        seller_id=seller.party_id,
        buyer_id=buyer.party_id,
        lender_id="L1",
        requested_amount_paise=5_000_000,
        tenor_days=60,
        seller_signature_hex="bb" * 32,
        submitted_at="2026-01-20T10:05:00Z",
    )

    contract_doc = EvidenceDoc(
        doc_id="D-CNT-SVC-01",
        doc_type=DocType.CONTRACT,
        doc_no="SOW/2026/01",
        doc_date="2026-01-01",
        seller_id=seller.party_id,
        buyer_id=buyer.party_id,
        invoice_id=invoice.invoice_id,
        contract_id="D-CNT-SVC-01",
        source=DocSource.BUYER_PORTAL,
        verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
        line_items=[svc_li],
        total_paise=svc_li.total_paise,
        valid_from="2026-01-01",
        valid_until="2026-12-31",
        payment_terms_days=60,
        rate_caps_paise={"998314": taxable},
    )

    sa_doc = EvidenceDoc(
        doc_id="D-SA-001",
        doc_type=DocType.SERVICE_ACCEPTANCE,
        doc_no="SA/2026/001",
        doc_date="2026-01-18",
        seller_id=seller.party_id,
        buyer_id=buyer.party_id,
        invoice_id=invoice.invoice_id,
        contract_id="D-CNT-SVC-01",
        source=DocSource.BUYER_PORTAL,
        verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
        line_items=[svc_li],
        total_paise=svc_li.total_paise,
        milestone_id="MS-01",
    )

    ctx = CaseContext(
        request=request,
        invoice=invoice,
        version=version,
        parties=parties,
        evidence=[contract_doc, sa_doc],
        history=InMemoryHistoryView(
            invoices=[invoice],
            versions=[version],
            evidence_docs=[contract_doc, sa_doc],
        ),
    )

    results = run_match(ctx)
    by_id = {r.check: r for r in results}

    assert _failing_checks(results) == []
    assert by_id["match.three_way.grn_presence"].status == CheckStatus.NOT_APPLICABLE
    assert by_id["match.three_way.grn_presence"].severity == Severity.INFO
    assert by_id["match.ewaybill"].status == CheckStatus.NOT_APPLICABLE
    assert by_id["match.ewaybill"].severity == Severity.INFO
    assert by_id["match.services.acceptance"].status == CheckStatus.PASS


def test_docs_unavailable_returns_unavailable_never_pass():
    ctx = _make_base_goods_case(metadata={"docs_unavailable": True})
    results = run_match(ctx)
    by_id = {r.check: r for r in results}

    for check_id in (
        "match.three_way.parties",
        "match.three_way.hsn",
        "match.three_way.qty",
        "match.three_way.rate",
        "match.three_way.total",
        "match.three_way.grn_presence",
        "match.ordering.dates",
        "match.contract.terms",
        "match.over_invoicing",
        "match.ewaybill",
    ):
        assert by_id[check_id].status == CheckStatus.UNAVAILABLE
        assert by_id[check_id].severity == Severity.MEDIUM


def test_tolerances_within_bounds_and_exempt_ewaybill(monkeypatch: pytest.MonkeyPatch):
    # +2% qty (102 vs 100) and +1% rate (80,800 vs 80,000) should pass three-way qty/rate
    ctx_within = _make_base_goods_case(
        inv_qty=102,
        po_qty=100,
        grn_qty=100,
        inv_rate_paise=80_000,
        po_rate_paise=80_000,
    )
    results_within = {r.check: r for r in run_match(ctx_within)}
    assert results_within["match.three_way.qty"].status == CheckStatus.PASS
    assert results_within["match.three_way.total"].status == CheckStatus.PASS

    # Amount <= ₹50,000 -> match.ewaybill is NOT_APPLICABLE
    ctx_low_val = _make_base_goods_case(
        inv_qty=10,
        po_qty=10,
        grn_qty=10,
        inv_rate_paise=3_000,
        po_rate_paise=3_000,
        include_ewb=False,
    )
    results_low_val = {r.check: r for r in run_match(ctx_low_val)}
    assert results_low_val["match.ewaybill"].status == CheckStatus.NOT_APPLICABLE

    # TESSERA_MATCH_LIVE=0 sets source to FIXTURE
    monkeypatch.setenv("TESSERA_MATCH_LIVE", "0")
    fixture_res = run_match(ctx_low_val)
    assert all(r.source == CheckSource.FIXTURE for r in fixture_res)

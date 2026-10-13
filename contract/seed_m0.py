"""Generate M0 seed fixtures (clean_goods, bad_arith, and check_examples.json)."""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contract.models import (
    CheckResult,
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

DEMO_HMAC_KEY = b"tessera-consortium-demo-hmac-key-v1"


def compute_identity(gstin: str, fy: str, doc_type: str, invoice_no: str) -> str:
    norm = "|".join(
        part.strip().upper()
        for part in (gstin, fy, doc_type, " ".join(invoice_no.strip().upper().split()))
    )
    return hmac.new(DEMO_HMAC_KEY, norm.encode("utf-8"), hashlib.sha256).hexdigest()


def build_seed_fixtures() -> None:
    fixtures_dir = ROOT / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    seller_gstin = make_gstin("27", "AABCS1429B", "1")
    buyer_gstin = make_gstin("27", "AABCB5812K", "1")

    seller = Party(
        party_id="P-0001",
        name="Sahyadri Precision Castings Pvt Ltd",
        gstin=seller_gstin,
        pan="AABCS1429B",
        cin="U27100MH2018PTC304112",
        state_code="27",
        industry_class=IndustryClass.MANUFACTURING,
        address="Plot 14, Chakan MIDC Phase II",
        pincode="410501",
        city="Pune",
        phone="+91-20-27891001",
        bank_account_hash=hashlib.sha256(b"HDFC0001201:50200019283711").hexdigest(),
        directors=["DIN-08112201", "DIN-08112202"],
    )
    buyer = Party(
        party_id="P-0002",
        name="Deccan Drivetrain Assemblies Ltd",
        gstin=buyer_gstin,
        pan="AABCB5812K",
        cin="U29304MH2015PLC268901",
        state_code="27",
        industry_class=IndustryClass.MANUFACTURING,
        address="B-22, Ranjangaon Industrial Area",
        pincode="412220",
        city="Pune",
        phone="+91-20-27892002",
        bank_account_hash=hashlib.sha256(b"ICIC0000039:003905012938").hexdigest(),
        directors=["DIN-09001101", "DIN-09001102"],
    )

    line_clean = LineItem(
        line_no=1,
        description="CNC Machined Differential Housing Castings",
        hsn_sac="87084000",
        is_service=False,
        qty=500,
        unit="NOS",
        rate_paise=169492,
        taxable_paise=84746000,
        cgst_bps=900,
        sgst_bps=900,
        igst_bps=0,
        tax_paise=15254280,
        total_paise=100000280,
    )
    # Exact ₹10,00,000 invoice line for clean_goods:
    # qty 500, rate ₹1,600.00 (160_000 paise) -> taxable ₹8,00,000 (80_000_000 paise)
    # Wait: if grand total is ₹10,00,000 (100_000_000 paise) or taxable + tax = 100_000_000:
    # Let's use qty=1000, rate=80000 (₹800.00) -> taxable=80_000_000, CGST 9% (7_200_000) + SGST 9% (7_200_000) = 14_400_000 -> total 94_400_000
    # Or for exact 100_000_000 paise (₹10,00,000) grand total with 0% or 5% tax:
    # With 5% GST (CGST 250 bps + SGST 250 bps) on ₹10,00,000? Or let's make requested_amount_paise=100_000_000 on a ₹10,00,000 invoice!
    # Wait: if qty=800, rate_paise=100000 (₹1,000) -> taxable=80_000_000, CGST 1250 bps? Standard GST slabs: 0, 500, 1200, 1800, 2800.
    #Wait: what if taxable = 100_000_000 (₹10,00,000) and CGST=9_000_000, SGST=9_000_000 -> grand_total=118_000_000, requested_amount_paise=100_000_000?
    # Or what if pricing is computed on `requested_amount_paise` (or `invoice.grand_total_paise` = 100_000_000)?
    # Let's check: if line_1 has taxable=80_000_000 + tax 14_400_000 = 94_400_000 and line_2 has taxable=5_000_000 + tax 600_000 (12% GST) = 5_600_000, then subtotal=85_000_000 + total_tax=15_000_000 = grand_total_paise = 100_000_000 (exact ₹10,00,000)!
    line1 = LineItem(
        line_no=1,
        description="CNC Machined Differential Housing Castings",
        hsn_sac="87084000",
        is_service=False,
        qty=1000,
        unit="NOS",
        rate_paise=80000,
        taxable_paise=80000000,
        cgst_bps=900,
        sgst_bps=900,
        igst_bps=0,
        tax_paise=14400000,
        total_paise=94400000,
    )
    line2 = LineItem(
        line_no=2,
        description="Forged Industrial Fastener Kit",
        hsn_sac="73181500",
        is_service=False,
        qty=500,
        unit="SET",
        rate_paise=10000,
        taxable_paise=5000000,
        cgst_bps=600,
        sgst_bps=600,
        igst_bps=0,
        tax_paise=600000,
        total_paise=5600000,
    )

    inv_clean = Invoice(
        invoice_id="INV-FX-0001",
        invoice_no="SPC/26-27/0101",
        financial_year="2026-27",
        doc_type=DocType.INVOICE,
        invoice_date="2026-08-12",
        due_date="2026-10-11",
        seller_id=seller.party_id,
        seller_gstin=seller.gstin,
        buyer_id=buyer.party_id,
        buyer_gstin=buyer.gstin,
        place_of_supply_state="27",
        is_export_or_sez=False,
        po_id="D-PO-0001",
        contract_id="D-CNT-0001",
        payment_terms_days=60,
        line_items=[line1, line2],
        subtotal_paise=85000000,
        cgst_paise=7500000,
        sgst_paise=7500000,
        igst_paise=0,
        total_tax_paise=15000000,
        grand_total_paise=100000000,
    )

    ver_clean = InvoiceVersion(
        version_id="V-FX-0001-1",
        invoice_id=inv_clean.invoice_id,
        version_no=1,
        kind=VersionKind.INITIAL,
        invoice_identity=compute_identity(
            inv_clean.seller_gstin, inv_clean.financial_year, inv_clean.doc_type.value, inv_clean.invoice_no
        ),
        file_sha256=hashlib.sha256(b"fx-clean-goods-pdf-v1").hexdigest(),
        field_root=hashlib.sha256(b"fx-clean-goods-field-root-v1").hexdigest(),
        created_at="2026-08-12T10:00:00Z",
    )

    req_clean = FinancingRequest(
        request_id="REQ-FX-0001",
        invoice_id=inv_clean.invoice_id,
        version_id=ver_clean.version_id,
        seller_id=seller.party_id,
        buyer_id=buyer.party_id,
        lender_id="L1",
        requested_amount_paise=100000000,
        tenor_days=60,
        seller_signature_hex="00" * 64,
        submitted_at="2026-08-12T10:05:00Z",
    )

    ev_clean = [
        EvidenceDoc(
            doc_id="D-PO-0001",
            doc_type=DocType.PURCHASE_ORDER,
            doc_no="PO/DDA/26/0089",
            doc_date="2026-08-01",
            seller_id=seller.party_id,
            buyer_id=buyer.party_id,
            invoice_id=inv_clean.invoice_id,
            po_id="D-PO-0001",
            contract_id="D-CNT-0001",
            source=DocSource.BUYER_PORTAL,
            verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
            line_items=[line1, line2],
            total_paise=100000000,
            valid_from="2026-08-01",
            valid_until="2026-09-30",
            payment_terms_days=60,
        ),
        EvidenceDoc(
            doc_id="D-GRN-0001",
            doc_type=DocType.GRN,
            doc_no="GRN/DDA/26/0144",
            doc_date="2026-08-10",
            seller_id=seller.party_id,
            buyer_id=buyer.party_id,
            invoice_id=inv_clean.invoice_id,
            po_id="D-PO-0001",
            source=DocSource.ERP_WEBHOOK,
            verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
            line_items=[line1, line2],
            total_paise=100000000,
        ),
        EvidenceDoc(
            doc_id="D-CNT-0001",
            doc_type=DocType.CONTRACT,
            doc_no="MSA/DDA-SPC/2026",
            doc_date="2026-04-01",
            seller_id=seller.party_id,
            buyer_id=buyer.party_id,
            invoice_id=inv_clean.invoice_id,
            contract_id="D-CNT-0001",
            source=DocSource.BUYER_PORTAL,
            verification_status=VerificationStatus.SIGNATURE_CHECKED,
            total_paise=500000000,
            valid_from="2026-04-01",
            valid_until="2027-03-31",
            payment_terms_days=60,
            rate_caps_paise={"87084000": 82000, "73181500": 10500},
        ),
        EvidenceDoc(
            doc_id="D-EWB-0001",
            doc_type=DocType.EWAY_BILL,
            doc_no="EWB-271009283741",
            doc_date="2026-08-09",
            seller_id=seller.party_id,
            buyer_id=buyer.party_id,
            invoice_id=inv_clean.invoice_id,
            source=DocSource.GSTN_EWAY_FIXTURE,
            verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
            total_paise=100000000,
            vehicle_no="MH14HG4421",
            transporter_id="27AABCT9911K1Z5",
            from_pincode="410501",
            to_pincode="412220",
            distance_km=42,
            generated_at="2026-08-09T08:30:00Z",
            valid_until="2026-08-11T23:59:59Z",
        ),
    ]

    clean_dir = fixtures_dir / "clean_goods"
    clean_dir.mkdir(parents=True, exist_ok=True)
    (clean_dir / "case.json").write_text(
        json.dumps(
            {
                "case_id": "clean_goods",
                "description": "Clean goods trade with full 3-way match, valid e-way bill, and strong buyer history.",
                "request": req_clean.model_dump(mode="json"),
                "invoice": inv_clean.model_dump(mode="json"),
                "version": ver_clean.model_dump(mode="json"),
                "parties": {
                    seller.party_id: seller.model_dump(mode="json"),
                    buyer.party_id: buyer.model_dump(mode="json"),
                },
                "evidence": [d.model_dump(mode="json") for d in ev_clean],
                "history": {
                    "invoices": [inv_clean.model_dump(mode="json")],
                    "versions": [ver_clean.model_dump(mode="json")],
                    "payments": [
                        {
                            "payment_id": "PAY-0001",
                            "invoice_id": "INV-HIST-0001",
                            "seller_id": seller.party_id,
                            "buyer_id": buyer.party_id,
                            "amount_paise": 90000000,
                            "due_date": "2026-07-01",
                            "paid_date": "2026-06-29",
                            "dpd": 0,
                            "disputed": False,
                        }
                    ],
                    "evidence_docs": [d.model_dump(mode="json") for d in ev_clean],
                    "reservations": [],
                    "allocations_paise": {},
                    "metadata": {"case_id": "clean_goods"},
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (clean_dir / "expected.json").write_text(
        json.dumps(
            {
                "case_id": "clean_goods",
                "expected_outcome": "conditionally_approved",
                "failing_checks": [],
                "warn_checks": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    # Seed fixture 2: bad_arith (planted arithmetic error on invoice total)
    inv_bad = inv_clean.model_copy(
        update={
            "invoice_id": "INV-FX-0002",
            "invoice_no": "SPC/26-27/0102",
            "grand_total_paise": 105000000,  # Planted ₹50,000 arithmetic inflation over subtotal+tax
        }
    )
    ver_bad = ver_clean.model_copy(
        update={
            "version_id": "V-FX-0002-1",
            "invoice_id": inv_bad.invoice_id,
            "invoice_identity": compute_identity(
                inv_bad.seller_gstin, inv_bad.financial_year, inv_bad.doc_type.value, inv_bad.invoice_no
            ),
            "file_sha256": hashlib.sha256(b"fx-bad-arith-pdf-v1").hexdigest(),
        }
    )
    req_bad = req_clean.model_copy(
        update={
            "request_id": "REQ-FX-0002",
            "invoice_id": inv_bad.invoice_id,
            "version_id": ver_bad.version_id,
            "requested_amount_paise": 105000000,
        }
    )
    bad_dir = fixtures_dir / "bad_arith"
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "case.json").write_text(
        json.dumps(
            {
                "case_id": "bad_arith",
                "description": "Invoice grand total ₹10,50,000 disagrees with subtotal ₹8,50,000 + tax ₹1,50,000 = ₹10,00,000.",
                "request": req_bad.model_dump(mode="json"),
                "invoice": inv_bad.model_dump(mode="json"),
                "version": ver_bad.model_dump(mode="json"),
                "parties": {
                    seller.party_id: seller.model_dump(mode="json"),
                    buyer.party_id: buyer.model_dump(mode="json"),
                },
                "evidence": [
                    d.model_copy(update={"invoice_id": inv_bad.invoice_id}).model_dump(mode="json")
                    for d in ev_clean
                ],
                "history": {
                    "invoices": [inv_bad.model_dump(mode="json")],
                    "versions": [ver_bad.model_dump(mode="json")],
                    "payments": [],
                    "evidence_docs": [],
                    "reservations": [],
                    "allocations_paise": {},
                    "metadata": {"case_id": "bad_arith"},
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (bad_dir / "expected.json").write_text(
        json.dumps(
            {
                "case_id": "bad_arith",
                "expected_outcome": "reject",
                "failing_checks": ["extract.arith_total"],
                "warn_checks": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    # One example CheckResult of each of the 5 statuses (pass, warn, fail, unavailable, not_applicable)
    examples = [
        CheckResult(
            check="extract.gstin_checksum",
            status=CheckStatus.PASS,
            severity=Severity.HARD,
            source=CheckSource.LIVE,
            evidence=[
                f"Seller GSTIN {seller.gstin} and buyer GSTIN {buyer.gstin} passed mod-36 checksum verification."
            ],
            refs=[inv_clean.invoice_id],
            verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
        ),
        CheckResult(
            check="network.ring",
            status=CheckStatus.WARN,
            severity=Severity.HIGH,
            source=CheckSource.LIVE,
            evidence=[
                "₹4,02,00,000 invoiced in and ₹4,05,00,000 invoiced out across 5 firms in Ring R01 (0.7% retained).",
                "Vehicle MH12AB1234 recorded on EWB-271001 in Pune at 09:10 and EWB-331002 in Chennai at 12:05 (1,190 km in 2.9 h).",
            ],
            refs=["R01", inv_clean.invoice_id],
            verification_status=VerificationStatus.SIGNATURE_CHECKED,
        ),
        CheckResult(
            check="extract.arith_total",
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            source=CheckSource.LIVE,
            evidence=[
                "Printed grand total ₹10,50,000 exceeds subtotal ₹8,50,000 plus total tax ₹1,50,000 (₹10,00,000) by ₹50,000."
            ],
            refs=[inv_bad.invoice_id],
            verification_status=VerificationStatus.UNVERIFIED,
        ),
        CheckResult(
            check="match.ewaybill",
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=CheckSource.FIXTURE,
            evidence=[
                "E-way bill portal endpoint timed out after 2,000 ms for invoice INV-FX-0001 (₹10,00,000 > ₹50,000 threshold); routing to human review."
            ],
            refs=[inv_clean.invoice_id],
        ),
        CheckResult(
            check="match.three_way.grn_presence",
            status=CheckStatus.NOT_APPLICABLE,
            severity=Severity.INFO,
            source=CheckSource.LIVE,
            evidence=[
                "Invoice contains only SAC 998314 IT consulting services; physical Goods Receipt Note (GRN) check does not apply."
            ],
            refs=["INV-FX-0003"],
        ),
    ]
    (fixtures_dir / "check_examples.json").write_text(
        json.dumps([ex.model_dump(mode="json") for ex in examples], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    build_seed_fixtures()
    print("Generated M0 seed fixtures: clean_goods, bad_arith, check_examples.json")

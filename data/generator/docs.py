"""Sector flow table driving 12 months of legitimate trades, documents, and >=30 legit_circular loops."""

from __future__ import annotations

import hashlib
import hmac
import random
from dataclasses import dataclass, field
from typing import Any

from contract.models import (
    BuyerAcceptance,
    DocSource,
    DocType,
    EvidenceDoc,
    FinancingRequest,
    IndustryClass,
    Invoice,
    InvoiceVersion,
    LineItem,
    Party,
    PaymentRecord,
    Reservation,
    VerificationStatus,
    VersionKind,
    canonical_json,
    canonical_sha256,
)
from data.generator.cities import road_distance_km
from data.generator.world import GeneratedWorld, sign_with_party_key


def _sign_canonical(seed: int, party_id: str, model: Any) -> str:
    """Sign canonical JSON of a model with its signature fields stripped (matches registry verification)."""
    raw = {
        k: v
        for k, v in model.model_dump(mode="json").items()
        if k not in ("seller_signature_hex", "signature_hex")
    }
    return sign_with_party_key(seed, party_id, canonical_json(raw, allow_floats=True).encode("utf-8"))

DEMO_HMAC_KEY = b"tessera-consortium-demo-hmac-key-v1"

SECTOR_FLOW_TABLE: list[dict[str, Any]] = [
    {
        "stage": "raw_metal",
        "hsn_sac": "72081000",
        "description": "Prime Hot Rolled Steel Coils IS2062",
        "unit": "MT",
        "base_rate_paise": 80000,
        "is_service": False,
        "cgst_bps": 900,
        "sgst_bps": 900,
        "igst_bps": 1800,
    },
    {
        "stage": "cast_components",
        "hsn_sac": "87084000",
        "description": "CNC Machined Differential Housing Castings",
        "unit": "NOS",
        "base_rate_paise": 96000,
        "is_service": False,
        "cgst_bps": 900,
        "sgst_bps": 900,
        "igst_bps": 1800,
    },
    {
        "stage": "fasteners",
        "hsn_sac": "73181500",
        "description": "High-Tensile Forged Industrial Fastener Kit",
        "unit": "SET",
        "base_rate_paise": 115200,
        "is_service": False,
        "cgst_bps": 900,
        "sgst_bps": 900,
        "igst_bps": 1800,
    },
    {
        "stage": "vehicles_and_fleet",
        "hsn_sac": "87032310",
        "description": "Industrial Material Handling Utility Vehicles",
        "unit": "NOS",
        "base_rate_paise": 138240,
        "is_service": False,
        "cgst_bps": 900,
        "sgst_bps": 900,
        "igst_bps": 1800,
    },
    {
        "stage": "it_services",
        "hsn_sac": "998314",
        "description": "Cloud ERP & Industrial IoT Integration Services",
        "unit": "HRS",
        "base_rate_paise": 85000,
        "is_service": True,
        "cgst_bps": 900,
        "sgst_bps": 900,
        "igst_bps": 1800,
    },
]


def compute_identity(gstin: str, fy: str, doc_type: str, invoice_no: str) -> str:
    norm = "|".join(
        part.strip().upper()
        for part in (gstin, fy, doc_type, " ".join(invoice_no.strip().upper().split()))
    )
    return hmac.new(DEMO_HMAC_KEY, norm.encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass
class EconomyDataset:
    seed: int
    regime: str
    invoices: list[Invoice] = field(default_factory=list)
    versions: list[InvoiceVersion] = field(default_factory=list)
    evidence_docs: list[EvidenceDoc] = field(default_factory=list)
    payments: list[PaymentRecord] = field(default_factory=list)
    reservations: list[Reservation] = field(default_factory=list)
    requests: list[FinancingRequest] = field(default_factory=list)
    buyer_acceptances: list[BuyerAcceptance] = field(default_factory=list)
    cases: list[dict[str, Any]] = field(default_factory=list)
    legit_circular_rings: list[dict[str, Any]] = field(default_factory=list)


def build_invoice_and_docs(
    seed: int,
    seq_no: int,
    seller: Party,
    buyer: Party,
    flow: dict[str, Any],
    qty: int,
    rate_paise: int,
    invoice_date: str,
    due_date: str,
    po_date: str,
    grn_date: str,
    ewb_date: str,
    ewb_generated_at: str,
    vehicle_no: str,
    invoice_id_override: str | None = None,
    invoice_no_override: str | None = None,
    po_id_override: str | None = None,
    contract_id_override: str | None = None,
    include_grn: bool = True,
    include_ewb: bool = True,
) -> tuple[Invoice, InvoiceVersion, FinancingRequest, list[EvidenceDoc], BuyerAcceptance]:
    inv_id = invoice_id_override or f"INV-{seed}-{seq_no:05d}"
    fy = "2026-27" if invoice_date >= "2026-04-01" else "2025-26"
    inv_no = invoice_no_override or f"{seller.party_id[-3:]}/{fy[-5:]}/{seq_no:05d}"
    po_id = po_id_override or f"D-PO-{seed}-{seq_no:05d}"
    cnt_id = contract_id_override or f"D-CNT-{seed}-{seq_no:05d}"

    taxable = qty * rate_paise
    same_state = seller.state_code == buyer.state_code
    cgst_bps = flow["cgst_bps"] if same_state else 0
    sgst_bps = flow["sgst_bps"] if same_state else 0
    igst_bps = 0 if same_state else flow["igst_bps"]
    cgst = (taxable * cgst_bps) // 10000
    sgst = (taxable * sgst_bps) // 10000
    igst = (taxable * igst_bps) // 10000
    tot_tax = cgst + sgst + igst
    grand = taxable + tot_tax

    li = LineItem(
        line_no=1,
        description=flow["description"],
        hsn_sac=flow["hsn_sac"],
        is_service=bool(flow["is_service"]),
        qty=qty,
        unit=flow["unit"],
        rate_paise=rate_paise,
        taxable_paise=taxable,
        cgst_bps=cgst_bps,
        sgst_bps=sgst_bps,
        igst_bps=igst_bps,
        tax_paise=tot_tax,
        total_paise=grand,
    )
    inv = Invoice(
        invoice_id=inv_id,
        invoice_no=inv_no,
        financial_year=fy,
        doc_type=DocType.INVOICE,
        invoice_date=invoice_date,
        due_date=due_date,
        seller_id=seller.party_id,
        seller_gstin=seller.gstin,
        buyer_id=buyer.party_id,
        buyer_gstin=buyer.gstin,
        place_of_supply_state=buyer.state_code,
        is_export_or_sez=False,
        po_id=po_id,
        contract_id=cnt_id,
        payment_terms_days=60,
        line_items=[li],
        subtotal_paise=taxable,
        cgst_paise=cgst,
        sgst_paise=sgst,
        igst_paise=igst,
        total_tax_paise=tot_tax,
        grand_total_paise=grand,
    )
    ver = InvoiceVersion(
        version_id=f"V-{seed}-{seq_no:05d}-1",
        invoice_id=inv.invoice_id,
        version_no=1,
        kind=VersionKind.INITIAL,
        invoice_identity=compute_identity(inv.seller_gstin, inv.financial_year, inv.doc_type.value, inv.invoice_no),
        file_sha256=hashlib.sha256(f"pdf:{seed}:{inv.invoice_id}:v1".encode("utf-8")).hexdigest(),
        field_root=canonical_sha256(inv),
        created_at=f"{invoice_date}T10:00:00Z",
    )
    req = FinancingRequest(
        request_id=f"REQ-{seed}-{seq_no:05d}",
        invoice_id=inv.invoice_id,
        version_id=ver.version_id,
        seller_id=seller.party_id,
        buyer_id=buyer.party_id,
        lender_id="L1",
        requested_amount_paise=grand,
        tenor_days=60,
        seller_signature_hex="",
        submitted_at=f"{invoice_date}T10:05:00Z",
    )
    # Seller signs the canonical request (signature fields stripped), as the registry verifies it.
    req = req.model_copy(
        update={"seller_signature_hex": _sign_canonical(seed, seller.party_id, req)}
    )

    ev_list: list[EvidenceDoc] = [
        EvidenceDoc(
            doc_id=po_id,
            doc_type=DocType.PURCHASE_ORDER,
            doc_no=f"PO/{buyer.party_id[-3:]}/{seq_no:05d}",
            doc_date=po_date,
            seller_id=seller.party_id,
            buyer_id=buyer.party_id,
            invoice_id=inv.invoice_id,
            po_id=po_id,
            contract_id=cnt_id,
            source=DocSource.BUYER_PORTAL,
            verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
            line_items=[li],
            total_paise=grand,
            valid_from="2025-04-01",
            valid_until="2027-03-31",
            payment_terms_days=60,
        ),
        EvidenceDoc(
            doc_id=cnt_id,
            doc_type=DocType.CONTRACT,
            doc_no=f"CNT/{buyer.party_id[-3:]}/{seq_no:05d}",
            doc_date="2025-04-01",
            seller_id=seller.party_id,
            buyer_id=buyer.party_id,
            invoice_id=inv.invoice_id,
            contract_id=cnt_id,
            source=DocSource.BUYER_PORTAL,
            verification_status=VerificationStatus.SIGNATURE_CHECKED,
            total_paise=grand * 5,
            valid_from="2025-04-01",
            valid_until="2027-03-31",
            payment_terms_days=60,
            milestone_id="MS-01" if flow["is_service"] else None,
            rate_caps_paise={flow["hsn_sac"]: int(rate_paise * 110 // 100)},
        ),
    ]

    if flow["is_service"]:
        ev_list.append(
            EvidenceDoc(
                doc_id=f"D-SA-{seed}-{seq_no:05d}",
                doc_type=DocType.SERVICE_ACCEPTANCE,
                doc_no=f"SA/{buyer.party_id[-3:]}/{seq_no:05d}",
                doc_date=grn_date,
                seller_id=seller.party_id,
                buyer_id=buyer.party_id,
                invoice_id=inv.invoice_id,
                po_id=po_id,
                contract_id=cnt_id,
                source=DocSource.BUYER_PORTAL,
                verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
                line_items=[li],
                total_paise=grand,
                milestone_id="MS-01",
            )
        )
    else:
        if include_grn:
            ev_list.append(
                EvidenceDoc(
                    doc_id=f"D-GRN-{seed}-{seq_no:05d}",
                    doc_type=DocType.GRN,
                    doc_no=f"GRN/{buyer.party_id[-3:]}/{seq_no:05d}",
                    doc_date=grn_date,
                    seller_id=seller.party_id,
                    buyer_id=buyer.party_id,
                    invoice_id=inv.invoice_id,
                    po_id=po_id,
                    source=DocSource.ERP_WEBHOOK,
                    verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
                    line_items=[li],
                    total_paise=grand,
                )
            )
        if include_ewb:
            dist_km = max(10, int(round(road_distance_km(seller.pincode, buyer.pincode))))
            ev_list.append(
                EvidenceDoc(
                    doc_id=f"D-EWB-{seed}-{seq_no:05d}",
                    doc_type=DocType.EWAY_BILL,
                    doc_no=f"EWB-{seller.state_code}{seq_no:08d}",
                    doc_date=ewb_date,
                    seller_id=seller.party_id,
                    buyer_id=buyer.party_id,
                    invoice_id=inv.invoice_id,
                    source=DocSource.GSTN_EWAY_FIXTURE,
                    verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
                    total_paise=grand,
                    vehicle_no=vehicle_no,
                    transporter_id=f"{seller.state_code}AABCT9911K1Z5",
                    from_pincode=seller.pincode,
                    to_pincode=buyer.pincode,
                    distance_km=dist_km,
                    generated_at=ewb_generated_at,
                    valid_until="2026-09-30",
                )
            )

    acceptance = BuyerAcceptance(
        acceptance_id=f"ACC-{seed}-{seq_no:05d}",
        invoice_id=inv.invoice_id,
        version_id=ver.version_id,
        buyer_id=buyer.party_id,
        decision="accept",
        accepted_amount_paise=grand,
        accepted_at=f"{invoice_date}T14:00:00Z",
        signature_hex="",
    )
    acceptance = acceptance.model_copy(
        update={"signature_hex": _sign_canonical(seed, buyer.party_id, acceptance)}
    )
    return inv, ver, req, ev_list, acceptance


def generate_legitimate_economy(world: GeneratedWorld) -> EconomyDataset:
    """Generate 12 months of legitimate sector-flow trades, payments, and >=30 legit_circular loops."""
    rng = random.Random(f"tessera-docs:{world.regime}:{world.seed}")
    ds = EconomyDataset(seed=world.seed, regime=world.regime)

    # Use independent manufacturing/distribution firms (firms[40:120]) for clean trades & legit circular loops
    clean_pool = [f for f in world.firms[40:160] if f.industry_class in (IndustryClass.MANUFACTURING, IndustryClass.DISTRIBUTION)]
    if len(clean_pool) < 60:
        clean_pool = list(world.firms[40:140])

    seq = 1

    # 1. Generate historical on-time payments for all buyers in the world so clean cases have strong buyer track records
    for idx_p, party in enumerate(world.firms[:168], start=1):
        for p_i in range(1, 6):
            seller_p = world.firms[(idx_p + p_i * 7) % 160]
            if seller_p.party_id == party.party_id:
                seller_p = world.firms[(idx_p + p_i * 7 + 1) % 160]
            ds.payments.append(
                PaymentRecord(
                    payment_id=f"PAY-HIST-{world.seed}-{idx_p:03d}-{p_i}",
                    invoice_id=f"INV-HIST-{world.seed}-{idx_p:03d}-{p_i}",
                    seller_id=seller_p.party_id,
                    buyer_id=party.party_id,
                    amount_paise=85_000_000 + p_i * 5_000_000,
                    due_date=f"2026-0{p_i}-20",
                    paid_date=f"2026-0{p_i}-18",
                    dpd=0,
                    disputed=False,
                )
            )

    # 2. Generate 50 clean_goods & 12 clean_services cases across 12 months
    for c_idx in range(1, 63):
        is_svc = c_idx > 50
        flow = SECTOR_FLOW_TABLE[4] if is_svc else SECTOR_FLOW_TABLE[c_idx % 4]
        seller = clean_pool[(c_idx * 3) % len(clean_pool)]
        buyer = clean_pool[(c_idx * 3 + 5) % len(clean_pool)]
        if seller.party_id == buyer.party_id:
            buyer = clean_pool[(c_idx * 3 + 6) % len(clean_pool)]

        inv, ver, req, ev_list, acc = build_invoice_and_docs(
            seed=world.seed,
            seq_no=seq,
            seller=seller,
            buyer=buyer,
            flow=flow,
            qty=1000,
            rate_paise=flow["base_rate_paise"],
            invoice_date="2026-08-12",
            due_date="2026-10-11",
            po_date="2026-08-01",
            grn_date="2026-08-10",
            ewb_date="2026-08-09",
            ewb_generated_at="2026-08-09T08:30:00Z",
            vehicle_no=f"MH14HG{1000 + seq:04d}",
        )
        seq += 1
        ds.invoices.append(inv)
        ds.versions.append(ver)
        ds.requests.append(req)
        ds.evidence_docs.extend(ev_list)
        ds.buyer_acceptances.append(acc)

        sub_type = "clean_services" if is_svc else "clean_goods"
        case_id = f"{sub_type}_{c_idx:03d}"
        buyer_pays = [p.model_dump(mode="json") for p in ds.payments if p.buyer_id == buyer.party_id][:5]
        ds.cases.append(
            {
                "case_id": case_id,
                "category": "clean",
                "sub_type": sub_type,
                "description": f"Clean {sub_type} synthetic trade {case_id}",
                "request": req.model_dump(mode="json"),
                "invoice": inv.model_dump(mode="json"),
                "version": ver.model_dump(mode="json"),
                "parties": {
                    seller.party_id: seller.model_dump(mode="json"),
                    buyer.party_id: buyer.model_dump(mode="json"),
                },
                "evidence": [d.model_dump(mode="json") for d in ev_list],
                "history": {
                    "invoices": [inv.model_dump(mode="json")],
                    "versions": [],
                    "payments": buyer_pays,
                    "evidence_docs": [d.model_dump(mode="json") for d in ev_list],
                    "reservations": [],
                    "allocations_paise": {},
                    "metadata": {"case_id": case_id, "sub_type": sub_type},
                },
                "expected": {
                    "case_id": case_id,
                    "expected_outcome": "conditionally_approved",
                    "failing_checks": [],
                    "warn_checks": [],
                },
            }
        )

    # 3. Generate 32 emergent legit_circular supply-chain loops (>= 30 required)
    # 4 long-standing manufacturing/distribution firms per loop, 20% value-add margin per stage,
    # HSN transformation (72081000 -> 87084000 -> 73181500 -> 87032310), >6 months history, >85% outside trade.
    for lc_idx in range(1, 33):
        base_offset = (lc_idx * 4) % (len(clean_pool) - 8)
        f1, f2, f3, f4 = (
            clean_pool[base_offset],
            clean_pool[base_offset + 1],
            clean_pool[base_offset + 2],
            clean_pool[base_offset + 3],
        )
        ext1, ext2 = clean_pool[base_offset + 4], clean_pool[base_offset + 5]
        # Ensure all 4 ring firms have distinct independent directors & bank accounts
        f1 = f1.model_copy(
            update={
                "industry_class": IndustryClass.MANUFACTURING,
                "directors": [f"DIN-LC{lc_idx:02d}11", f"DIN-LC{lc_idx:02d}12"],
                "bank_account_hash": hashlib.sha256(f"LC:{lc_idx}:1".encode("utf-8")).hexdigest(),
            }
        )
        f2 = f2.model_copy(
            update={
                "industry_class": IndustryClass.MANUFACTURING,
                "directors": [f"DIN-LC{lc_idx:02d}21", f"DIN-LC{lc_idx:02d}22"],
                "bank_account_hash": hashlib.sha256(f"LC:{lc_idx}:2".encode("utf-8")).hexdigest(),
            }
        )
        f3 = f3.model_copy(
            update={
                "industry_class": IndustryClass.MANUFACTURING,
                "directors": [f"DIN-LC{lc_idx:02d}31", f"DIN-LC{lc_idx:02d}32"],
                "bank_account_hash": hashlib.sha256(f"LC:{lc_idx}:3".encode("utf-8")).hexdigest(),
            }
        )
        f4 = f4.model_copy(
            update={
                "industry_class": IndustryClass.DISTRIBUTION,
                "directors": [f"DIN-LC{lc_idx:02d}41", f"DIN-LC{lc_idx:02d}42"],
                "bank_account_hash": hashlib.sha256(f"LC:{lc_idx}:4".encode("utf-8")).hexdigest(),
            }
        )

        hop1_inv, hop1_ver, hop1_req, hop1_ev, _ = build_invoice_and_docs(
            seed=world.seed,
            seq_no=seq,
            seller=f1,
            buyer=f2,
            flow=SECTOR_FLOW_TABLE[0],
            qty=1000,
            rate_paise=80000,
            invoice_date="2026-08-12",
            due_date="2026-10-11",
            po_date="2026-08-01",
            grn_date="2026-08-10",
            ewb_date="2026-08-09",
            ewb_generated_at="2026-08-09T08:00:00Z",
            vehicle_no=f"MH12LC{1000 + seq:04d}",
        )
        seq += 1
        hop2_inv, _, _, _, _ = build_invoice_and_docs(
            seed=world.seed,
            seq_no=seq,
            seller=f2,
            buyer=f3,
            flow=SECTOR_FLOW_TABLE[1],
            qty=1000,
            rate_paise=96000,
            invoice_date="2026-06-15",
            due_date="2026-08-14",
            po_date="2026-06-01",
            grn_date="2026-06-12",
            ewb_date="2026-06-11",
            ewb_generated_at="2026-06-11T08:00:00Z",
            vehicle_no=f"MH12LC{1000 + seq:04d}",
        )
        seq += 1
        hop3_inv, _, _, _, _ = build_invoice_and_docs(
            seed=world.seed,
            seq_no=seq,
            seller=f3,
            buyer=f4,
            flow=SECTOR_FLOW_TABLE[2],
            qty=1000,
            rate_paise=115200,
            invoice_date="2026-04-18",
            due_date="2026-06-17",
            po_date="2026-04-02",
            grn_date="2026-04-15",
            ewb_date="2026-04-14",
            ewb_generated_at="2026-04-14T08:00:00Z",
            vehicle_no=f"MH12LC{1000 + seq:04d}",
        )
        seq += 1
        hop4_inv, _, _, _, _ = build_invoice_and_docs(
            seed=world.seed,
            seq_no=seq,
            seller=f4,
            buyer=f1,
            flow=SECTOR_FLOW_TABLE[3],
            qty=1000,
            rate_paise=138240,
            invoice_date="2026-02-10",
            due_date="2026-04-11",
            po_date="2026-02-01",
            grn_date="2026-02-08",
            ewb_date="2026-02-07",
            ewb_generated_at="2026-02-07T08:00:00Z",
            vehicle_no=f"MH12LC{1000 + seq:04d}",
        )
        seq += 1

        # Add >85% outside trade with ext1 and ext2 dating back >6 months (2025-10..2026-05)
        outside_invs: list[Invoice] = []
        for s_f, b_f, dt in [
            (f1, ext1, "2025-10-15"),
            (ext2, f1, "2025-12-10"),
            (f2, ext1, "2025-11-12"),
            (ext2, f2, "2026-01-19"),
            (f3, ext1, "2025-10-28"),
            (ext2, f3, "2026-03-08"),
            (f4, ext1, "2025-11-25"),
            (ext2, f4, "2026-05-14"),
        ]:
            out_inv, _, _, _, _ = build_invoice_and_docs(
                seed=world.seed,
                seq_no=seq,
                seller=s_f,
                buyer=b_f,
                flow=SECTOR_FLOW_TABLE[0],
                qty=5000,
                rate_paise=180000,
                invoice_date=dt,
                due_date="2026-07-15",
                po_date="2025-09-01",
                grn_date=dt,
                ewb_date=dt,
                ewb_generated_at=f"{dt}T08:00:00Z",
                vehicle_no=f"MH12EX{1000 + seq:04d}",
            )
            seq += 1
            outside_invs.append(out_inv)

        ds.invoices.extend([hop1_inv, hop2_inv, hop3_inv, hop4_inv, *outside_invs])
        ds.versions.append(hop1_ver)
        ds.requests.append(hop1_req)
        ds.evidence_docs.extend(hop1_ev)

        case_id = f"legit_circular_{lc_idx:03d}"
        ring_id = f"RLC-{lc_idx:02d}"
        buyer_pays = [p.model_dump(mode="json") for p in ds.payments if p.buyer_id == f2.party_id][:5]
        parties_map = {p.party_id: p.model_dump(mode="json") for p in (f1, f2, f3, f4, ext1, ext2)}
        ds.legit_circular_rings.append(
            {
                "ring_id": ring_id,
                "ring_type": "legit_circular",
                "case_id": case_id,
                "firms": [f1.party_id, f2.party_id, f3.party_id, f4.party_id],
                "invoice_ids": [hop1_inv.invoice_id, hop2_inv.invoice_id, hop3_inv.invoice_id, hop4_inv.invoice_id],
                "expected_outcome": "conditionally_approved",
                "expected_tier": "low",
                "plausible_forgery": False,
            }
        )
        ds.cases.append(
            {
                "case_id": case_id,
                "category": "legit_circular",
                "sub_type": "legit_circular",
                "ring_id": ring_id,
                "description": f"Legitimate manufacturing circular supply chain loop {case_id} with 20% value-add and >85% outside trade.",
                "request": hop1_req.model_dump(mode="json"),
                "invoice": hop1_inv.model_dump(mode="json"),
                "version": hop1_ver.model_dump(mode="json"),
                "parties": parties_map,
                "evidence": [d.model_dump(mode="json") for d in hop1_ev],
                "history": {
                    "invoices": [
                        i.model_dump(mode="json")
                        for i in (hop1_inv, hop2_inv, hop3_inv, hop4_inv, *outside_invs)
                    ],
                    "versions": [],
                    "payments": buyer_pays,
                    "evidence_docs": [d.model_dump(mode="json") for d in hop1_ev],
                    "reservations": [],
                    "allocations_paise": {},
                    "metadata": {"case_id": case_id, "sub_type": "legit_circular", "ring_id": ring_id},
                },
                "expected": {
                    "case_id": case_id,
                    "expected_outcome": "conditionally_approved",
                    "failing_checks": [],
                    "warn_checks": [],
                },
            }
        )

    return ds

"""Single source of truth for Labyrinth domain models and wire contracts (schema_version: 1)."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

SCHEMA_VERSION = 1


class CheckStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    HARD = "hard"


class DocType(StrEnum):
    INVOICE = "invoice"
    PURCHASE_ORDER = "purchase_order"
    GRN = "grn"
    SERVICE_ACCEPTANCE = "service_acceptance"
    CONTRACT = "contract"
    EWAY_BILL = "eway_bill"
    CREDIT_NOTE = "credit_note"
    DEBIT_NOTE = "debit_note"
    PAYMENT_ADVICE = "payment_advice"


class DocSource(StrEnum):
    SELLER_UPLOAD = "seller_upload"
    BUYER_PORTAL = "buyer_portal"
    ERP_WEBHOOK = "erp_webhook"
    GSTN_EWAY_FIXTURE = "gstn_eway_fixture"
    CONSORTIUM_REGISTRY = "consortium_registry"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    SIGNATURE_CHECKED = "signature_checked"
    INDEPENDENTLY_CONFIRMED = "independently_confirmed"


EVIDENCE_WEIGHT_MAP: dict[VerificationStatus, float] = {
    VerificationStatus.UNVERIFIED: 0.4,
    VerificationStatus.SIGNATURE_CHECKED: 0.7,
    VerificationStatus.INDEPENDENTLY_CONFIRMED: 1.0,
}


def derive_evidence_weight(status: VerificationStatus) -> float:
    return EVIDENCE_WEIGHT_MAP[status]


class ReservationState(StrEnum):
    RESERVED = "reserved"
    FINANCED = "financed"
    SETTLED = "settled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class RequestState(StrEnum):
    SUBMITTED = "submitted"
    EXTRACTED = "extracted"
    VERIFIED = "verified"
    NETWORK_CHECKED = "network_checked"
    SCORED = "scored"
    COND_APPROVED = "cond_approved"
    IN_REVIEW = "in_review"
    REJECTED = "rejected"
    BUYER_ACCEPTED = "buyer_accepted"
    RESERVATION_CONFIRMED = "reservation_confirmed"
    DISBURSED = "disbursed"
    REPAID = "repaid"
    OVERDUE = "overdue"
    SETTLED = "settled"
    DISPUTED = "disputed"


class Outcome(StrEnum):
    CONDITIONALLY_APPROVED = "conditionally_approved"
    REVIEW = "review"
    REJECT = "reject"


class Tier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class IndustryClass(StrEnum):
    MANUFACTURING = "manufacturing"
    TRADING = "trading"
    DISTRIBUTION = "distribution"
    SERVICES = "services"


class VersionKind(StrEnum):
    INITIAL = "initial"
    RESUBMISSION = "resubmission"
    AMENDED = "amended"
    UNEXPLAINED_CHANGE = "unexplained_change"
    EXTRACTION_REVISION = "extraction_revision"


class CheckSource(StrEnum):
    LIVE = "live"
    FIXTURE = "fixture"


class Grade(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"


def _reject_float_int(v: Any) -> int:
    if isinstance(v, bool) or isinstance(v, float):
        raise ValueError(f"Expected strict integer (paise/bps), got {type(v).__name__}: {v}")
    if not isinstance(v, int):
        raise ValueError(f"Expected strict integer (paise/bps), got {type(v).__name__}: {v}")
    return v


class LabyrinthModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class FieldProvenance(LabyrinthModel):
    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    page: int = Field(default=1, ge=1)


class Party(LabyrinthModel):
    schema_version: int = SCHEMA_VERSION
    party_id: str = Field(pattern=r"^(P-[A-Za-z0-9_-]+|L[0-9]+)$")
    name: str
    gstin: str
    pan: str
    cin: str | None = None
    state_code: str
    industry_class: IndustryClass
    address: str
    pincode: str
    city: str
    phone: str
    bank_account_hash: str
    directors: list[str] = Field(default_factory=list)
    public_key_hex: str | None = None


class LineItem(LabyrinthModel):
    line_no: int = Field(ge=1)
    description: str
    hsn_sac: str
    is_service: bool = False
    qty: int = Field(ge=0)
    unit: str = "NOS"
    rate_paise: int = Field(ge=0)
    taxable_paise: int = Field(ge=0)
    cgst_bps: int = Field(default=0, ge=0)
    sgst_bps: int = Field(default=0, ge=0)
    igst_bps: int = Field(default=0, ge=0)
    tax_paise: int = Field(default=0, ge=0)
    total_paise: int = Field(ge=0)

    @field_validator(
        "qty",
        "rate_paise",
        "taxable_paise",
        "cgst_bps",
        "sgst_bps",
        "igst_bps",
        "tax_paise",
        "total_paise",
        mode="before",
    )
    @classmethod
    def _strict_ints(cls, v: Any) -> int:
        return _reject_float_int(v)


class Invoice(LabyrinthModel):
    schema_version: int = SCHEMA_VERSION
    invoice_id: str = Field(pattern=r"^INV-[A-Za-z0-9_-]+$")
    invoice_no: str
    financial_year: str
    doc_type: DocType = DocType.INVOICE
    invoice_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    due_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    seller_id: str
    seller_gstin: str
    buyer_id: str
    buyer_gstin: str
    place_of_supply_state: str
    is_export_or_sez: bool = False
    po_id: str | None = None
    contract_id: str | None = None
    payment_terms_days: int = Field(default=60, ge=0)
    line_items: list[LineItem] = Field(min_length=1)
    subtotal_paise: int = Field(ge=0)
    cgst_paise: int = Field(default=0, ge=0)
    sgst_paise: int = Field(default=0, ge=0)
    igst_paise: int = Field(default=0, ge=0)
    total_tax_paise: int = Field(ge=0)
    grand_total_paise: int = Field(ge=0)
    linked_invoice_id: str | None = None

    @field_validator(
        "payment_terms_days",
        "subtotal_paise",
        "cgst_paise",
        "sgst_paise",
        "igst_paise",
        "total_tax_paise",
        "grand_total_paise",
        mode="before",
    )
    @classmethod
    def _strict_ints(cls, v: Any) -> int:
        return _reject_float_int(v)


class InvoiceVersion(LabyrinthModel):
    schema_version: int = SCHEMA_VERSION
    version_id: str = Field(pattern=r"^V-[A-Za-z0-9_-]+$")
    invoice_id: str = Field(pattern=r"^INV-[A-Za-z0-9_-]+$")
    version_no: int = Field(default=1, ge=1)
    kind: VersionKind = VersionKind.INITIAL
    invoice_identity: str
    file_sha256: str
    field_root: str
    field_salts: dict[str, str] = Field(default_factory=dict)
    field_provenance: dict[str, FieldProvenance] = Field(default_factory=dict)
    tamper_hints: list[str] = Field(default_factory=list)
    created_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T.*Z$")


class EvidenceDoc(LabyrinthModel):
    schema_version: int = SCHEMA_VERSION
    doc_id: str = Field(pattern=r"^D-[A-Za-z0-9_-]+$")
    doc_type: DocType
    doc_no: str
    doc_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    seller_id: str
    buyer_id: str
    invoice_id: str | None = None
    po_id: str | None = None
    contract_id: str | None = None
    source: DocSource
    verification_status: VerificationStatus
    file_sha256: str | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    total_paise: int = Field(default=0, ge=0)
    valid_from: str | None = None
    valid_until: str | None = None
    payment_terms_days: int | None = None
    rate_caps_paise: dict[str, int] = Field(default_factory=dict)
    milestone_id: str | None = None
    vehicle_no: str | None = None
    transporter_id: str | None = None
    from_pincode: str | None = None
    to_pincode: str | None = None
    distance_km: int | None = None
    generated_at: str | None = None
    evidence_weight: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("total_paise", mode="before")
    @classmethod
    def _strict_total(cls, v: Any) -> int:
        return _reject_float_int(v)

    @model_validator(mode="after")
    def _derive_weight(self) -> EvidenceDoc:
        object.__setattr__(self, "evidence_weight", derive_evidence_weight(self.verification_status))
        return self


class BuyerAcceptance(LabyrinthModel):
    schema_version: int = SCHEMA_VERSION
    acceptance_id: str
    invoice_id: str = Field(pattern=r"^INV-[A-Za-z0-9_-]+$")
    version_id: str = Field(pattern=r"^V-[A-Za-z0-9_-]+$")
    buyer_id: str
    decision: Literal["accept", "reject"] = "accept"
    accepted_amount_paise: int = Field(ge=0)
    accepted_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T.*Z$")
    signature_hex: str

    @field_validator("accepted_amount_paise", mode="before")
    @classmethod
    def _strict_amt(cls, v: Any) -> int:
        return _reject_float_int(v)


class FinancingRequest(LabyrinthModel):
    schema_version: int = SCHEMA_VERSION
    request_id: str = Field(pattern=r"^REQ-[A-Za-z0-9_-]+$")
    invoice_id: str = Field(pattern=r"^INV-[A-Za-z0-9_-]+$")
    version_id: str = Field(pattern=r"^V-[A-Za-z0-9_-]+$")
    seller_id: str
    buyer_id: str
    lender_id: str
    requested_amount_paise: int = Field(ge=0)
    tenor_days: int = Field(default=60, ge=1)
    state: RequestState = RequestState.SUBMITTED
    seller_signature_hex: str
    submitted_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T.*Z$")

    @field_validator("requested_amount_paise", "tenor_days", mode="before")
    @classmethod
    def _strict_ints(cls, v: Any) -> int:
        return _reject_float_int(v)


class Reservation(LabyrinthModel):
    schema_version: int = SCHEMA_VERSION
    reservation_id: str = Field(pattern=r"^RSV-[A-Za-z0-9_-]+$")
    invoice_identity: str
    request_id: str = Field(pattern=r"^REQ-[A-Za-z0-9_-]+$")
    lender_id: str
    seller_id: str
    amount_paise: int = Field(ge=0)
    state: ReservationState
    created_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T.*Z$")
    expires_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T.*Z$")

    @field_validator("amount_paise", mode="before")
    @classmethod
    def _strict_amt(cls, v: Any) -> int:
        return _reject_float_int(v)


class CheckResult(LabyrinthModel):
    schema_version: int = SCHEMA_VERSION
    check: str = Field(pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")
    status: CheckStatus
    severity: Severity
    source: CheckSource = CheckSource.LIVE
    evidence_weight: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: list[str] = Field(min_length=1)
    refs: list[str] = Field(default_factory=list)
    verification_status: VerificationStatus | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_evidence_sentences(self) -> CheckResult:
        for line in self.evidence:
            if not line or not line.strip():
                raise ValueError("Evidence strings must be non-empty sentences.")
            if line.strip().lower().startswith("score:"):
                raise ValueError("Evidence must be sentences built from numbers, never raw 'score: X'.")
        if self.verification_status is not None:
            object.__setattr__(
                self, "evidence_weight", derive_evidence_weight(self.verification_status)
            )
        return self


class Override(LabyrinthModel):
    override_id: str
    reviewer_id: str
    second_reviewer_id: str | None = None
    reason: str = Field(min_length=3)
    prior_outcome: Outcome
    new_outcome: Outcome
    scores_snapshot: dict[str, Any] = Field(default_factory=dict)
    overridden_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T.*Z$")


class PricingQuote(LabyrinthModel):
    grade: Grade
    advance_rate_bps: int = Field(ge=0, le=10000)
    advance_amount_paise: int = Field(ge=0)
    annual_rate_bps: int = Field(ge=0)
    tenor_days: int = Field(ge=0)
    discount_paise: int = Field(ge=0)
    net_disbursement_paise: int = Field(ge=0)
    arithmetic_trace: str
    policy_notice: str = "Illustrative prototype policy, not validated lending prices."

    @field_validator(
        "advance_rate_bps",
        "advance_amount_paise",
        "annual_rate_bps",
        "tenor_days",
        "discount_paise",
        "net_disbursement_paise",
        mode="before",
    )
    @classmethod
    def _strict_ints(cls, v: Any) -> int:
        return _reject_float_int(v)


class Decision(LabyrinthModel):
    schema_version: int = SCHEMA_VERSION
    decision_id: str
    request_id: str = Field(pattern=r"^REQ-[A-Za-z0-9_-]+$")
    invoice_id: str = Field(pattern=r"^INV-[A-Za-z0-9_-]+$")
    version_id: str = Field(pattern=r"^V-[A-Za-z0-9_-]+$")
    outcome: Outcome
    state: RequestState
    grade: Grade
    score_points: int
    network_tier: Tier = Tier.LOW
    pricing: PricingQuote | None = None
    top_reasons: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)
    stage_latencies_ms: dict[str, int] = Field(default_factory=dict)
    overridden_by: list[Override] = Field(default_factory=list)
    decided_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T.*Z$")


class AuditEvent(LabyrinthModel):
    schema_version: int = SCHEMA_VERSION
    seq: int = Field(ge=1)
    event_id: str
    event_type: str
    entity_id: str
    actor_id: str
    timestamp: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T.*Z$")
    payload: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str
    hash: str


class PaymentRecord(LabyrinthModel):
    payment_id: str
    invoice_id: str
    seller_id: str
    buyer_id: str
    amount_paise: int = Field(ge=0)
    due_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    paid_date: str | None = None
    dpd: int = 0
    disputed: bool = False

    @field_validator("amount_paise", "dpd", mode="before")
    @classmethod
    def _strict_ints(cls, v: Any) -> int:
        return _reject_float_int(v)


def _assert_no_floats(data: Any, path: str = "$") -> None:
    if isinstance(data, float):
        raise ValueError(f"Float disallowed in canonical JSON at {path}: {data}")
    if isinstance(data, dict):
        for k, v in data.items():
            _assert_no_floats(v, f"{path}.{k}")
    elif isinstance(data, (list, tuple)):
        for idx, item in enumerate(data):
            _assert_no_floats(item, f"{path}[{idx}]")


def canonical_json(data: Any, allow_floats: bool = False) -> str:
    """Deterministic UTF-8 JSON with sorted keys and compact separators."""
    if isinstance(data, BaseModel):
        payload = data.model_dump(mode="json")
    else:
        payload = data
    if not allow_floats:
        _assert_no_floats(payload)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_sha256(data: Any, allow_floats: bool = False) -> str:
    return hashlib.sha256(canonical_json(data, allow_floats=allow_floats).encode("utf-8")).hexdigest()


def format_inr(paise: int) -> str:
    """Format paise into Indian numbering currency string (e.g. ₹10,00,000 or ₹16,767.12)."""
    sign = "-" if paise < 0 else ""
    abs_paise = abs(paise)
    rupees, rem_paise = divmod(abs_paise, 100)
    s = str(rupees)
    if len(s) > 3:
        last3 = s[-3:]
        rest = s[:-3]
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        formatted_rupees = ",".join(groups) + "," + last3
    else:
        formatted_rupees = s
    if rem_paise:
        return f"{sign}₹{formatted_rupees}.{rem_paise:02d}"
    return f"{sign}₹{formatted_rupees}"


GSTIN_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def compute_gstin_check_char(prefix14: str) -> str:
    """Compute the 15th mod-36 check character for a 14-character GSTIN prefix."""
    s = prefix14.strip().upper()
    if len(s) != 14 or any(ch not in GSTIN_ALPHABET for ch in s):
        raise ValueError(f"GSTIN prefix must be 14 alphanumeric chars, got {prefix14!r}")
    total = 0
    for idx, ch in enumerate(s):
        val = GSTIN_ALPHABET.index(ch)
        factor = 1 if (idx % 2 == 0) else 2
        prod = val * factor
        total += (prod // 36) + (prod % 36)
    rem = total % 36
    check_idx = (36 - rem) % 36
    return GSTIN_ALPHABET[check_idx]


def make_gstin(state_code: str, pan: str, entity_code: str = "1") -> str:
    """Build a valid 15-character GSTIN from state_code (2 digits), PAN (10 chars), and entity code."""
    prefix14 = f"{state_code.strip()}{pan.strip().upper()}{entity_code.strip().upper()}Z"
    return prefix14 + compute_gstin_check_char(prefix14)


def verify_gstin_checksum(gstin: str) -> bool:
    """Verify whether a 15-character GSTIN has a valid mod-36 check character."""
    s = gstin.strip().upper()
    if len(s) != 15 or any(ch not in GSTIN_ALPHABET for ch in s):
        return False
    return compute_gstin_check_char(s[:14]) == s[14]


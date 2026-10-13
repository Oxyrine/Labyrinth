"""CaseContext and HistoryView protocol — the seam every module check implements."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from contract.models import (
    CheckResult,
    DocType,
    EvidenceDoc,
    FinancingRequest,
    Invoice,
    InvoiceVersion,
    Party,
    PaymentRecord,
    Reservation,
)


@runtime_checkable
class HistoryView(Protocol):
    """Read-only view of historical trade, payment, version, and reservation data."""

    def all_invoices(self) -> list[Invoice]: ...

    def invoices_by_party(self, party_id: str) -> list[Invoice]: ...

    def invoices_by_po(self, po_id: str) -> list[Invoice]: ...

    def prior_versions(self, invoice_identity: str) -> list[tuple[Invoice, InvoiceVersion]]: ...

    def payments_by_buyer(self, buyer_id: str) -> list[PaymentRecord]: ...

    def payments_for_invoice(self, invoice_id: str) -> list[PaymentRecord]: ...

    def credit_notes_for_invoice(self, invoice_id: str) -> list[Invoice]: ...

    def evidence_for_invoice(self, invoice_id: str) -> list[EvidenceDoc]: ...

    def all_evidence(self) -> list[EvidenceDoc]: ...

    def allocations_for_invoice(self, invoice_id: str) -> int: ...

    def active_reservations(self, invoice_identity: str) -> list[Reservation]: ...

    def case_metadata(self) -> dict[str, Any]: ...


class InMemoryHistoryView(BaseModel):
    """In-memory fixture implementation of HistoryView so every module tests against fixtures from hour 1."""

    model_config = ConfigDict(extra="forbid")

    invoices: list[Invoice] = Field(default_factory=list)
    versions: list[InvoiceVersion] = Field(default_factory=list)
    payments: list[PaymentRecord] = Field(default_factory=list)
    evidence_docs: list[EvidenceDoc] = Field(default_factory=list)
    reservations: list[Reservation] = Field(default_factory=list)
    allocations_paise: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def all_invoices(self) -> list[Invoice]:
        return [inv for inv in self.invoices if inv.doc_type == DocType.INVOICE]

    def invoices_by_party(self, party_id: str) -> list[Invoice]:
        return [
            inv
            for inv in self.invoices
            if (inv.seller_id == party_id or inv.buyer_id == party_id) and inv.doc_type == DocType.INVOICE
        ]

    def invoices_by_po(self, po_id: str) -> list[Invoice]:
        return [inv for inv in self.invoices if inv.po_id == po_id and inv.doc_type == DocType.INVOICE]

    def prior_versions(self, invoice_identity: str) -> list[tuple[Invoice, InvoiceVersion]]:
        inv_map = {inv.invoice_id: inv for inv in self.invoices}
        out: list[tuple[Invoice, InvoiceVersion]] = []
        for ver in self.versions:
            if ver.invoice_identity == invoice_identity and ver.invoice_id in inv_map:
                out.append((inv_map[ver.invoice_id], ver))
        return out

    def payments_by_buyer(self, buyer_id: str) -> list[PaymentRecord]:
        return [p for p in self.payments if p.buyer_id == buyer_id]

    def payments_for_invoice(self, invoice_id: str) -> list[PaymentRecord]:
        return [p for p in self.payments if p.invoice_id == invoice_id]

    def credit_notes_for_invoice(self, invoice_id: str) -> list[Invoice]:
        return [
            inv
            for inv in self.invoices
            if inv.linked_invoice_id == invoice_id and inv.doc_type == DocType.CREDIT_NOTE
        ]

    def evidence_for_invoice(self, invoice_id: str) -> list[EvidenceDoc]:
        return [doc for doc in self.evidence_docs if doc.invoice_id == invoice_id]

    def all_evidence(self) -> list[EvidenceDoc]:
        return list(self.evidence_docs)

    def allocations_for_invoice(self, invoice_id: str) -> int:
        return self.allocations_paise.get(invoice_id, 0)

    def active_reservations(self, invoice_identity: str) -> list[Reservation]:
        return [
            r
            for r in self.reservations
            if r.invoice_identity == invoice_identity and r.state.value in ("reserved", "financed", "settled")
        ]

    def case_metadata(self) -> dict[str, Any]:
        return dict(self.metadata)


class CaseContext(BaseModel, frozen=True):
    """Immutable context passed into every module's `check.run(ctx: CaseContext) -> list[CheckResult]`."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    request: FinancingRequest
    invoice: Invoice
    version: InvoiceVersion
    parties: dict[str, Party]
    evidence: list[EvidenceDoc] = Field(default_factory=list)
    history: Any = Field(default_factory=InMemoryHistoryView)
    stage_outputs: dict[str, list[CheckResult]] = Field(default_factory=dict)

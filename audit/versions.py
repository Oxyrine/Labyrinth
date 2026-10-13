"""Deterministic version records and `invoice.version_created` payload builders."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from audit.merkle import build_field_tree
from contract.models import (
    SCHEMA_VERSION,
    Invoice,
    InvoiceVersion,
    LabyrinthModel,
    VersionKind,
    canonical_sha256,
)

VERSION_RECORD_KEYS = (
    "version_id",
    "invoice_id",
    "version_no",
    "schema_version",
    "file_sha256",
    "field_root",
)


class VersionRecord(LabyrinthModel):
    """Canonical 6-field version commitment committed into `invoice.version_created` events."""

    version_id: str = Field(pattern=r"^V-[A-Za-z0-9_-]+$")
    invoice_id: str = Field(pattern=r"^INV-[A-Za-z0-9_-]+$")
    version_no: int = Field(default=1, ge=1)
    schema_version: int = SCHEMA_VERSION
    file_sha256: str
    field_root: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "invoice_id": self.invoice_id,
            "version_no": self.version_no,
            "schema_version": self.schema_version,
            "file_sha256": self.file_sha256,
            "field_root": self.field_root,
        }

    def sha256(self) -> str:
        return canonical_sha256(self.to_dict())


def build_version_record(
    version_or_dict: InvoiceVersion | VersionRecord | dict[str, Any] | None = None,
    *,
    version_id: str | None = None,
    invoice_id: str | None = None,
    version_no: int = 1,
    schema_version: int = SCHEMA_VERSION,
    file_sha256: str | None = None,
    field_root: str | None = None,
) -> dict[str, Any]:
    """Build the canonical `{version_id, invoice_id, version_no, schema_version, file_sha256, field_root}` dict."""
    if version_or_dict is not None:
        if isinstance(version_or_dict, BaseModel):
            src = version_or_dict.model_dump(mode="json")
        elif isinstance(version_or_dict, dict):
            if "version_record" in version_or_dict and isinstance(version_or_dict["version_record"], dict):
                src = version_or_dict["version_record"]
            else:
                src = version_or_dict
        else:
            raise TypeError(f"Unsupported version record source: {type(version_or_dict)!r}")
        return {
            "version_id": str(version_id if version_id is not None else src["version_id"]),
            "invoice_id": str(invoice_id if invoice_id is not None else src["invoice_id"]),
            "version_no": int(src.get("version_no", version_no)),
            "schema_version": int(src.get("schema_version", schema_version)),
            "file_sha256": str(file_sha256 if file_sha256 is not None else src["file_sha256"]),
            "field_root": str(field_root if field_root is not None else src["field_root"]),
        }

    if not all([version_id, invoice_id, file_sha256, field_root]):
        raise ValueError("version_id, invoice_id, file_sha256, and field_root are required")
    return {
        "version_id": str(version_id),
        "invoice_id": str(invoice_id),
        "version_no": int(version_no),
        "schema_version": int(schema_version),
        "file_sha256": str(file_sha256),
        "field_root": str(field_root),
    }


def hash_version_record(version_or_dict: InvoiceVersion | VersionRecord | dict[str, Any]) -> str:
    """Compute canonical SHA-256 of a version record."""
    rec = build_version_record(version_or_dict)
    return canonical_sha256(rec)


def build_version_created_payload(
    version_or_dict: InvoiceVersion | VersionRecord | dict[str, Any],
    *,
    doc_id: str | None = None,
) -> dict[str, Any]:
    """Build the payload for an `invoice.version_created` AuditEvent."""
    rec = build_version_record(version_or_dict)
    rec_hash = canonical_sha256(rec)
    payload: dict[str, Any] = {
        **rec,
        "version_record": rec,
        "version_record_hash": rec_hash,
    }
    if doc_id is not None:
        payload["doc_id"] = doc_id
    return payload


def create_invoice_version(
    invoice: Invoice | dict[str, Any],
    *,
    version_id: str,
    file_sha256: str,
    version_no: int = 1,
    kind: VersionKind = VersionKind.INITIAL,
    invoice_identity: str | None = None,
    salts: dict[str, str] | None = None,
    created_at: str = "2026-01-01T00:00:00Z",
) -> tuple[InvoiceVersion, dict[str, Any], dict[str, list[dict[str, str]]]]:
    """Build field Merkle tree for `invoice` and return `(InvoiceVersion, version_record_dict, proofs_by_field)`."""
    if isinstance(invoice, BaseModel):
        fields = invoice.model_dump(mode="json")
        inv_id = invoice.invoice_id
    else:
        fields = dict(invoice)
        inv_id = str(fields["invoice_id"])

    field_root, field_salts, proofs_by_field = build_field_tree(fields, salts=salts)
    identity = invoice_identity or f"ID-{inv_id}"

    inv_version = InvoiceVersion(
        schema_version=SCHEMA_VERSION,
        version_id=version_id,
        invoice_id=inv_id,
        version_no=version_no,
        kind=kind,
        invoice_identity=identity,
        file_sha256=file_sha256,
        field_root=field_root,
        field_salts=field_salts,
        created_at=created_at,
    )
    rec = build_version_record(inv_version)
    return inv_version, rec, proofs_by_field

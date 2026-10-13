"""Selective disclosure proofs for individual invoice fields against `field_root` and `batch_root`."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from audit.chain import EventChain, compute_event_hash
from audit.merkle import build_field_tree, compute_field_leaf_hash, verify_inclusion_proof
from audit.versions import build_version_record, hash_version_record
from contract.models import AuditEvent, Invoice, InvoiceVersion


class DisclosureBundle(dict):  # type: ignore[type-arg]
    """Dictionary-compatible selective disclosure proof bundle with attribute access."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


def build_selective_disclosure(
    invoice_or_fields: Invoice | dict[str, Any] | None = None,
    field_name: str = "grand_total_paise",
    *,
    field_value: Any = None,
    salt_hex: str | None = None,
    field_proof: list[dict[str, str]] | None = None,
    field_root: str | None = None,
    version: InvoiceVersion | None = None,
    version_record: dict[str, Any] | None = None,
    salts: dict[str, str] | None = None,
    field_proofs: dict[str, list[dict[str, str]]] | None = None,
    event: AuditEvent | dict[str, Any] | None = None,
    event_hash: str | None = None,
    event_inclusion_proof: list[dict[str, str]] | None = None,
    batch_root: str | None = None,
    chain: EventChain | None = None,
) -> DisclosureBundle:
    """Create a selective disclosure proof bundle for a single invoice field without revealing other fields."""
    fields_map: dict[str, Any] | None = None
    if invoice_or_fields is not None:
        if isinstance(invoice_or_fields, BaseModel):
            fields_map = invoice_or_fields.model_dump(mode="json")
        elif isinstance(invoice_or_fields, dict):
            fields_map = dict(invoice_or_fields)

    if field_value is None:
        if fields_map is not None and field_name in fields_map:
            field_value = fields_map[field_name]
        else:
            raise ValueError(f"field_value for '{field_name}' must be provided")

    effective_salts = salts or (version.field_salts if version is not None else None)
    if (field_root is None or field_proof is None or salt_hex is None) and fields_map is not None:
        computed_root, computed_salts, computed_proofs = build_field_tree(fields_map, salts=effective_salts)
        field_root = field_root or computed_root
        salt_hex = salt_hex or computed_salts[field_name]
        field_proof = field_proof if field_proof is not None else computed_proofs[field_name]
    else:
        if salt_hex is None and effective_salts is not None and field_name in effective_salts:
            salt_hex = effective_salts[field_name]
        if field_proof is None and field_proofs is not None and field_name in field_proofs:
            field_proof = field_proofs[field_name]
        if field_root is None and version is not None:
            field_root = version.field_root

    if version_record is None and version is not None:
        version_record = build_version_record(version)
    elif version_record is not None:
        version_record = build_version_record(version_record)

    if field_root is None and version_record is not None:
        field_root = str(version_record["field_root"])

    if salt_hex is None or field_proof is None or field_root is None:
        raise ValueError("salt_hex, field_proof, and field_root are required to build a selective disclosure bundle")

    ev_dict: dict[str, Any] | None = None
    if event is not None:
        if isinstance(event, AuditEvent):
            ev_dict = event.model_dump(mode="json")
        elif isinstance(event, dict):
            ev_dict = dict(event)
        if event_hash is None and ev_dict is not None and "hash" in ev_dict:
            event_hash = str(ev_dict["hash"])
        if version_record is None and ev_dict is not None:
            payload = ev_dict.get("payload") or {}
            if isinstance(payload, dict) and ("version_record" in payload or "field_root" in payload):
                version_record = build_version_record(payload)

    if chain is not None and ev_dict is not None and (event_inclusion_proof is None or batch_root is None):
        seq = int(ev_dict["seq"])
        proof_info = chain.get_event_inclusion_proof(seq)
        if proof_info is not None:
            batch_dict, ev_proof = proof_info
            batch_root = batch_root or str(batch_dict["root"])
            event_inclusion_proof = event_inclusion_proof if event_inclusion_proof is not None else ev_proof

    bundle = DisclosureBundle(
        field_name=field_name,
        field_value=field_value,
        salt_hex=salt_hex,
        field_proof=list(field_proof),
        field_root=field_root,
        version_record=version_record,
        event_inclusion_proof=list(event_inclusion_proof) if event_inclusion_proof is not None else [],
        batch_root=batch_root or "",
    )
    if ev_dict is not None:
        bundle["event"] = ev_dict
    if event_hash is not None:
        bundle["event_hash"] = event_hash
    return bundle


create_selective_disclosure = build_selective_disclosure


def verify_selective_disclosure(disclosure_bundle: dict[str, Any] | DisclosureBundle) -> bool:
    """Verify a selective disclosure proof bundle against `field_root` and `batch_root`.

    Returns `False` if the field value, salt, field proof, version record, event payload,
    or batch Merkle proof has been tampered with.
    """
    if not isinstance(disclosure_bundle, dict):
        return False

    required_keys = ("field_name", "field_value", "salt_hex", "field_proof", "field_root")
    for k in required_keys:
        if k not in disclosure_bundle:
            return False

    field_name = disclosure_bundle["field_name"]
    field_value = disclosure_bundle["field_value"]
    salt_hex = disclosure_bundle["salt_hex"]
    field_proof = disclosure_bundle["field_proof"]
    field_root = disclosure_bundle["field_root"]

    if not isinstance(field_name, str) or not isinstance(salt_hex, str) or not isinstance(field_root, str):
        return False
    if not isinstance(field_proof, list):
        return False

    try:
        leaf_hex = compute_field_leaf_hash(field_name, field_value, salt_hex)
    except Exception:
        return False

    if not verify_inclusion_proof(leaf_hex, field_root, field_proof):
        return False

    version_record = disclosure_bundle.get("version_record")
    vr_hash: str | None = None
    if version_record is not None:
        if not isinstance(version_record, dict):
            return False
        if version_record.get("field_root") != field_root:
            return False
        try:
            vr_hash = hash_version_record(version_record)
        except Exception:
            return False

    batch_root = disclosure_bundle.get("batch_root")
    event_inclusion_proof = disclosure_bundle.get("event_inclusion_proof")

    if batch_root:
        if not isinstance(batch_root, str) or not isinstance(event_inclusion_proof, list):
            return False

        ev_data = disclosure_bundle.get("event")
        if ev_data is not None:
            if isinstance(ev_data, AuditEvent):
                ev_dict = ev_data.model_dump(mode="json")
            elif isinstance(ev_data, dict):
                ev_dict = ev_data
            else:
                return False

            payload = ev_dict.get("payload")
            if not isinstance(payload, dict):
                return False

            # Check version_record / field_root binding inside the event payload
            payload_field_root = payload.get("field_root") or (
                payload.get("version_record", {}).get("field_root")
                if isinstance(payload.get("version_record"), dict)
                else None
            )
            if payload_field_root != field_root:
                return False

            if vr_hash is not None:
                if "version_record_hash" in payload and payload["version_record_hash"] != vr_hash:
                    return False
                if "version_record" in payload and isinstance(payload["version_record"], dict):
                    if hash_version_record(payload["version_record"]) != vr_hash:
                        return False

            try:
                recomputed_ev_hash = compute_event_hash(
                    seq=int(ev_dict["seq"]),
                    event_id=str(ev_dict["event_id"]),
                    event_type=str(ev_dict["event_type"]),
                    entity_id=str(ev_dict["entity_id"]),
                    actor_id=str(ev_dict["actor_id"]),
                    timestamp=str(ev_dict["timestamp"]),
                    payload=payload,
                    prev_hash=str(ev_dict["prev_hash"]),
                    schema_version=int(ev_dict.get("schema_version", 1)),
                )
            except Exception:
                return False

            if "hash" in ev_dict and str(ev_dict["hash"]) != recomputed_ev_hash:
                return False
            if "event_hash" in disclosure_bundle and str(disclosure_bundle["event_hash"]) != recomputed_ev_hash:
                return False

            if not verify_inclusion_proof(recomputed_ev_hash, batch_root, event_inclusion_proof):
                return False
        elif disclosure_bundle.get("event_hash"):
            ev_hash = str(disclosure_bundle["event_hash"])
            if not verify_inclusion_proof(ev_hash, batch_root, event_inclusion_proof):
                return False
        elif vr_hash is not None:
            if not verify_inclusion_proof(vr_hash, batch_root, event_inclusion_proof):
                return False
        else:
            return False

    return True

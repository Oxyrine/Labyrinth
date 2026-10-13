"""RFC 6962-style Merkle trees with domain separation (`0x00` leaves, `0x01` internal nodes)."""

from __future__ import annotations

import hashlib
import os
from typing import Any

from pydantic import BaseModel

from contract.models import canonical_json

LEAF_PREFIX = b"\x00"
NODE_PREFIX = b"\x01"
SALT_BYTES = 16


def leaf_hash(data: bytes) -> str:
    """Compute RFC 6962 domain-separated leaf hash: SHA256(0x00 || data)."""
    return hashlib.sha256(LEAF_PREFIX + data).hexdigest()


def internal_hash(left_hex: str, right_hex: str) -> str:
    """Compute RFC 6962 domain-separated internal node hash: SHA256(0x01 || left_bytes || right_bytes)."""
    left_bytes = bytes.fromhex(left_hex)
    right_bytes = bytes.fromhex(right_hex)
    return hashlib.sha256(NODE_PREFIX + left_bytes + right_bytes).hexdigest()


def compute_field_leaf_hash(field_name: str, field_value: Any, salt_hex: str) -> str:
    """Compute salted leaf hash for a single invoice field:
    SHA256(0x00 || salt_bytes || name.encode('utf-8') || b'=' || canonical_json(value, allow_floats=True).encode('utf-8'))
    """
    if not salt_hex or not isinstance(salt_hex, str):
        raise ValueError("salt_hex must be a non-empty hex string")
    salt_bytes = bytes.fromhex(salt_hex)
    if len(salt_bytes) == 0:
        raise ValueError("salt_bytes cannot be empty")
    value_bytes = canonical_json(field_value, allow_floats=True).encode("utf-8")
    payload = salt_bytes + field_name.encode("utf-8") + b"=" + value_bytes
    return leaf_hash(payload)


def compute_event_leaf_hash(event_hash_hex: str) -> str:
    """Compute RFC 6962 leaf hash for an event hash in an anchor batch."""
    try:
        raw = bytes.fromhex(event_hash_hex)
    except ValueError:
        raw = event_hash_hex.encode("utf-8")
    return leaf_hash(raw)


def _build_tree_from_leaves(leaf_hashes: list[str]) -> tuple[str, list[list[dict[str, str]]]]:
    """Recursively build an RFC 6962 Merkle tree and inclusion proofs from leaf hashes."""
    n = len(leaf_hashes)
    if n == 0:
        return hashlib.sha256(b"").hexdigest(), []
    if n == 1:
        return leaf_hashes[0], [[]]

    # Largest power of two strictly less than n (RFC 6962 Section 2.1)
    k = 1 << ((n - 1).bit_length() - 1)
    left_root, left_proofs = _build_tree_from_leaves(leaf_hashes[:k])
    right_root, right_proofs = _build_tree_from_leaves(leaf_hashes[k:])

    root = internal_hash(left_root, right_root)
    combined_proofs: list[list[dict[str, str]]] = []
    for p in left_proofs:
        combined_proofs.append([*p, {"position": "right", "side": "right", "hash": right_root}])
    for p in right_proofs:
        combined_proofs.append([*p, {"position": "left", "side": "left", "hash": left_root}])
    return root, combined_proofs


def build_field_tree(
    fields: dict[str, Any] | BaseModel,
    salts: dict[str, str] | None = None,
) -> tuple[str, dict[str, str], dict[str, list[dict[str, str]]]]:
    """Build salted field Merkle tree in sorted field order.

    Returns:
        `(field_root_hex, salts_hex_dict, proofs_by_field)`
    """
    if isinstance(fields, BaseModel):
        field_map: dict[str, Any] = fields.model_dump(mode="json")
    else:
        field_map = dict(fields)

    sorted_names = sorted(field_map.keys())
    salts_out: dict[str, str] = {}
    leaf_hashes: list[str] = []

    for name in sorted_names:
        if salts is not None and name in salts:
            salt_hex = salts[name]
        else:
            salt_hex = os.urandom(SALT_BYTES).hex()
        salts_out[name] = salt_hex
        leaf_hashes.append(compute_field_leaf_hash(name, field_map[name], salt_hex))

    root_hex, proofs_list = _build_tree_from_leaves(leaf_hashes)
    proofs_by_field = {name: proofs_list[idx] for idx, name in enumerate(sorted_names)}
    return root_hex, salts_out, proofs_by_field


def build_event_batch_tree(event_hashes: list[str]) -> tuple[str, list[list[dict[str, str]]]]:
    """Build RFC 6962 Merkle tree over a batch of event hashes.

    Returns:
        `(batch_root_hex, inclusion_proofs)`
    """
    leaf_hashes = [compute_event_leaf_hash(eh) for eh in event_hashes]
    return _build_tree_from_leaves(leaf_hashes)


def _walk_proof(start_hex: str, proof: list[dict[str, Any]]) -> str:
    current = start_hex.lower()
    bytes.fromhex(current)  # Validate hex
    for step in proof:
        if not isinstance(step, dict):
            raise ValueError("Proof element must be a dict")
        sibling_hex = str(step.get("hash", "")).lower()
        if len(sibling_hex) != 64:
            raise ValueError("Invalid sibling hash length")
        pos = str(step.get("position") or step.get("side") or "").lower()
        if pos in ("left", "l"):
            current = internal_hash(sibling_hex, current)
        elif pos in ("right", "r"):
            current = internal_hash(current, sibling_hex)
        else:
            raise ValueError(f"Invalid sibling position: {pos!r}")
    return current


def verify_inclusion_proof(leaf_hash_hex: str, root_hex: str, proof: list[dict[str, Any]]) -> bool:
    """Verify an RFC 6962 Merkle inclusion proof against `root_hex`.

    Accepts either a pre-hashed leaf (`0x00`-prefixed) or a raw event hash hex string.
    """
    if not isinstance(leaf_hash_hex, str) or not isinstance(root_hex, str) or not isinstance(proof, list):
        return False
    if len(leaf_hash_hex) != 64 or len(root_hex) != 64:
        return False
    target_root = root_hex.lower()
    try:
        bytes.fromhex(target_root)
        if _walk_proof(leaf_hash_hex, proof) == target_root:
            return True
        event_leaf = compute_event_leaf_hash(leaf_hash_hex)
        if _walk_proof(event_leaf, proof) == target_root:
            return True
    except Exception:
        return False
    return False

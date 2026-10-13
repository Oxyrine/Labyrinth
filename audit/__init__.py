"""Encrypted off-chain audit store, salted field Merkle trees, SQLite hash chain, and selective disclosure."""

from __future__ import annotations

from audit.chain import GENESIS_PREV_HASH, EventChain, append_event, compute_event_hash, get_default_chain
from audit.disclose import (
    DisclosureBundle,
    build_selective_disclosure,
    create_selective_disclosure,
    verify_selective_disclosure,
)
from audit.merkle import (
    build_event_batch_tree,
    build_field_tree,
    compute_event_leaf_hash,
    compute_field_leaf_hash,
    internal_hash,
    leaf_hash,
    verify_inclusion_proof,
)
from audit.store import EncryptedBlobStore, TamperDetectedError, load_or_derive_master_key
from audit.verify import VerificationReport, verify, verify_audit
from audit.versions import (
    VERSION_RECORD_KEYS,
    VersionRecord,
    build_version_created_payload,
    build_version_record,
    create_invoice_version,
    hash_version_record,
)

__all__ = [
    "DisclosureBundle",
    "EncryptedBlobStore",
    "EventChain",
    "GENESIS_PREV_HASH",
    "TamperDetectedError",
    "VERSION_RECORD_KEYS",
    "VerificationReport",
    "VersionRecord",
    "append_event",
    "build_event_batch_tree",
    "build_field_tree",
    "build_selective_disclosure",
    "build_version_created_payload",
    "build_version_record",
    "compute_event_hash",
    "compute_event_leaf_hash",
    "compute_field_leaf_hash",
    "create_invoice_version",
    "create_selective_disclosure",
    "get_default_chain",
    "hash_version_record",
    "internal_hash",
    "leaf_hash",
    "load_or_derive_master_key",
    "verify",
    "verify_audit",
    "verify_inclusion_proof",
    "verify_selective_disclosure",
]

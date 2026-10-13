"""Labyrinth Stage 2b deduplication and consortium registry package (`dedup`)."""

from dedup.check import check_registry, compute_outstanding_paise, run
from dedup.identity import classify_version, compute_invoice_identity, diff_invoices, normalize_identity_token
from dedup.near_dup import aggressive_normalize_inv_no, check_near_duplicate, compute_line_jaccard
from dedup.registry import (
    ConsortiumRegistry,
    sign_buyer_acceptance,
    sign_financing_request,
    sign_payload,
    verify_ed25519_signature,
)
from dedup.registry_client import RegistryClient, make_op_id

__all__ = [
    "ConsortiumRegistry",
    "RegistryClient",
    "aggressive_normalize_inv_no",
    "check_near_duplicate",
    "check_registry",
    "classify_version",
    "compute_invoice_identity",
    "compute_line_jaccard",
    "compute_outstanding_paise",
    "diff_invoices",
    "make_op_id",
    "normalize_identity_token",
    "run",
    "sign_buyer_acceptance",
    "sign_financing_request",
    "sign_payload",
    "verify_ed25519_signature",
]

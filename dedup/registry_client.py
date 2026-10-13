"""Client interface for interacting with the ConsortiumRegistry."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from contract.models import BuyerAcceptance, FinancingRequest, Reservation
from dedup.registry import ConsortiumRegistry


def make_op_id(entity_id: str, action: str) -> str:
    """Compute a deterministic SHA-256 operation ID for idempotent registry calls."""
    return hashlib.sha256(f"{entity_id}|{action}".encode("utf-8")).hexdigest()


class RegistryClient:
    """High-level client wrapper around `ConsortiumRegistry`."""

    def __init__(self, registry: ConsortiumRegistry | str | Path = ":memory:") -> None:
        if isinstance(registry, ConsortiumRegistry):
            self.registry = registry
        else:
            self.registry = ConsortiumRegistry(registry)

    def reserve(
        self,
        request: FinancingRequest | dict[str, Any],
        invoice_identity: str,
        op_id: str | None = None,
        seller_public_key_hex: str | None = None,
        now_iso: str | None = None,
        ttl_hours: int = 24,
        outstanding_paise: int | None = None,
    ) -> dict[str, Any]:
        req_id = request.request_id if isinstance(request, FinancingRequest) else str(request["request_id"])
        effective_op_id = op_id or make_op_id(req_id, "reserve")
        return self.registry.reserve(
            op_id=effective_op_id,
            request=request,
            invoice_identity=invoice_identity,
            seller_public_key_hex=seller_public_key_hex,
            now_iso=now_iso,
            ttl_hours=ttl_hours,
            outstanding_paise=outstanding_paise,
        )

    def confirm(
        self,
        reservation_id: str,
        buyer_acceptance: BuyerAcceptance | dict[str, Any],
        outstanding_paise: int | None = None,
        op_id: str | None = None,
        buyer_public_key_hex: str | None = None,
        now_iso: str | None = None,
    ) -> dict[str, Any]:
        effective_op_id = op_id or make_op_id(reservation_id, "confirm")
        return self.registry.confirm(
            op_id=effective_op_id,
            reservation_id=reservation_id,
            buyer_acceptance=buyer_acceptance,
            outstanding_paise=outstanding_paise,
            buyer_public_key_hex=buyer_public_key_hex,
            now_iso=now_iso,
        )

    def settle(
        self,
        reservation_id: str,
        op_id: str | None = None,
        now_iso: str | None = None,
    ) -> dict[str, Any]:
        effective_op_id = op_id or make_op_id(reservation_id, "settle")
        return self.registry.settle(
            op_id=effective_op_id,
            reservation_id=reservation_id,
            now_iso=now_iso,
        )

    def cancel(
        self,
        reservation_id: str,
        reason: str = "cancelled",
        op_id: str | None = None,
        now_iso: str | None = None,
    ) -> dict[str, Any]:
        effective_op_id = op_id or make_op_id(reservation_id, f"cancel:{reason}")
        return self.registry.cancel(
            op_id=effective_op_id,
            reservation_id=reservation_id,
            reason=reason,
            now_iso=now_iso,
        )

    def reject(
        self,
        reservation_id: str,
        op_id: str | None = None,
        now_iso: str | None = None,
    ) -> dict[str, Any]:
        effective_op_id = op_id or make_op_id(reservation_id, "reject")
        return self.registry.reject(
            op_id=effective_op_id,
            reservation_id=reservation_id,
            now_iso=now_iso,
        )

    def expire_sweep(self, now_iso: str | None = None) -> int:
        return self.registry.expire_sweep(now_iso=now_iso)

    def active_reservations(self, invoice_identity: str) -> list[Reservation]:
        return self.registry.active_reservations(invoice_identity)

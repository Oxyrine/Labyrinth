"""Entrypoint for Stage 2b deduplication and consortium registry checks (`dedup.*`)."""

from __future__ import annotations

import hashlib

from contract.context import CaseContext
from contract.models import (
    CheckResult,
    CheckStatus,
    DocType,
    Reservation,
    Severity,
    VerificationStatus,
    format_inr,
)
from dedup.identity import _get_dedup_source, classify_version
from dedup.near_dup import check_near_duplicate
from dedup.registry import ConsortiumRegistry


def compute_outstanding_paise(ctx: CaseContext, registry: ConsortiumRegistry | None = None) -> int:
    """Compute outstanding receivable capacity in paise:
    `invoice.grand_total_paise - sum(payments) - sum(credit_notes) - allocations`.
    """
    inv_id = ctx.invoice.invoice_id
    payments_paise = 0
    if hasattr(ctx.history, "payments_for_invoice") and callable(ctx.history.payments_for_invoice):
        payments_paise = sum(p.amount_paise for p in ctx.history.payments_for_invoice(inv_id))

    credit_notes_paise = 0
    if hasattr(ctx.history, "credit_notes_for_invoice") and callable(ctx.history.credit_notes_for_invoice):
        credit_notes_paise = sum(cn.grand_total_paise for cn in ctx.history.credit_notes_for_invoice(inv_id))
    else:
        credit_notes_paise = sum(
            d.total_paise for d in ctx.evidence if d.doc_type == DocType.CREDIT_NOTE
        )

    hist_allocations = 0
    if hasattr(ctx.history, "allocations_for_invoice") and callable(ctx.history.allocations_for_invoice):
        hist_allocations = int(ctx.history.allocations_for_invoice(inv_id))

    reg_allocations = registry.get_allocated_paise(inv_id) if registry is not None else 0
    allocations_paise = max(hist_allocations, reg_allocations)

    return max(0, ctx.invoice.grand_total_paise - payments_paise - credit_notes_paise - allocations_paise)


def check_registry(ctx: CaseContext) -> CheckResult:
    """Execute `dedup.registry`: atomic consortium reservation, capacity check, and double-financing prevention."""
    check_id = "dedup.registry"
    source = _get_dedup_source()

    meta = (
        ctx.history.case_metadata()
        if hasattr(ctx.history, "case_metadata") and callable(ctx.history.case_metadata)
        else {}
    ) or {}
    unavailable = meta.get("unavailable_checks") or []
    if check_id in unavailable or meta.get("registry_unavailable") is True:
        return CheckResult(
            check=check_id,
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[
                f"Consortium registry is unreachable or unavailable to reserve invoice {ctx.invoice.invoice_id}."
            ],
        )

    # Resolve or create ConsortiumRegistry instance
    reg_obj = meta.get("registry")
    created_temp_registry = False
    if isinstance(reg_obj, ConsortiumRegistry):
        registry = reg_obj
    elif meta.get("registry_db_path"):
        registry = ConsortiumRegistry(meta["registry_db_path"])
        created_temp_registry = True
    else:
        registry = ConsortiumRegistry(":memory:")
        created_temp_registry = True

    try:
        # Seed historical reservations from ctx.history into the registry so active and stale reservations are evaluated
        seeded_reservations: list[Reservation] = []
        if hasattr(ctx.history, "reservations") and isinstance(ctx.history.reservations, list):
            seeded_reservations = list(ctx.history.reservations)
        elif hasattr(ctx.history, "active_reservations") and callable(ctx.history.active_reservations):
            seeded_reservations = list(ctx.history.active_reservations(ctx.version.invoice_identity))

        for r in seeded_reservations:
            registry.seed_reservation(r)

        outstanding_paise = compute_outstanding_paise(ctx, registry)
        seller_party = ctx.parties.get(ctx.invoice.seller_id)
        seller_pub_key = seller_party.public_key_hex if seller_party else None
        now_iso = meta.get("now_iso") or ctx.request.submitted_at

        op_id = hashlib.sha256(f"{ctx.request.request_id}|reserve".encode("utf-8")).hexdigest()
        res = registry.reserve(
            op_id=op_id,
            request=ctx.request,
            invoice_identity=ctx.version.invoice_identity,
            seller_public_key_hex=seller_pub_key,
            now_iso=now_iso,
            outstanding_paise=outstanding_paise,
        )

        if res.get("ok") is True:
            res_id = str(res["reservation_id"])
            return CheckResult(
                check=check_id,
                status=CheckStatus.PASS,
                severity=Severity.HARD,
                source=source,
                verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
                evidence=[
                    f"Consortium reservation {res_id} acquired for invoice {ctx.invoice.invoice_id} "
                    f"({format_inr(ctx.request.requested_amount_paise)} requested against "
                    f"{format_inr(outstanding_paise)} outstanding capacity, lender {ctx.request.lender_id})."
                ],
                refs=[res_id],
                details={
                    "reservation_id": res_id,
                    "outstanding_paise": outstanding_paise,
                    "requested_amount_paise": ctx.request.requested_amount_paise,
                    "state": res.get("state"),
                },
            )

        reason = str(res.get("reason", "conflict"))
        holder_state = str(res.get("state", "reserved"))
        held_by_other = bool(res.get("held_by_other", False))
        holder_lender = res.get("holder_lender")

        if reason == "above_outstanding":
            msg = (
                f"Financing request {ctx.request.request_id} for {format_inr(ctx.request.requested_amount_paise)} "
                f"exceeds remaining invoice capacity {format_inr(outstanding_paise)} on invoice {ctx.invoice.invoice_id}."
            )
        elif reason == "invalid_signature":
            msg = (
                f"Financing request {ctx.request.request_id} on invoice {ctx.invoice.invoice_id} "
                f"was rejected due to a missing or invalid seller Ed25519 signature."
            )
        elif reason == "already_settled" or holder_state == "settled":
            msg = (
                f"Invoice {ctx.invoice.invoice_id} (identity {ctx.version.invoice_identity[:16]}...) "
                f"is already in terminal settled state in the consortium registry and cannot be re-financed."
            )
        elif held_by_other:
            lender_str = f" by lender {holder_lender}" if holder_lender else " by another consortium lender"
            msg = (
                f"Duplicate financing blocked: Invoice {ctx.invoice.invoice_id} (identity {ctx.version.invoice_identity[:16]}...) "
                f"is already {holder_state}{lender_str} in the consortium registry."
            )
        else:
            msg = (
                f"Duplicate reservation blocked: Invoice {ctx.invoice.invoice_id} (identity {ctx.version.invoice_identity[:16]}...) "
                f"already has an active '{holder_state}' reservation for lender {ctx.request.lender_id}."
            )

        refs = [str(res["existing_reservation_id"])] if res.get("existing_reservation_id") else [ctx.request.request_id]
        return CheckResult(
            check=check_id,
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            source=source,
            verification_status=VerificationStatus.INDEPENDENTLY_CONFIRMED,
            evidence=[msg],
            refs=refs,
            details={
                "reason": reason,
                "state": holder_state,
                "held_by_other": held_by_other,
                "holder_lender": holder_lender,
                "outstanding_paise": outstanding_paise,
            },
        )
    finally:
        if created_temp_registry:
            registry.close()


def run(ctx: CaseContext) -> list[CheckResult]:
    """Run all `dedup.*` checks (`dedup.identity`, `dedup.near_dup`, `dedup.registry`)."""
    source = _get_dedup_source()
    meta = (
        ctx.history.case_metadata()
        if hasattr(ctx.history, "case_metadata") and callable(ctx.history.case_metadata)
        else {}
    ) or {}
    if source.value == "fixture" and "fixture_dedup_results" in meta:
        raw = meta["fixture_dedup_results"]
        return [
            r if isinstance(r, CheckResult) else CheckResult.model_validate(r)
            for r in raw
        ]

    return [
        classify_version(ctx),
        check_near_duplicate(ctx),
        check_registry(ctx),
    ]

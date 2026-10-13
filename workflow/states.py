"""Explicit request workflow state machine and audit event recorder (§3.7)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from contract.models import AuditEvent, RequestState, canonical_sha256


class IllegalStateTransition(ValueError):
    """Raised when an invalid RequestState transition is attempted."""


# §3.7 Explicit state transition graph:
# submitted -> extracted -> verified -> network_checked -> scored
#   -> {cond_approved, in_review, rejected}
#   -> buyer_accepted -> reservation_confirmed -> disbursed -> {repaid, overdue} -> settled
# plus disputed from any state after buyer_accepted (buyer_accepted, reservation_confirmed, disbursed, repaid, overdue).
ALLOWED_TRANSITIONS: dict[RequestState, frozenset[RequestState]] = {
    RequestState.SUBMITTED: frozenset({RequestState.EXTRACTED}),
    RequestState.EXTRACTED: frozenset({RequestState.VERIFIED}),
    RequestState.VERIFIED: frozenset({RequestState.NETWORK_CHECKED}),
    RequestState.NETWORK_CHECKED: frozenset({RequestState.SCORED}),
    RequestState.SCORED: frozenset(
        {
            RequestState.COND_APPROVED,
            RequestState.IN_REVIEW,
            RequestState.REJECTED,
        }
    ),
    RequestState.IN_REVIEW: frozenset(
        {
            RequestState.COND_APPROVED,
            RequestState.REJECTED,
        }
    ),
    RequestState.COND_APPROVED: frozenset(
        {
            RequestState.BUYER_ACCEPTED,
            RequestState.IN_REVIEW,
            RequestState.REJECTED,
        }
    ),
    RequestState.REJECTED: frozenset(),
    RequestState.BUYER_ACCEPTED: frozenset(
        {
            RequestState.RESERVATION_CONFIRMED,
            RequestState.DISPUTED,
        }
    ),
    RequestState.RESERVATION_CONFIRMED: frozenset(
        {
            RequestState.DISBURSED,
            RequestState.DISPUTED,
        }
    ),
    RequestState.DISBURSED: frozenset(
        {
            RequestState.REPAID,
            RequestState.OVERDUE,
            RequestState.DISPUTED,
        }
    ),
    RequestState.REPAID: frozenset(
        {
            RequestState.SETTLED,
            RequestState.DISPUTED,
        }
    ),
    RequestState.OVERDUE: frozenset(
        {
            RequestState.REPAID,
            RequestState.SETTLED,
            RequestState.DISPUTED,
        }
    ),
    RequestState.DISPUTED: frozenset({RequestState.SETTLED}),
    RequestState.SETTLED: frozenset(),
}

OVERRIDE_ALLOWED_TARGETS: frozenset[RequestState] = frozenset(
    {
        RequestState.COND_APPROVED,
        RequestState.IN_REVIEW,
        RequestState.REJECTED,
    }
)

_STATE_AUDIT_EVENTS: list[AuditEvent] = []


def get_recorded_state_events() -> list[AuditEvent]:
    """Return a copy of recorded workflow state transition AuditEvents."""
    return list(_STATE_AUDIT_EVENTS)


def clear_recorded_state_events() -> None:
    """Clear in-memory workflow state transition events (for unit testing)."""
    _STATE_AUDIT_EVENTS.clear()


def can_transition(
    current: RequestState,
    target: RequestState,
    *,
    is_override: bool = False,
) -> bool:
    """Return True if transitioning from `current` to `target` is permitted."""
    if is_override:
        if current in (
            RequestState.SCORED,
            RequestState.COND_APPROVED,
            RequestState.IN_REVIEW,
            RequestState.REJECTED,
        ) and target in OVERRIDE_ALLOWED_TARGETS:
            return current != target
        return False
    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    return target in allowed


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def transition_state(
    current: RequestState,
    target: RequestState,
    *,
    entity_id: str = "REQ-SYSTEM",
    actor_id: str = "workflow",
    reason: str | None = None,
    is_override: bool = False,
    timestamp: str | None = None,
    audit_log: list[AuditEvent] | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> RequestState:
    """Validate and execute a state transition, recording a hash-chained AuditEvent."""
    if not isinstance(current, RequestState):
        current = RequestState(current)
    if not isinstance(target, RequestState):
        target = RequestState(target)

    if not can_transition(current, target, is_override=is_override):
        raise IllegalStateTransition(
            f"Illegal workflow state transition: {current.value!r} -> {target.value!r} "
            f"(entity={entity_id}, is_override={is_override})"
        )

    sink = audit_log if audit_log is not None else _STATE_AUDIT_EVENTS
    seq = sink[-1].seq + 1 if sink else 1
    prev_hash = sink[-1].hash if sink else "0" * 64
    ts = timestamp or _now_iso()
    event_id = f"EVT-STATE-{seq:06d}"

    payload: dict[str, Any] = {
        "from_state": current.value,
        "to_state": target.value,
        "is_override": is_override,
    }
    if reason:
        payload["reason"] = reason
    if extra_payload:
        payload.update(extra_payload)

    unsigned_event = {
        "schema_version": 1,
        "seq": seq,
        "event_id": event_id,
        "event_type": "request.state_transition",
        "entity_id": entity_id,
        "actor_id": actor_id,
        "timestamp": ts,
        "payload": payload,
        "prev_hash": prev_hash,
    }
    event_hash = canonical_sha256(unsigned_event)
    event = AuditEvent(**unsigned_event, hash=event_hash)
    sink.append(event)
    return target

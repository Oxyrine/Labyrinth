"""SQLite WAL Consortium Registry with BEGIN IMMEDIATE, partial unique index, expire-on-read, and Ed25519 verification."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from contract.models import (
    AuditEvent,
    BuyerAcceptance,
    FinancingRequest,
    Reservation,
    ReservationState,
    canonical_json,
    canonical_sha256,
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS reservations (
    reservation_id TEXT PRIMARY KEY,
    invoice_identity TEXT NOT NULL,
    request_id TEXT NOT NULL,
    lender_id TEXT NOT NULL,
    seller_id TEXT NOT NULL,
    amount_paise INTEGER NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_active_invoice_identity
ON reservations(invoice_identity)
WHERE state IN ('reserved', 'financed', 'settled');

CREATE TABLE IF NOT EXISTS ops (
    op_id TEXT PRIMARY KEY,
    result_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS allocations (
    invoice_id TEXT PRIMARY KEY,
    allocated_paise INTEGER NOT NULL DEFAULT 0
);
"""


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso_utc(iso_str: str) -> datetime:
    s = iso_str.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _add_hours_iso(iso_str: str, hours: int) -> str:
    dt = _parse_iso_utc(iso_str) + timedelta(hours=hours)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coerce_private_key(private_key: Ed25519PrivateKey | bytes | str) -> Ed25519PrivateKey:
    if isinstance(private_key, Ed25519PrivateKey):
        return private_key
    if isinstance(private_key, str):
        return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key.strip()))
    return Ed25519PrivateKey.from_private_bytes(private_key)


def _strip_sig_fields(data: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in data.items()
        if k not in ("seller_signature_hex", "signature_hex")
    }


def sign_payload(payload: Any, private_key: Ed25519PrivateKey | bytes | str) -> str:
    """Sign canonical JSON of payload (with signature fields stripped) using Ed25519."""
    priv = _coerce_private_key(private_key)
    if hasattr(payload, "model_dump"):
        raw = payload.model_dump(mode="json")
    elif isinstance(payload, dict):
        raw = dict(payload)
    else:
        msg = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
        return priv.sign(msg).hex()

    cleaned = _strip_sig_fields(raw)
    msg = canonical_json(cleaned, allow_floats=True).encode("utf-8")
    return priv.sign(msg).hex()


def sign_financing_request(
    request: FinancingRequest | dict[str, Any],
    private_key: Ed25519PrivateKey | bytes | str,
) -> str:
    """Sign a FinancingRequest with the seller's Ed25519 private key."""
    return sign_payload(request, private_key)


def sign_buyer_acceptance(
    acceptance: BuyerAcceptance | dict[str, Any],
    private_key: Ed25519PrivateKey | bytes | str,
) -> str:
    """Sign a BuyerAcceptance with the buyer's Ed25519 private key."""
    return sign_payload(acceptance, private_key)


def verify_ed25519_signature(
    payload: Any,
    signature_hex: str | None,
    public_key_hex: str | None = None,
    require_sig: bool = True,
) -> bool:
    """Verify Ed25519 signature over canonical JSON when public_key_hex is provided,
    and reject missing/empty/malformed signatures whenever require_sig=True.
    """
    if not signature_hex or not str(signature_hex).strip():
        return not require_sig

    sig_str = str(signature_hex).strip()
    try:
        sig_bytes = bytes.fromhex(sig_str)
    except ValueError:
        return False

    if len(sig_bytes) < 4:
        return False

    if not public_key_hex or not str(public_key_hex).strip():
        # No cryptographic public key supplied; syntactic non-empty hex check passed
        return True

    try:
        pub_bytes = bytes.fromhex(str(public_key_hex).strip())
        pub_key = Ed25519PublicKey.from_public_bytes(pub_bytes)
    except Exception:
        return False

    candidates: list[bytes] = []
    if hasattr(payload, "model_dump"):
        d = payload.model_dump(mode="json")
    elif isinstance(payload, dict):
        d = dict(payload)
    else:
        d = None

    if d is not None:
        # 1. Dict without signature field (standard canonical signing representation)
        candidates.append(canonical_json(_strip_sig_fields(d), allow_floats=True).encode("utf-8"))
        # 2. Dict with empty signature field
        d_empty = dict(d)
        if "seller_signature_hex" in d_empty:
            d_empty["seller_signature_hex"] = ""
        if "signature_hex" in d_empty:
            d_empty["signature_hex"] = ""
        candidates.append(canonical_json(d_empty, allow_floats=True).encode("utf-8"))
        # 3. Full dict as-is
        candidates.append(canonical_json(d, allow_floats=True).encode("utf-8"))
    elif isinstance(payload, str):
        candidates.append(payload.encode("utf-8"))
    elif isinstance(payload, bytes):
        candidates.append(payload)

    for msg in candidates:
        try:
            pub_key.verify(sig_bytes, msg)
            return True
        except InvalidSignature:
            continue
        except Exception:
            continue

    return False


class ConsortiumRegistry:
    """SQLite WAL Consortium Registry with atomic BEGIN IMMEDIATE reservations and idempotency."""

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        audit_callback: Callable[[AuditEvent], None] | None = None,
    ) -> None:
        self.db_path = str(db_path)
        self._is_memory = self.db_path == ":memory:" or self.db_path.startswith("file::memory:")
        self._lock = threading.RLock()
        self._local = threading.local()
        self._all_connections: list[sqlite3.Connection] = []
        self._mem_conn: sqlite3.Connection | None = None
        self.audit_callback = audit_callback
        self.audit_events: list[AuditEvent] = []
        self._audit_lock = threading.Lock()

        conn = self._get_conn()
        with self._lock:
            self._init_schema(conn)

    def _create_raw_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        self._all_connections.append(conn)
        return conn

    def _get_conn(self) -> sqlite3.Connection:
        if self._is_memory:
            if self._mem_conn is None:
                self._mem_conn = self._create_raw_conn()
            return self._mem_conn
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._create_raw_conn()
            self._local.conn = conn
        return conn

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        for stmt in _SCHEMA_SQL.strip().split(";"):
            s = stmt.strip()
            if s:
                conn.execute(s)

    def _begin_immediate(self, conn: sqlite3.Connection) -> None:
        """Start an explicit BEGIN IMMEDIATE transaction with retry on transient lock."""
        for attempt in range(100):
            try:
                conn.execute("BEGIN IMMEDIATE")
                return
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if ("locked" in msg or "busy" in msg) and attempt < 99:
                    time.sleep(0.001 * (attempt + 1))
                    continue
                raise

    def _emit_audit(
        self,
        event_type: str,
        entity_id: str,
        actor_id: str,
        payload: dict[str, Any],
        timestamp: str | None = None,
    ) -> AuditEvent:
        ts = timestamp or _utc_now_iso()
        with self._audit_lock:
            seq = len(self.audit_events) + 1
            prev_hash = self.audit_events[-1].hash if self.audit_events else "0" * 64
            event_id = f"EVT-DEDUP-{seq:06d}"
            body = {
                "seq": seq,
                "event_id": event_id,
                "event_type": event_type,
                "entity_id": entity_id,
                "actor_id": actor_id,
                "timestamp": ts,
                "payload": payload,
                "prev_hash": prev_hash,
            }
            digest = canonical_sha256(body, allow_floats=True)
            event = AuditEvent(
                seq=seq,
                event_id=event_id,
                event_type=event_type,
                entity_id=entity_id,
                actor_id=actor_id,
                timestamp=ts,
                payload=payload,
                prev_hash=prev_hash,
                hash=digest,
            )
            self.audit_events.append(event)

        if self.audit_callback is not None:
            self.audit_callback(event)
        return event

    def seed_reservation(self, reservation: Reservation) -> None:
        """Seed an existing Reservation model into the database (idempotent)."""
        conn = self._get_conn()
        with self._lock if self._is_memory else threading.Lock():
            self._begin_immediate(conn)
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO reservations(
                        reservation_id, invoice_identity, request_id, lender_id,
                        seller_id, amount_paise, state, created_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        reservation.reservation_id,
                        reservation.invoice_identity,
                        reservation.request_id,
                        reservation.lender_id,
                        reservation.seller_id,
                        reservation.amount_paise,
                        reservation.state.value if isinstance(reservation.state, ReservationState) else str(reservation.state),
                        reservation.created_at,
                        reservation.expires_at,
                    ),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def reserve(
        self,
        op_id: str,
        request: FinancingRequest | dict[str, Any],
        invoice_identity: str,
        seller_public_key_hex: str | None = None,
        now_iso: str | None = None,
        ttl_hours: int = 24,
        outstanding_paise: int | None = None,
    ) -> dict[str, Any]:
        """Atomically reserve an invoice identity in the consortium registry."""
        if isinstance(request, FinancingRequest):
            req_id = request.request_id
            lender_id = request.lender_id
            seller_id = request.seller_id
            amount_paise = request.requested_amount_paise
            sig_hex = request.seller_signature_hex
            submitted_at = request.submitted_at
        else:
            req_id = str(request["request_id"])
            lender_id = str(request["lender_id"])
            seller_id = str(request["seller_id"])
            amount_paise = int(request["requested_amount_paise"])
            sig_hex = request.get("seller_signature_hex", "")
            submitted_at = str(request.get("submitted_at") or _utc_now_iso())

        effective_now = now_iso or submitted_at or _utc_now_iso()
        expires_at = _add_hours_iso(effective_now, ttl_hours)

        conn = self._get_conn()
        ctx_lock = self._lock if self._is_memory else None
        if ctx_lock is not None:
            ctx_lock.acquire()
        try:
            self._begin_immediate(conn)
            try:
                # 1. Idempotency check in ops table
                op_row = conn.execute(
                    "SELECT result_json FROM ops WHERE op_id = ?",
                    (op_id,),
                ).fetchone()
                if op_row is not None:
                    conn.execute("COMMIT")
                    return json.loads(op_row["result_json"])

                # 2. Seller signature verification
                if not verify_ed25519_signature(
                    request,
                    sig_hex,
                    public_key_hex=seller_public_key_hex,
                    require_sig=True,
                ):
                    result = {
                        "ok": False,
                        "reason": "invalid_signature",
                        "invoice_identity": invoice_identity,
                        "request_id": req_id,
                        "lender_id": lender_id,
                    }
                    conn.execute(
                        "INSERT INTO ops(op_id, result_json) VALUES (?, ?)",
                        (op_id, json.dumps(result, sort_keys=True)),
                    )
                    conn.execute("COMMIT")
                    self._emit_audit(
                        "reservation.rejected_signature",
                        req_id,
                        seller_id,
                        result,
                        effective_now,
                    )
                    return result

                # 3. Capacity check against outstanding_paise
                if outstanding_paise is not None and amount_paise > outstanding_paise:
                    result = {
                        "ok": False,
                        "reason": "above_outstanding",
                        "invoice_identity": invoice_identity,
                        "request_id": req_id,
                        "lender_id": lender_id,
                        "requested_amount_paise": amount_paise,
                        "outstanding_paise": outstanding_paise,
                    }
                    conn.execute(
                        "INSERT INTO ops(op_id, result_json) VALUES (?, ?)",
                        (op_id, json.dumps(result, sort_keys=True)),
                    )
                    conn.execute("COMMIT")
                    self._emit_audit(
                        "reservation.rejected_capacity",
                        req_id,
                        lender_id,
                        result,
                        effective_now,
                    )
                    return result

                # 4. Expire-on-read inside BEGIN IMMEDIATE so stale expired rows never block a new reserve
                conn.execute(
                    """
                    UPDATE reservations
                    SET state = 'expired'
                    WHERE invoice_identity = ?
                      AND state = 'reserved'
                      AND expires_at <= ?
                    """,
                    (invoice_identity, effective_now),
                )

                # 5. Attempt atomic INSERT protected by partial unique index idx_active_invoice_identity
                reservation_id = f"RSV-{hashlib.sha256(f'{op_id}|{req_id}|{uuid.uuid4().hex}'.encode()).hexdigest()[:12].upper()}"
                try:
                    conn.execute(
                        """
                        INSERT INTO reservations(
                            reservation_id, invoice_identity, request_id, lender_id,
                            seller_id, amount_paise, state, created_at, expires_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 'reserved', ?, ?)
                        """,
                        (
                            reservation_id,
                            invoice_identity,
                            req_id,
                            lender_id,
                            seller_id,
                            amount_paise,
                            effective_now,
                            expires_at,
                        ),
                    )
                    result = {
                        "ok": True,
                        "reservation_id": reservation_id,
                        "invoice_identity": invoice_identity,
                        "request_id": req_id,
                        "lender_id": lender_id,
                        "seller_id": seller_id,
                        "amount_paise": amount_paise,
                        "state": ReservationState.RESERVED.value,
                        "created_at": effective_now,
                        "expires_at": expires_at,
                    }
                    conn.execute(
                        "INSERT INTO ops(op_id, result_json) VALUES (?, ?)",
                        (op_id, json.dumps(result, sort_keys=True)),
                    )
                    conn.execute("COMMIT")
                except sqlite3.IntegrityError:
                    holder = conn.execute(
                        """
                        SELECT reservation_id, lender_id, state, amount_paise, request_id, created_at, expires_at
                        FROM reservations
                        WHERE invoice_identity = ?
                          AND state IN ('reserved', 'financed', 'settled')
                        LIMIT 1
                        """,
                        (invoice_identity,),
                    ).fetchone()
                    holder_lender = str(holder["lender_id"]) if holder else "UNKNOWN"
                    holder_state = str(holder["state"]) if holder else "reserved"
                    holder_res_id = str(holder["reservation_id"]) if holder else None
                    held_by_other = holder_lender != lender_id
                    disclose = os.environ.get("LABYRINTH_DISCLOSE_LENDER") == "1"

                    if holder_state == ReservationState.SETTLED.value:
                        reason = "already_settled"
                    elif held_by_other:
                        reason = "held_by_other_lender"
                    else:
                        reason = "already_reserved_same_lender"

                    result = {
                        "ok": False,
                        "reason": reason,
                        "held_by_other": held_by_other,
                        "state": holder_state,
                        "holder_lender": holder_lender if disclose else None,
                        "existing_reservation_id": holder_res_id if (not held_by_other or disclose) else None,
                        "invoice_identity": invoice_identity,
                        "request_id": req_id,
                        "lender_id": lender_id,
                    }
                    conn.execute(
                        "INSERT INTO ops(op_id, result_json) VALUES (?, ?)",
                        (op_id, json.dumps(result, sort_keys=True)),
                    )
                    conn.execute("COMMIT")
                    self._emit_audit(
                        "reservation.conflict",
                        req_id,
                        lender_id,
                        result,
                        effective_now,
                    )
                    return result

            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            if ctx_lock is not None:
                ctx_lock.release()

        self._emit_audit(
            "reservation.reserved",
            reservation_id,
            lender_id,
            result,
            effective_now,
        )
        return result

    def confirm(
        self,
        op_id: str,
        reservation_id: str,
        buyer_acceptance: BuyerAcceptance | dict[str, Any],
        outstanding_paise: int | None = None,
        buyer_public_key_hex: str | None = None,
        now_iso: str | None = None,
    ) -> dict[str, Any]:
        """Confirm a reservation into 'financed' state upon buyer acceptance."""
        if isinstance(buyer_acceptance, BuyerAcceptance):
            decision = buyer_acceptance.decision
            accepted_amount = buyer_acceptance.accepted_amount_paise
            accepted_at = buyer_acceptance.accepted_at
            buyer_id = buyer_acceptance.buyer_id
            invoice_id = buyer_acceptance.invoice_id
            sig_hex = buyer_acceptance.signature_hex
        else:
            decision = str(buyer_acceptance.get("decision", "accept"))
            accepted_amount = int(buyer_acceptance.get("accepted_amount_paise", 0))
            accepted_at = str(buyer_acceptance.get("accepted_at") or _utc_now_iso())
            buyer_id = str(buyer_acceptance.get("buyer_id", "BUYER"))
            invoice_id = str(buyer_acceptance.get("invoice_id", reservation_id))
            sig_hex = buyer_acceptance.get("signature_hex", "")

        effective_now = now_iso or accepted_at or _utc_now_iso()

        conn = self._get_conn()
        ctx_lock = self._lock if self._is_memory else None
        if ctx_lock is not None:
            ctx_lock.acquire()
        try:
            self._begin_immediate(conn)
            try:
                # 1. Idempotency check in ops
                op_row = conn.execute(
                    "SELECT result_json FROM ops WHERE op_id = ?",
                    (op_id,),
                ).fetchone()
                if op_row is not None:
                    conn.execute("COMMIT")
                    return json.loads(op_row["result_json"])

                # 2. Fetch reservation
                row = conn.execute(
                    """
                    SELECT reservation_id, invoice_identity, request_id, lender_id,
                           seller_id, amount_paise, state, created_at, expires_at
                    FROM reservations
                    WHERE reservation_id = ?
                    """,
                    (reservation_id,),
                ).fetchone()
                if row is None:
                    result = {
                        "ok": False,
                        "reason": "reservation_not_found",
                        "reservation_id": reservation_id,
                    }
                    conn.execute("COMMIT")
                    return result

                row_state = str(row["state"])
                row_amount = int(row["amount_paise"])
                row_expires = str(row["expires_at"])
                row_lender = str(row["lender_id"])

                # 3. Buyer signature verification
                if not verify_ed25519_signature(
                    buyer_acceptance,
                    sig_hex,
                    public_key_hex=buyer_public_key_hex,
                    require_sig=True,
                ):
                    result = {
                        "ok": False,
                        "reason": "invalid_buyer_signature",
                        "reservation_id": reservation_id,
                        "state": row_state,
                    }
                    conn.execute(
                        "INSERT INTO ops(op_id, result_json) VALUES (?, ?)",
                        (op_id, json.dumps(result, sort_keys=True)),
                    )
                    conn.execute("COMMIT")
                    self._emit_audit("reservation.confirm_failed_sig", reservation_id, buyer_id, result, effective_now)
                    return result

                # 4. If buyer decision is 'reject', auto-cancel reservation
                if decision != "accept":
                    conn.execute(
                        "UPDATE reservations SET state = 'cancelled' WHERE reservation_id = ?",
                        (reservation_id,),
                    )
                    result = {
                        "ok": False,
                        "reason": "buyer_rejected",
                        "reservation_id": reservation_id,
                        "state": ReservationState.CANCELLED.value,
                    }
                    conn.execute(
                        "INSERT INTO ops(op_id, result_json) VALUES (?, ?)",
                        (op_id, json.dumps(result, sort_keys=True)),
                    )
                    conn.execute("COMMIT")
                    self._emit_audit("reservation.cancelled", reservation_id, buyer_id, result, effective_now)
                    return result

                # 5. Check expiration
                if row_state == ReservationState.EXPIRED.value or effective_now > row_expires:
                    conn.execute(
                        "UPDATE reservations SET state = 'expired' WHERE reservation_id = ?",
                        (reservation_id,),
                    )
                    result = {
                        "ok": False,
                        "reason": "reservation_expired",
                        "reservation_id": reservation_id,
                        "state": ReservationState.EXPIRED.value,
                        "expires_at": row_expires,
                        "now_iso": effective_now,
                    }
                    conn.execute(
                        "INSERT INTO ops(op_id, result_json) VALUES (?, ?)",
                        (op_id, json.dumps(result, sort_keys=True)),
                    )
                    conn.execute("COMMIT")
                    self._emit_audit("reservation.expired", reservation_id, buyer_id, result, effective_now)
                    return result

                if row_state != ReservationState.RESERVED.value:
                    result = {
                        "ok": False,
                        "reason": f"invalid_state_{row_state}",
                        "reservation_id": reservation_id,
                        "state": row_state,
                    }
                    conn.execute("COMMIT")
                    return result

                # 6. Capacity verification
                if outstanding_paise is not None and row_amount > outstanding_paise:
                    result = {
                        "ok": False,
                        "reason": "above_outstanding",
                        "reservation_id": reservation_id,
                        "amount_paise": row_amount,
                        "outstanding_paise": outstanding_paise,
                    }
                    conn.execute(
                        "INSERT INTO ops(op_id, result_json) VALUES (?, ?)",
                        (op_id, json.dumps(result, sort_keys=True)),
                    )
                    conn.execute("COMMIT")
                    return result

                if accepted_amount > 0 and row_amount > accepted_amount:
                    result = {
                        "ok": False,
                        "reason": "above_buyer_accepted_amount",
                        "reservation_id": reservation_id,
                        "amount_paise": row_amount,
                        "accepted_amount_paise": accepted_amount,
                    }
                    conn.execute(
                        "INSERT INTO ops(op_id, result_json) VALUES (?, ?)",
                        (op_id, json.dumps(result, sort_keys=True)),
                    )
                    conn.execute("COMMIT")
                    return result

                # 7. Atomically transition to 'financed' and increment allocations
                conn.execute(
                    "UPDATE reservations SET state = 'financed' WHERE reservation_id = ?",
                    (reservation_id,),
                )
                conn.execute(
                    """
                    INSERT INTO allocations(invoice_id, allocated_paise)
                    VALUES (?, ?)
                    ON CONFLICT(invoice_id) DO UPDATE SET
                        allocated_paise = allocated_paise + excluded.allocated_paise
                    """,
                    (invoice_id, row_amount),
                )
                alloc_row = conn.execute(
                    "SELECT allocated_paise FROM allocations WHERE invoice_id = ?",
                    (invoice_id,),
                ).fetchone()
                total_allocated = int(alloc_row["allocated_paise"]) if alloc_row else row_amount

                result = {
                    "ok": True,
                    "reservation_id": reservation_id,
                    "invoice_id": invoice_id,
                    "lender_id": row_lender,
                    "amount_paise": row_amount,
                    "allocated_paise": total_allocated,
                    "state": ReservationState.FINANCED.value,
                    "confirmed_at": effective_now,
                }
                conn.execute(
                    "INSERT INTO ops(op_id, result_json) VALUES (?, ?)",
                    (op_id, json.dumps(result, sort_keys=True)),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            if ctx_lock is not None:
                ctx_lock.release()

        self._emit_audit("reservation.financed", reservation_id, buyer_id, result, effective_now)
        return result

    def settle(
        self,
        op_id: str,
        reservation_id: str,
        now_iso: str | None = None,
    ) -> dict[str, Any]:
        """Transition a reservation to terminal 'settled' state (keeps invoice_identity permanently locked)."""
        effective_now = now_iso or _utc_now_iso()
        conn = self._get_conn()
        ctx_lock = self._lock if self._is_memory else None
        if ctx_lock is not None:
            ctx_lock.acquire()
        try:
            self._begin_immediate(conn)
            try:
                op_row = conn.execute("SELECT result_json FROM ops WHERE op_id = ?", (op_id,)).fetchone()
                if op_row is not None:
                    conn.execute("COMMIT")
                    return json.loads(op_row["result_json"])

                row = conn.execute(
                    "SELECT reservation_id, invoice_identity, lender_id, state FROM reservations WHERE reservation_id = ?",
                    (reservation_id,),
                ).fetchone()
                if row is None:
                    conn.execute("COMMIT")
                    return {"ok": False, "reason": "reservation_not_found", "reservation_id": reservation_id}

                conn.execute(
                    "UPDATE reservations SET state = 'settled' WHERE reservation_id = ?",
                    (reservation_id,),
                )
                result = {
                    "ok": True,
                    "reservation_id": reservation_id,
                    "invoice_identity": str(row["invoice_identity"]),
                    "state": ReservationState.SETTLED.value,
                    "settled_at": effective_now,
                }
                conn.execute(
                    "INSERT INTO ops(op_id, result_json) VALUES (?, ?)",
                    (op_id, json.dumps(result, sort_keys=True)),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            if ctx_lock is not None:
                ctx_lock.release()

        self._emit_audit("reservation.settled", reservation_id, str(row["lender_id"]), result, effective_now)
        return result

    def cancel(
        self,
        op_id: str,
        reservation_id: str,
        reason: str = "cancelled",
        now_iso: str | None = None,
    ) -> dict[str, Any]:
        """Cancel a reservation (marks state='cancelled', freeing the partial unique index for subsequent reservations)."""
        effective_now = now_iso or _utc_now_iso()
        conn = self._get_conn()
        ctx_lock = self._lock if self._is_memory else None
        if ctx_lock is not None:
            ctx_lock.acquire()
        try:
            self._begin_immediate(conn)
            try:
                op_row = conn.execute("SELECT result_json FROM ops WHERE op_id = ?", (op_id,)).fetchone()
                if op_row is not None:
                    conn.execute("COMMIT")
                    return json.loads(op_row["result_json"])

                row = conn.execute(
                    "SELECT reservation_id, invoice_identity, lender_id, state FROM reservations WHERE reservation_id = ?",
                    (reservation_id,),
                ).fetchone()
                if row is None:
                    conn.execute("COMMIT")
                    return {"ok": False, "reason": "reservation_not_found", "reservation_id": reservation_id}

                if str(row["state"]) == ReservationState.SETTLED.value:
                    conn.execute("COMMIT")
                    return {
                        "ok": False,
                        "reason": "cannot_cancel_settled",
                        "reservation_id": reservation_id,
                        "state": ReservationState.SETTLED.value,
                    }

                conn.execute(
                    "UPDATE reservations SET state = 'cancelled' WHERE reservation_id = ?",
                    (reservation_id,),
                )
                result = {
                    "ok": True,
                    "reservation_id": reservation_id,
                    "invoice_identity": str(row["invoice_identity"]),
                    "state": ReservationState.CANCELLED.value,
                    "reason": reason,
                    "cancelled_at": effective_now,
                }
                conn.execute(
                    "INSERT INTO ops(op_id, result_json) VALUES (?, ?)",
                    (op_id, json.dumps(result, sort_keys=True)),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            if ctx_lock is not None:
                ctx_lock.release()

        self._emit_audit("reservation.cancelled", reservation_id, str(row["lender_id"]), result, effective_now)
        return result

    def reject(
        self,
        op_id: str,
        reservation_id: str,
        now_iso: str | None = None,
    ) -> dict[str, Any]:
        """Auto-cancel a reservation when a request is rejected, freeing the partial unique index."""
        return self.cancel(op_id=op_id, reservation_id=reservation_id, reason="rejected", now_iso=now_iso)

    def expire_sweep(self, now_iso: str | None = None) -> int:
        """Mark all 'reserved' rows with expires_at <= now_iso as 'expired' and return count."""
        effective_now = now_iso or _utc_now_iso()
        conn = self._get_conn()
        ctx_lock = self._lock if self._is_memory else None
        if ctx_lock is not None:
            ctx_lock.acquire()
        expired_ids: list[str] = []
        try:
            self._begin_immediate(conn)
            try:
                rows = conn.execute(
                    """
                    SELECT reservation_id
                    FROM reservations
                    WHERE state = 'reserved' AND expires_at <= ?
                    """,
                    (effective_now,),
                ).fetchall()
                expired_ids = [str(r["reservation_id"]) for r in rows]
                if expired_ids:
                    conn.execute(
                        """
                        UPDATE reservations
                        SET state = 'expired'
                        WHERE state = 'reserved' AND expires_at <= ?
                        """,
                        (effective_now,),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            if ctx_lock is not None:
                ctx_lock.release()

        for rid in expired_ids:
            self._emit_audit(
                "reservation.expired_sweep",
                rid,
                "SYSTEM",
                {"reservation_id": rid, "state": ReservationState.EXPIRED.value, "expired_at": effective_now},
                effective_now,
            )
        return len(expired_ids)

    def get_allocated_paise(self, invoice_id: str) -> int:
        """Return total financed allocation in paise for the given invoice_id."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT allocated_paise FROM allocations WHERE invoice_id = ?",
            (invoice_id,),
        ).fetchone()
        return int(row["allocated_paise"]) if row else 0

    def get_reservation(self, reservation_id: str) -> Reservation | None:
        """Fetch a Reservation model by reservation_id."""
        conn = self._get_conn()
        row = conn.execute(
            """
            SELECT reservation_id, invoice_identity, request_id, lender_id,
                   seller_id, amount_paise, state, created_at, expires_at
            FROM reservations
            WHERE reservation_id = ?
            """,
            (reservation_id,),
        ).fetchone()
        if row is None:
            return None
        return Reservation(
            reservation_id=str(row["reservation_id"]),
            invoice_identity=str(row["invoice_identity"]),
            request_id=str(row["request_id"]),
            lender_id=str(row["lender_id"]),
            seller_id=str(row["seller_id"]),
            amount_paise=int(row["amount_paise"]),
            state=ReservationState(str(row["state"])),
            created_at=str(row["created_at"]),
            expires_at=str(row["expires_at"]),
        )

    def active_reservations(self, invoice_identity: str) -> list[Reservation]:
        """Return all active ('reserved', 'financed', 'settled') reservations for an invoice_identity."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT reservation_id, invoice_identity, request_id, lender_id,
                   seller_id, amount_paise, state, created_at, expires_at
            FROM reservations
            WHERE invoice_identity = ?
              AND state IN ('reserved', 'financed', 'settled')
            """,
            (invoice_identity,),
        ).fetchall()
        return [
            Reservation(
                reservation_id=str(r["reservation_id"]),
                invoice_identity=str(r["invoice_identity"]),
                request_id=str(r["request_id"]),
                lender_id=str(r["lender_id"]),
                seller_id=str(r["seller_id"]),
                amount_paise=int(r["amount_paise"]),
                state=ReservationState(str(r["state"])),
                created_at=str(r["created_at"]),
                expires_at=str(r["expires_at"]),
            )
            for r in rows
        ]

    def close(self) -> None:
        """Close all open SQLite connections managed by this registry instance."""
        for conn in self._all_connections:
            try:
                conn.close()
            except Exception:
                pass
        self._all_connections.clear()
        self._mem_conn = None
        if hasattr(self._local, "conn"):
            self._local.conn = None

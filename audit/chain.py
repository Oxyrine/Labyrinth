"""Append-only SQLite event hash chain, anchor batch tracking, and lender head checkpoints."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from audit.merkle import build_event_batch_tree
from contract.models import SCHEMA_VERSION, AuditEvent, canonical_json, canonical_sha256

GENESIS_PREV_HASH = "0" * 64


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_event_hash(
    *,
    seq: int,
    event_id: str,
    event_type: str,
    entity_id: str,
    actor_id: str,
    timestamp: str,
    payload: dict[str, Any],
    prev_hash: str,
    schema_version: int = SCHEMA_VERSION,
) -> str:
    """Compute canonical SHA-256 hash for an AuditEvent."""
    envelope = {
        "schema_version": int(schema_version),
        "seq": int(seq),
        "event_id": str(event_id),
        "event_type": str(event_type),
        "entity_id": str(entity_id),
        "actor_id": str(actor_id),
        "timestamp": str(timestamp),
        "payload": payload,
        "prev_hash": str(prev_hash),
    }
    return canonical_sha256(envelope, allow_floats=True)


class EventChain:
    """Append-only SQLite hash chain with `BEGIN IMMEDIATE` write serialization."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.db_path,
            isolation_level=None,  # Autocommit mode so we control `BEGIN IMMEDIATE` explicitly
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT,
                    event_type TEXT,
                    entity_id TEXT,
                    actor_id TEXT,
                    timestamp TEXT,
                    payload_json TEXT,
                    prev_hash TEXT,
                    hash TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS anchor_batches (
                    batch_id TEXT PRIMARY KEY,
                    first_seq INTEGER,
                    last_seq INTEGER,
                    root TEXT,
                    head_hash TEXT,
                    tx_hash TEXT,
                    status TEXT,
                    anchored_at TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS lender_heads (
                    lender_id TEXT PRIMARY KEY,
                    last_seq INTEGER,
                    last_hash TEXT
                )
                """
            )

    def append(
        self,
        event_type: str | dict[str, Any] | AuditEvent,
        entity_id: str = "",
        actor_id: str = "system",
        payload: dict[str, Any] | None = None,
        *,
        event_id: str | None = None,
        timestamp: str | None = None,
        schema_version: int = SCHEMA_VERSION,
        sync_lender_ids: list[str] | None = None,
    ) -> AuditEvent:
        """Append an event under SQLite `BEGIN IMMEDIATE` write lock."""
        if isinstance(event_type, AuditEvent):
            ev_obj = event_type
            event_id = ev_obj.event_id
            entity_id = ev_obj.entity_id
            actor_id = ev_obj.actor_id
            payload = dict(ev_obj.payload)
            timestamp = ev_obj.timestamp
            schema_version = ev_obj.schema_version
            ev_type_str = ev_obj.event_type
        elif isinstance(event_type, dict):
            d = event_type
            ev_type_str = str(d["event_type"])
            entity_id = str(d.get("entity_id", entity_id))
            actor_id = str(d.get("actor_id", actor_id))
            payload = dict(d.get("payload") or {})
            event_id = d.get("event_id", event_id)
            timestamp = d.get("timestamp", timestamp)
            schema_version = int(d.get("schema_version", schema_version))
        else:
            ev_type_str = str(event_type)

        payload_dict: dict[str, Any] = dict(payload) if payload is not None else {}
        ts = timestamp or _utc_now_iso()

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                if row is None:
                    next_seq = 1
                    prev_hash = GENESIS_PREV_HASH
                else:
                    next_seq = int(row["seq"]) + 1
                    prev_hash = str(row["hash"])

                ev_id = event_id or f"EVT-{next_seq:06d}"
                payload_str = canonical_json(payload_dict, allow_floats=True)
                ev_hash = compute_event_hash(
                    seq=next_seq,
                    event_id=ev_id,
                    event_type=ev_type_str,
                    entity_id=entity_id,
                    actor_id=actor_id,
                    timestamp=ts,
                    payload=json.loads(payload_str),
                    prev_hash=prev_hash,
                    schema_version=schema_version,
                )

                self._conn.execute(
                    """
                    INSERT INTO events (
                        seq, event_id, event_type, entity_id, actor_id,
                        timestamp, payload_json, prev_hash, hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        next_seq,
                        ev_id,
                        ev_type_str,
                        entity_id,
                        actor_id,
                        ts,
                        payload_str,
                        prev_hash,
                        ev_hash,
                    ),
                )

                if sync_lender_ids:
                    for lid in sync_lender_ids:
                        self._conn.execute(
                            """
                            INSERT INTO lender_heads (lender_id, last_seq, last_hash)
                            VALUES (?, ?, ?)
                            ON CONFLICT(lender_id) DO UPDATE SET
                                last_seq = excluded.last_seq,
                                last_hash = excluded.last_hash
                            """,
                            (lid, next_seq, ev_hash),
                        )

                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        return AuditEvent(
            schema_version=schema_version,
            seq=next_seq,
            event_id=ev_id,
            event_type=ev_type_str,
            entity_id=entity_id,
            actor_id=actor_id,
            timestamp=ts,
            payload=json.loads(payload_str),
            prev_hash=prev_hash,
            hash=ev_hash,
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> AuditEvent:
        payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        return AuditEvent(
            schema_version=SCHEMA_VERSION,
            seq=int(row["seq"]),
            event_id=str(row["event_id"]),
            event_type=str(row["event_type"]),
            entity_id=str(row["entity_id"]),
            actor_id=str(row["actor_id"]),
            timestamp=str(row["timestamp"]),
            payload=payload,
            prev_hash=str(row["prev_hash"]),
            hash=str(row["hash"]),
        )

    def get_event(self, seq: int) -> AuditEvent | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM events WHERE seq = ?", (seq,)).fetchone()
            return self._row_to_event(row) if row is not None else None

    def get_event_by_id(self, event_id: str) -> AuditEvent | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
            return self._row_to_event(row) if row is not None else None

    def get_all_events(self) -> list[AuditEvent]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM events ORDER BY seq ASC").fetchall()
            return [self._row_to_event(r) for r in rows]

    def get_events_range(self, first_seq: int, last_seq: int) -> list[AuditEvent]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE seq >= ? AND seq <= ? ORDER BY seq ASC",
                (first_seq, last_seq),
            ).fetchall()
            return [self._row_to_event(r) for r in rows]

    def head(self) -> tuple[int, str]:
        with self._lock:
            row = self._conn.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            if row is None:
                return 0, GENESIS_PREV_HASH
            return int(row["seq"]), str(row["hash"])

    def get_last_anchored_seq(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(last_seq) AS max_seq FROM anchor_batches WHERE status = 'anchored'"
            ).fetchone()
            if row is None or row["max_seq"] is None:
                return 0
            return int(row["max_seq"])

    def get_unanchored_events(self, limit: int | None = None) -> list[AuditEvent]:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(last_seq) AS max_seq FROM anchor_batches WHERE status = 'anchored'"
            ).fetchone()
            last_anchored = int(row["max_seq"]) if (row and row["max_seq"] is not None) else 0
            if limit is not None:
                rows = self._conn.execute(
                    "SELECT * FROM events WHERE seq > ? ORDER BY seq ASC LIMIT ?",
                    (last_anchored, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM events WHERE seq > ? ORDER BY seq ASC",
                    (last_anchored,),
                ).fetchall()
            return [self._row_to_event(r) for r in rows]

    def record_anchor_batch(
        self,
        batch_id: str,
        first_seq: int,
        last_seq: int,
        root: str,
        head_hash: str,
        tx_hash: str | None = None,
        status: str = "anchored",
        anchored_at: str | None = None,
    ) -> dict[str, Any]:
        ts = anchored_at if anchored_at is not None else (_utc_now_iso() if status == "anchored" else "")
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    INSERT INTO anchor_batches (
                        batch_id, first_seq, last_seq, root, head_hash, tx_hash, status, anchored_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(batch_id) DO UPDATE SET
                        first_seq = excluded.first_seq,
                        last_seq = excluded.last_seq,
                        root = excluded.root,
                        head_hash = excluded.head_hash,
                        tx_hash = excluded.tx_hash,
                        status = excluded.status,
                        anchored_at = excluded.anchored_at
                    """,
                    (batch_id, first_seq, last_seq, root, head_hash, tx_hash or "", status, ts),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return {
            "batch_id": batch_id,
            "first_seq": first_seq,
            "last_seq": last_seq,
            "root": root,
            "head_hash": head_hash,
            "tx_hash": tx_hash or "",
            "status": status,
            "anchored_at": ts,
        }

    def get_anchor_batches(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if status is not None:
                rows = self._conn.execute(
                    "SELECT * FROM anchor_batches WHERE status = ? ORDER BY first_seq ASC",
                    (status,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM anchor_batches ORDER BY first_seq ASC"
                ).fetchall()
            return [dict(r) for r in rows]

    def get_batch_for_seq(self, seq: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM anchor_batches
                WHERE first_seq <= ? AND last_seq >= ?
                ORDER BY CASE WHEN status = 'anchored' THEN 0 ELSE 1 END, last_seq DESC
                LIMIT 1
                """,
                (seq, seq),
            ).fetchone()
            return dict(row) if row is not None else None

    def get_event_inclusion_proof(self, seq: int) -> tuple[dict[str, Any], list[dict[str, str]]] | None:
        """Return `(batch_dict, merkle_inclusion_proof)` for event `seq` if it belongs to a batch."""
        batch = self.get_batch_for_seq(seq)
        if batch is None:
            return None
        events = self.get_events_range(int(batch["first_seq"]), int(batch["last_seq"]))
        if not events:
            return None
        hashes = [e.hash for e in events]
        _, proofs = build_event_batch_tree(hashes)
        for idx, ev in enumerate(events):
            if ev.seq == seq:
                return batch, proofs[idx]
        return None

    def record_lender_head(
        self,
        lender_id: str,
        last_seq: int | None = None,
        last_hash: str | None = None,
    ) -> dict[str, Any]:
        """Record or update a lender's observed chain head `(last_seq, last_hash)`."""
        with self._lock:
            if last_seq is None or last_hash is None:
                cur_seq, cur_hash = self.head()
                last_seq = cur_seq if last_seq is None else last_seq
                last_hash = cur_hash if last_hash is None else last_hash
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    INSERT INTO lender_heads (lender_id, last_seq, last_hash)
                    VALUES (?, ?, ?)
                    ON CONFLICT(lender_id) DO UPDATE SET
                        last_seq = excluded.last_seq,
                        last_hash = excluded.last_hash
                    """,
                    (lender_id, int(last_seq), str(last_hash)),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return {"lender_id": lender_id, "last_seq": int(last_seq), "last_hash": str(last_hash)}

    def get_lender_heads(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT lender_id, last_seq, last_hash FROM lender_heads ORDER BY lender_id ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_DEFAULT_CHAIN: EventChain | None = None
_DEFAULT_LOCK = threading.Lock()


def get_default_chain() -> EventChain:
    global _DEFAULT_CHAIN
    with _DEFAULT_LOCK:
        if _DEFAULT_CHAIN is None:
            _DEFAULT_CHAIN = EventChain(":memory:")
        return _DEFAULT_CHAIN


def append_event(
    chain_or_event_type: EventChain | str | dict[str, Any] | AuditEvent,
    event_type_or_entity_id: str = "",
    entity_id_or_actor_id: str = "system",
    actor_id_or_payload: str | dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    *,
    chain: EventChain | None = None,
    event_id: str | None = None,
    timestamp: str | None = None,
    schema_version: int = SCHEMA_VERSION,
) -> AuditEvent:
    """Module-level convenience wrapper to append an event to an `EventChain`."""
    if isinstance(chain_or_event_type, EventChain):
        target_chain = chain_or_event_type
        ev_type = event_type_or_entity_id
        ent_id = entity_id_or_actor_id
        if isinstance(actor_id_or_payload, dict):
            act_id = "system"
            pay = actor_id_or_payload
        else:
            act_id = str(actor_id_or_payload) if actor_id_or_payload is not None else "system"
            pay = payload
        return target_chain.append(
            ev_type,
            ent_id,
            act_id,
            pay,
            event_id=event_id,
            timestamp=timestamp,
            schema_version=schema_version,
        )

    target_chain = chain or get_default_chain()
    if isinstance(chain_or_event_type, (dict, AuditEvent)):
        return target_chain.append(
            chain_or_event_type,
            event_id=event_id,
            timestamp=timestamp,
            schema_version=schema_version,
        )

    ev_type = str(chain_or_event_type)
    ent_id = event_type_or_entity_id
    act_id = entity_id_or_actor_id
    pay = actor_id_or_payload if isinstance(actor_id_or_payload, dict) else payload
    return target_chain.append(
        ev_type,
        ent_id,
        act_id,
        pay,
        event_id=event_id,
        timestamp=timestamp,
        schema_version=schema_version,
    )

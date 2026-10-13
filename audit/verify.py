"""Two-answer audit verification: local cryptographic integrity + independent on-chain anchoring status."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from audit.chain import GENESIS_PREV_HASH, EventChain, compute_event_hash
from audit.merkle import build_event_batch_tree, verify_inclusion_proof
from audit.store import EncryptedBlobStore, TamperDetectedError
from audit.versions import VERSION_RECORD_KEYS, hash_version_record
from chain.rpc import AnchorRPCClient, RPCDownError
from contract.models import SCHEMA_VERSION


class AttrDict(dict):  # type: ignore[type-arg]
    """Dictionary supporting both `d['key']` and `d.key` attribute access."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


class VerificationReport(AttrDict):
    """Two-answer verification report containing independent `integrity` and `anchoring` results."""


def _verify_integrity(
    chain: EventChain,
    blob_store: EncryptedBlobStore | None = None,
    doc_ids: list[str] | None = None,
) -> tuple[AttrDict, dict[int, str], int]:
    """Run full off-chain cryptographic integrity checks:
    1. Encrypted blob AES-256-GCM tag + SHA-256 verification.
    2. Version record hash verification.
    3. Event payload and canonical event hash verification.
    4. Full hash-chain walk (`seq` continuity and `prev_hash` links).
    5. Truncation detection against `anchor_batches` and `lender_heads`.
    """
    verified_blobs: dict[str, str] = {}

    if blob_store is not None:
        candidates: list[str] = []
        if doc_ids:
            for d in doc_ids:
                if d not in candidates:
                    candidates.append(d)
        for d in blob_store.list_doc_ids():
            if d not in candidates:
                candidates.append(d)

        for doc_id in candidates:
            try:
                plaintext = blob_store.get_blob(doc_id)
                verified_blobs[doc_id] = hashlib.sha256(plaintext).hexdigest()
            except TamperDetectedError as exc:
                return (
                    AttrDict(
                        status="failed",
                        failed_location=exc.failed_location,
                        details=str(exc),
                    ),
                    {},
                    0,
                )

    with chain._lock:
        rows = chain.conn.execute(
            """
            SELECT seq, event_id, event_type, entity_id, actor_id,
                   timestamp, payload_json, prev_hash, hash
            FROM events
            ORDER BY seq ASC
            """
        ).fetchall()

    expected_seq = 1
    expected_prev_hash = GENESIS_PREV_HASH
    events_by_seq: dict[int, str] = {}

    for row in rows:
        seq = int(row["seq"])
        if seq != expected_seq:
            return (
                AttrDict(
                    status="failed",
                    failed_location=f"seq:{expected_seq}",
                    details=(
                        f"Missing event at seq {expected_seq} (found seq {seq} next; "
                        f"mid-chain deletion detected at seq {expected_seq})."
                    ),
                ),
                events_by_seq,
                seq,
            )

        stored_prev_hash = str(row["prev_hash"])
        if stored_prev_hash != expected_prev_hash:
            return (
                AttrDict(
                    status="failed",
                    failed_location=f"seq:{seq}",
                    details=(
                        f"Broken hash chain link at seq {seq}: prev_hash {stored_prev_hash} "
                        f"does not match prior event hash {expected_prev_hash}."
                    ),
                ),
                events_by_seq,
                seq,
            )

        try:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            if not isinstance(payload, dict):
                raise ValueError("Event payload must be a JSON object")
        except Exception as exc:
            return (
                AttrDict(
                    status="failed",
                    failed_location=f"seq:{seq}",
                    details=f"Corrupted payload_json at seq {seq}: {exc}",
                ),
                events_by_seq,
                seq,
            )

        # Check version record commitment and linked blob SHA-256 if applicable
        has_version_rec = (
            isinstance(payload.get("version_record"), dict)
            or all(k in payload for k in VERSION_RECORD_KEYS)
        )
        if has_version_rec:
            try:
                computed_vr_hash = hash_version_record(payload)
            except Exception as exc:
                return (
                    AttrDict(
                        status="failed",
                        failed_location=f"seq:{seq}",
                        details=f"Invalid version record at seq {seq}: {exc}",
                    ),
                    events_by_seq,
                    seq,
                )
            if "version_record_hash" in payload and payload["version_record_hash"] != computed_vr_hash:
                return (
                    AttrDict(
                        status="failed",
                        failed_location=f"seq:{seq}",
                        details=(
                            f"Version record hash mismatch at seq {seq}: "
                            f"expected {payload['version_record_hash']}, computed {computed_vr_hash}."
                        ),
                    ),
                    events_by_seq,
                    seq,
                )

            expected_file_sha = payload.get("file_sha256") or (
                payload.get("version_record", {}).get("file_sha256")
                if isinstance(payload.get("version_record"), dict)
                else None
            )
            doc_id_ref = payload.get("doc_id")
            if blob_store is not None and doc_id_ref:
                doc_id_str = str(doc_id_ref)
                if doc_id_str not in verified_blobs:
                    try:
                        pt = blob_store.get_blob(doc_id_str)
                        verified_blobs[doc_id_str] = hashlib.sha256(pt).hexdigest()
                    except TamperDetectedError as exc:
                        return (
                            AttrDict(
                                status="failed",
                                failed_location=exc.failed_location,
                                details=str(exc),
                            ),
                            events_by_seq,
                            seq,
                        )
                if expected_file_sha and verified_blobs[doc_id_str] != expected_file_sha:
                    return (
                        AttrDict(
                            status="failed",
                            failed_location=f"{doc_id_str}.bin",
                            details=(
                                f"Blob SHA-256 mismatch for {doc_id_str}.bin referenced at seq {seq}: "
                                f"version record expects {expected_file_sha}, got {verified_blobs[doc_id_str]}."
                            ),
                        ),
                        events_by_seq,
                        seq,
                    )

        recomputed_hash = compute_event_hash(
            seq=seq,
            event_id=str(row["event_id"]),
            event_type=str(row["event_type"]),
            entity_id=str(row["entity_id"]),
            actor_id=str(row["actor_id"]),
            timestamp=str(row["timestamp"]),
            payload=payload,
            prev_hash=stored_prev_hash,
            schema_version=SCHEMA_VERSION,
        )
        stored_hash = str(row["hash"])
        if recomputed_hash != stored_hash:
            return (
                AttrDict(
                    status="failed",
                    failed_location=f"seq:{seq}",
                    details=(
                        f"Event hash mismatch at seq {seq}: stored hash {stored_hash} "
                        f"!= recomputed hash {recomputed_hash}."
                    ),
                ),
                events_by_seq,
                seq,
            )

        events_by_seq[seq] = recomputed_hash
        expected_prev_hash = recomputed_hash
        expected_seq = seq + 1

    max_seq = expected_seq - 1

    # Truncation and head-hash checks against anchor_batches
    for batch in chain.get_anchor_batches():
        b_id = str(batch["batch_id"])
        b_first = int(batch["first_seq"])
        b_last = int(batch["last_seq"])
        b_head = str(batch["head_hash"])
        b_root = str(batch["root"])

        if max_seq < b_last:
            missing_start = max_seq + 1
            return (
                AttrDict(
                    status="failed",
                    failed_location=f"truncation:seq:{missing_start}..{b_last}",
                    details=(
                        f"Chain truncation detected: events table ends at seq {max_seq}, "
                        f"but anchor batch {b_id} recorded last_seq={b_last} (head_hash={b_head})."
                    ),
                ),
                events_by_seq,
                max_seq,
            )

        if events_by_seq.get(b_last) != b_head:
            return (
                AttrDict(
                    status="failed",
                    failed_location=f"seq:{b_last}",
                    details=(
                        f"Anchor batch {b_id} head_hash mismatch at seq {b_last}: "
                        f"chain has {events_by_seq.get(b_last)}, batch recorded {b_head}."
                    ),
                ),
                events_by_seq,
                max_seq,
            )

        batch_hashes = [events_by_seq[s] for s in range(b_first, b_last + 1) if s in events_by_seq]
        recomputed_root, _ = build_event_batch_tree(batch_hashes)
        if recomputed_root != b_root:
            return (
                AttrDict(
                    status="failed",
                    failed_location=f"batch:{b_id}",
                    details=(
                        f"Anchor batch {b_id} Merkle root mismatch: "
                        f"recomputed {recomputed_root} != recorded {b_root}."
                    ),
                ),
                events_by_seq,
                max_seq,
            )

    # Truncation and head-hash checks against lender_heads
    for lh in chain.get_lender_heads():
        lender_id = str(lh["lender_id"])
        l_seq = int(lh["last_seq"])
        l_hash = str(lh["last_hash"])
        if max_seq < l_seq:
            missing_start = max_seq + 1
            return (
                AttrDict(
                    status="failed",
                    failed_location=f"truncation:lender:{lender_id}:seq:{missing_start}..{l_seq}",
                    details=(
                        f"Chain truncation detected against lender head {lender_id}: "
                        f"events table ends at seq {max_seq}, expected last_seq={l_seq} (last_hash={l_hash})."
                    ),
                ),
                events_by_seq,
                max_seq,
            )
        if l_seq > 0 and events_by_seq.get(l_seq) != l_hash:
            return (
                AttrDict(
                    status="failed",
                    failed_location=f"seq:{l_seq}",
                    details=(
                        f"Lender head {lender_id} hash mismatch at seq {l_seq}: "
                        f"chain has {events_by_seq.get(l_seq)}, lender recorded {l_hash}."
                    ),
                ),
                events_by_seq,
                max_seq,
            )

    return (
        AttrDict(
            status="verified",
            failed_location=None,
            details=f"Verified {max_seq} event(s) and {len(verified_blobs)} encrypted blob(s).",
        ),
        events_by_seq,
        max_seq,
    )


def _verify_anchoring(
    chain: EventChain,
    rpc_client: AnchorRPCClient,
    events_by_seq: dict[int, str],
    max_seq: int,
    event_seq: int | None = None,
) -> AttrDict:
    """Verify on-chain Merkle anchoring independently of off-chain integrity."""
    target_seq = int(event_seq) if event_seq is not None else max_seq
    batch = chain.get_batch_for_seq(target_seq) if target_seq > 0 else None

    try:
        rpc_client.health_check()
    except RPCDownError as exc:
        return AttrDict(
            status="unavailable",
            batch_id=str(batch["batch_id"]) if batch else None,
            root=str(batch["root"]) if batch else None,
            tx_hash=(str(batch["tx_hash"]) if batch and batch.get("tx_hash") else None),
            details=f"On-chain RPC endpoint unavailable: {exc}",
        )

    if target_seq <= 0:
        return AttrDict(
            status="pending",
            batch_id=None,
            root=None,
            tx_hash=None,
            details="No events present in chain yet.",
        )

    if batch is None or batch.get("status") == "pending":
        last_anchored = chain.get_last_anchored_seq()
        return AttrDict(
            status="pending",
            batch_id=str(batch["batch_id"]) if batch else None,
            root=str(batch["root"]) if batch else None,
            tx_hash=None,
            details=f"Event seq {target_seq} is awaiting anchoring (last anchored seq: {last_anchored}).",
        )

    if batch.get("status") == "unavailable":
        return AttrDict(
            status="unavailable",
            batch_id=str(batch["batch_id"]),
            root=str(batch["root"]),
            tx_hash=None,
            details=f"Anchor batch {batch['batch_id']} could not be submitted because RPC was unavailable.",
        )

    batch_id = str(batch["batch_id"])
    batch_root = str(batch["root"])
    tx_hash = str(batch["tx_hash"]) if batch.get("tx_hash") else None

    # Check Merkle inclusion proof of target_seq in batch
    proof_info = chain.get_event_inclusion_proof(target_seq)
    if proof_info is None:
        return AttrDict(
            status="unavailable",
            batch_id=batch_id,
            root=batch_root,
            tx_hash=tx_hash,
            details=f"Could not construct Merkle inclusion proof for event seq {target_seq} in {batch_id}.",
        )

    _, inclusion_proof = proof_info
    ev_hash = events_by_seq.get(target_seq)
    if not ev_hash:
        ev_obj = chain.get_event(target_seq)
        ev_hash = ev_obj.hash if ev_obj is not None else ""

    if not verify_inclusion_proof(ev_hash, batch_root, inclusion_proof):
        return AttrDict(
            status="unavailable",
            batch_id=batch_id,
            root=batch_root,
            tx_hash=tx_hash,
            details=f"Merkle inclusion proof failed for event seq {target_seq} against batch root {batch_root}.",
        )

    try:
        on_chain_ok = rpc_client.verify_anchor(
            root=batch_root,
            first_seq=int(batch["first_seq"]),
            last_seq=int(batch["last_seq"]),
            head_hash=str(batch["head_hash"]),
            tx_hash=tx_hash,
        )
    except RPCDownError as exc:
        return AttrDict(
            status="unavailable",
            batch_id=batch_id,
            root=batch_root,
            tx_hash=tx_hash,
            details=f"RPC became unreachable during anchor verification: {exc}",
        )

    if not on_chain_ok:
        return AttrDict(
            status="unavailable",
            batch_id=batch_id,
            root=batch_root,
            tx_hash=tx_hash,
            details=f"Batch root {batch_root} not confirmed on Anchor contract.",
        )

    return AttrDict(
        status="anchored",
        batch_id=batch_id,
        root=batch_root,
        tx_hash=tx_hash,
        details=f"Event seq {target_seq} verified in batch {batch_id} anchored on-chain ({tx_hash}).",
    )


def verify(
    chain: EventChain,
    blob_store: EncryptedBlobStore | None = None,
    rpc_client: AnchorRPCClient | None = None,
    *,
    event_seq: int | None = None,
    doc_ids: list[str] | None = None,
) -> VerificationReport:
    """Verify off-chain cryptographic integrity and on-chain Merkle anchoring independently.

    Returns a `VerificationReport` with two independent keys/attributes:
    - `integrity`: `{"status": "verified" | "failed", "failed_location": str | None, "details": str}`
    - `anchoring`: `{"status": "anchored" | "pending" | "unavailable", "batch_id": str | None, "root": str | None, "tx_hash": str | None, "details": str}`
    """
    client = rpc_client or AnchorRPCClient()
    integrity_res, events_by_seq, max_seq = _verify_integrity(
        chain=chain,
        blob_store=blob_store,
        doc_ids=doc_ids,
    )
    anchoring_res = _verify_anchoring(
        chain=chain,
        rpc_client=client,
        events_by_seq=events_by_seq,
        max_seq=max_seq,
        event_seq=event_seq,
    )
    return VerificationReport(
        integrity=integrity_res,
        anchoring=anchoring_res,
    )


verify_audit = verify

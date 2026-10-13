"""Non-blocking Merkle batch anchorer and background daemon thread."""

from __future__ import annotations

import threading
import time
from typing import Any

from audit.chain import EventChain
from audit.merkle import build_event_batch_tree
from chain.rpc import AnchorRPCClient, RPCDownError


def anchor_pending_batch(
    chain: EventChain,
    rpc_client: AnchorRPCClient | None = None,
    batch_size: int = 20,
    *,
    min_events: int = 1,
    max_retries: int = 2,
    backoff_sec: float = 0.02,
) -> dict[str, Any] | None:
    """Build an RFC 6962 Merkle batch over up to `batch_size` unanchored events and submit to `Anchor` contract.

    Never raises `RPCDownError` into the caller; if the RPC endpoint is unreachable after retries,
    records the batch with `status='unavailable'` so the request path is never blocked.
    """
    client = rpc_client or AnchorRPCClient()
    events = chain.get_unanchored_events(limit=batch_size)
    if len(events) < min_events or not events:
        return None

    first_seq = events[0].seq
    last_seq = events[-1].seq
    head_hash = events[-1].hash
    event_hashes = [ev.hash for ev in events]
    batch_root, _ = build_event_batch_tree(event_hashes)
    batch_id = f"BATCH-{first_seq:06d}-{last_seq:06d}"

    # First record as pending
    chain.record_anchor_batch(
        batch_id=batch_id,
        first_seq=first_seq,
        last_seq=last_seq,
        root=batch_root,
        head_hash=head_hash,
        tx_hash="",
        status="pending",
        anchored_at="",
    )

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            receipt = client.anchor(
                root=batch_root,
                first_seq=first_seq,
                last_seq=last_seq,
                head_hash=head_hash,
            )
            return chain.record_anchor_batch(
                batch_id=batch_id,
                first_seq=first_seq,
                last_seq=last_seq,
                root=batch_root,
                head_hash=head_hash,
                tx_hash=str(receipt["tx_hash"]),
                status="anchored",
                anchored_at=str(receipt["anchored_at"]),
            )
        except RPCDownError as exc:
            last_err = exc
            if attempt + 1 < max_retries and backoff_sec > 0:
                time.sleep(backoff_sec * (2**attempt))
        except Exception as exc:
            last_err = exc
            break

    batch_rec = chain.record_anchor_batch(
        batch_id=batch_id,
        first_seq=first_seq,
        last_seq=last_seq,
        root=batch_root,
        head_hash=head_hash,
        tx_hash="",
        status="unavailable",
        anchored_at="",
    )
    if last_err is not None:
        batch_rec["error"] = str(last_err)
    return batch_rec


class BackgroundAnchorer:
    """Daemon thread that anchors event batches every `batch_size=20` events (or on demand) without blocking."""

    def __init__(
        self,
        chain: EventChain,
        rpc_client: AnchorRPCClient | None = None,
        *,
        batch_size: int = 20,
        poll_interval: float = 0.1,
        max_retries: int = 2,
        backoff_sec: float = 0.02,
    ) -> None:
        self.chain = chain
        self.rpc_client = rpc_client or AnchorRPCClient()
        self.batch_size = batch_size
        self.poll_interval = poll_interval
        self.max_retries = max_retries
        self.backoff_sec = backoff_sec

        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._force_flush = False
        self._thread: threading.Thread | None = None
        self.batches_processed: list[dict[str, Any]] = []

    def start(self) -> BackgroundAnchorer:
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="TesseraBackgroundAnchorer",
            daemon=True,
        )
        self._thread.start()
        return self

    def trigger(self, *, flush_partial: bool = True) -> None:
        """Non-blocking signal to wake the background daemon immediately."""
        if flush_partial:
            self._force_flush = True
        self._wake_event.set()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self._wake_event.wait(timeout=self.poll_interval)
            self._wake_event.clear()
            if self._stop_event.is_set():
                break
            min_ev = 1 if self._force_flush else self.batch_size
            self._force_flush = False
            while not self._stop_event.is_set():
                res = anchor_pending_batch(
                    self.chain,
                    self.rpc_client,
                    batch_size=self.batch_size,
                    min_events=min_ev,
                    max_retries=self.max_retries,
                    backoff_sec=self.backoff_sec,
                )
                if res is None:
                    break
                self.batches_processed.append(res)
                if res.get("status") == "unavailable":
                    break

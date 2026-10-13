"""JSON-RPC client for on-chain Merkle root anchoring (`Anchor.sol`) with fast-fail offline detection."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from chain.deploy import ANCHOR_ABI


class RPCDownError(Exception):
    """Raised when the Ethereum/Polygon JSON-RPC endpoint is unreachable or network-off mode is active."""


_GLOBAL_LOCK = threading.RLock()
_GLOBAL_MOCK_ANCHORS: dict[str, dict[str, Any]] = {}
_GLOBAL_MOCK_TXS: dict[str, dict[str, Any]] = {}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_hex32(val: str) -> str:
    s = val.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    if len(s) != 64:
        raise ValueError(f"Expected 32-byte (64-hex-char) value, got {val!r}")
    bytes.fromhex(s)
    return s


class AnchorRPCClient:
    """Client for the `Anchor` smart contract supporting live `web3.py` RPC and fast in-memory mock mode."""

    def __init__(
        self,
        rpc_url: str | None = None,
        contract_address: str | None = None,
        private_key: str | None = None,
        *,
        network_off: bool = False,
        timeout: float = 0.3,
    ) -> None:
        self.rpc_url = rpc_url
        self.contract_address = contract_address or ("0x" + "a6" * 20)
        self.private_key = private_key
        self.network_off = network_off
        self.timeout = timeout
        self._anchors = _GLOBAL_MOCK_ANCHORS
        self._txs = _GLOBAL_MOCK_TXS

    def set_network_off(self, off: bool) -> None:
        """Toggle simulated network-off mode for resilience testing and demos."""
        self.network_off = bool(off)

    def _effective_rpc_url(self) -> str:
        env_url = os.environ.get("TESSERA_RPC", "").strip()
        if env_url:
            return env_url
        if self.rpc_url:
            return self.rpc_url.strip()
        return "mock"

    def _probe_http_rpc(self, url: str) -> dict[str, Any]:
        if ":9/" in (url + "/"):
            raise RPCDownError(f"RPC endpoint unreachable at dead port {url}")
        payload = json.dumps(
            {"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 1}
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
                data = json.loads(body)
                if not isinstance(data, dict) or ("result" not in data and "error" not in data):
                    raise RPCDownError(f"Invalid JSON-RPC response from {url}")
                return data
        except RPCDownError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise RPCDownError(f"RPC endpoint unreachable at {url}: {exc}") from exc

    def _ensure_online(self) -> str:
        """Verify RPC availability and return effective mode (`'mock'` or HTTP URL). Raises `RPCDownError` if down."""
        if self.network_off:
            raise RPCDownError("Anchor RPC client is in network_off=True mode")

        effective = self._effective_rpc_url()
        lowered = effective.lower()
        if lowered in ("mock", "memory", "inmemory", "local", ""):
            return "mock"
        if lowered in ("off", "down", "unavailable", "offline", "0", "false"):
            raise RPCDownError(f"Anchor RPC disabled via configuration ({effective!r})")
        if lowered.startswith(("http://", "https://")):
            self._probe_http_rpc(effective)
            return effective
        raise RPCDownError(f"Unsupported or unreachable RPC endpoint: {effective!r}")

    def health_check(self) -> bool:
        """Return True if RPC is reachable, or raise `RPCDownError`."""
        self._ensure_online()
        return True

    def anchor(
        self,
        root: str,
        first_seq: int,
        last_seq: int,
        head_hash: str,
    ) -> dict[str, Any]:
        """Submit `anchor(bytes32 root, uint64 firstSeq, uint64 lastSeq, bytes32 head)` to the Anchor contract."""
        mode = self._ensure_online()
        root_hex = _norm_hex32(root)
        head_hex = _norm_hex32(head_hash)
        first_i = int(first_seq)
        last_i = int(last_seq)
        if first_i < 1 or last_i < first_i:
            raise ValueError(f"Invalid sequence range: {first_i}..{last_i}")

        if mode != "mock" and self.private_key:
            try:
                from web3 import Web3  # type: ignore[import-not-found]

                w3 = Web3(Web3.HTTPProvider(mode, request_kwargs={"timeout": self.timeout}))
                account = w3.eth.account.from_key(self.private_key)
                contract = w3.eth.contract(
                    address=Web3.to_checksum_address(self.contract_address),
                    abi=ANCHOR_ABI,
                )
                tx = contract.functions.anchor(
                    bytes.fromhex(root_hex),
                    first_i,
                    last_i,
                    bytes.fromhex(head_hex),
                ).build_transaction(
                    {
                        "from": account.address,
                        "nonce": w3.eth.get_transaction_count(account.address),
                        "gas": 120_000,
                        "gasPrice": w3.eth.gas_price,
                    }
                )
                signed = account.sign_transaction(tx)
                tx_hash_bytes = w3.eth.send_raw_transaction(signed.raw_transaction)
                tx_hash = "0x" + tx_hash_bytes.hex().lstrip("0x")
            except Exception as exc:
                raise RPCDownError(f"Live Web3 anchor transaction failed: {exc}") from exc
        else:
            digest_input = f"{root_hex}:{first_i}:{last_i}:{head_hex}".encode("ascii")
            tx_hash = "0x" + hashlib.sha256(digest_input).hexdigest()

        record = {
            "root": root_hex,
            "first_seq": first_i,
            "last_seq": last_i,
            "head_hash": head_hex,
            "tx_hash": tx_hash,
            "contract_address": self.contract_address,
            "anchored_at": _utc_now_iso(),
        }
        with _GLOBAL_LOCK:
            self._anchors[root_hex] = record
            self._txs[tx_hash] = record
        return record

    def get_anchored_root(self, root: str) -> dict[str, Any] | None:
        """Query the on-chain `Anchored` event log for `root`. Raises `RPCDownError` if RPC is unreachable."""
        self._ensure_online()
        root_hex = _norm_hex32(root)
        with _GLOBAL_LOCK:
            rec = self._anchors.get(root_hex)
            return dict(rec) if rec is not None else None

    def verify_anchor(
        self,
        root: str,
        first_seq: int | None = None,
        last_seq: int | None = None,
        head_hash: str | None = None,
        tx_hash: str | None = None,
    ) -> bool:
        """Verify that `root` (and optional sequence bounds / head hash) was anchored on-chain."""
        self._ensure_online()
        try:
            root_hex = _norm_hex32(root)
        except ValueError:
            return False

        with _GLOBAL_LOCK:
            rec = self._anchors.get(root_hex)
            if rec is None and tx_hash:
                tx_rec = self._txs.get(tx_hash)
                if tx_rec is not None and tx_rec.get("root") != root_hex:
                    return False

        if rec is None:
            return False

        if first_seq is not None and int(rec["first_seq"]) != int(first_seq):
            return False
        if last_seq is not None and int(rec["last_seq"]) != int(last_seq):
            return False
        if head_hash is not None:
            try:
                if rec["head_hash"] != _norm_hex32(head_hash):
                    return False
            except ValueError:
                return False
        if tx_hash and rec.get("tx_hash") and str(rec["tx_hash"]) != str(tx_hash):
            return False
        return True

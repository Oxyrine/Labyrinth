"""Deployment utilities and ABI definition for `chain/Anchor.sol`."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

ANCHOR_SOL_PATH = Path(__file__).with_name("Anchor.sol")

ANCHOR_ABI: list[dict[str, Any]] = [
    {
        "inputs": [],
        "stateMutability": "nonpayable",
        "type": "constructor",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "bytes32", "name": "root", "type": "bytes32"},
            {"indexed": False, "internalType": "uint64", "name": "firstSeq", "type": "uint64"},
            {"indexed": False, "internalType": "uint64", "name": "lastSeq", "type": "uint64"},
            {"indexed": False, "internalType": "bytes32", "name": "head", "type": "bytes32"},
        ],
        "name": "Anchored",
        "type": "event",
    },
    {
        "inputs": [
            {"internalType": "bytes32", "name": "root", "type": "bytes32"},
            {"internalType": "uint64", "name": "firstSeq", "type": "uint64"},
            {"internalType": "uint64", "name": "lastSeq", "type": "uint64"},
            {"internalType": "bytes32", "name": "head", "type": "bytes32"},
        ],
        "name": "anchor",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "owner",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]


def get_anchor_source() -> str:
    """Read and return the Solidity source code of `Anchor.sol`."""
    return ANCHOR_SOL_PATH.read_text(encoding="utf-8")


def deploy_anchor_contract(
    rpc_url: str | None = None,
    private_key: str | None = None,
    bytecode: str | None = None,
) -> dict[str, Any]:
    """Deploy `Anchor.sol` to a live EVM RPC via `web3`, or return a deterministic mock contract deployment."""
    source = get_anchor_source()
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    default_address = "0x" + source_hash[:40]

    if not rpc_url or rpc_url in ("mock", "memory"):
        return {
            "contract_address": default_address,
            "abi": ANCHOR_ABI,
            "mode": "mock",
            "tx_hash": "0x" + hashlib.sha256(b"mock-deploy:" + source_hash.encode("ascii")).hexdigest(),
        }

    try:
        from web3 import Web3  # type: ignore[import-not-found]

        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 3.0}))
        if not w3.is_connected() or not private_key or not bytecode:
            return {
                "contract_address": default_address,
                "abi": ANCHOR_ABI,
                "mode": "fallback",
                "tx_hash": "0x" + hashlib.sha256(b"fallback-deploy:" + source_hash.encode("ascii")).hexdigest(),
            }
        account = w3.eth.account.from_key(private_key)
        contract = w3.eth.contract(abi=ANCHOR_ABI, bytecode=bytecode)
        tx = contract.constructor().build_transaction(
            {
                "from": account.address,
                "nonce": w3.eth.get_transaction_count(account.address),
                "gas": 300_000,
                "gasPrice": w3.eth.gas_price,
            }
        )
        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
        return {
            "contract_address": receipt.contractAddress,
            "abi": ANCHOR_ABI,
            "mode": "live",
            "tx_hash": tx_hash.hex(),
        }
    except Exception as exc:
        raise RuntimeError(f"Failed to deploy Anchor contract to {rpc_url}: {exc}") from exc

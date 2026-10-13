"""On-chain Merkle batch anchoring package (`Anchor.sol`, RPC client, and background anchorer)."""

from __future__ import annotations

from chain.anchorer import BackgroundAnchorer, anchor_pending_batch
from chain.deploy import ANCHOR_ABI, ANCHOR_SOL_PATH, deploy_anchor_contract, get_anchor_source
from chain.rpc import AnchorRPCClient, RPCDownError

__all__ = [
    "ANCHOR_ABI",
    "ANCHOR_SOL_PATH",
    "AnchorRPCClient",
    "BackgroundAnchorer",
    "RPCDownError",
    "anchor_pending_batch",
    "deploy_anchor_contract",
    "get_anchor_source",
]

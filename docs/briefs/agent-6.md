# Agent A6 Brief — Encrypted Off-Chain Audit Store & On-Chain Merkle Anchoring (`audit/`, `chain/`)

## Ownership & Boundaries
- **Owned folders**: `audit/`, `chain/`, `tests/test_audit.py`
- **Forbidden folders**: All other module folders

## Deliverables
- `audit/store.py`: AES-256-GCM encrypted blob storage, per-seller data key wrapped by master key, 96-bit random nonce, `AAD = file_sha256 | seller_id`.
- `audit/versions.py`: Deterministic version records hashed into `invoice.version_created` events.
- `audit/merkle.py`: RFC-6962 Merkle tree with domain separation (`0x00` leaf, `0x01` internal node), salted field leaves `H(0x00 || salt || name || value)` with 16-byte random salt per field, inclusion proofs.
- `audit/chain.py`: Append-only SQLite `events` hash chain (`hash = SHA256(canonical(event without hash))`, `prev_hash`), serialized under write lock.
- `audit/disclose.py`: Selective disclosure proof for individual invoice fields without revealing other fields.
- `audit/verify.py`: Independent two-answer verification:
  1. `integrity`: `verified | failed(location)`
  2. `anchoring`: `anchored | pending | unavailable`
- `chain/Anchor.sol`, `chain/deploy.py`, `chain/rpc.py`, `chain/anchorer.py` (background batch thread, never blocks request path).

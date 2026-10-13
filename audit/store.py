"""Encrypted off-chain document blob store using AES-256-GCM and envelope key wrapping."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DEFAULT_DEMO_MASTER_SEED = b"labyrinth-clean-room-demo-master-key-256bit-v1"
NONCE_BYTES = 12  # 96-bit nonce for AES-GCM
KEY_BYTES = 32  # 256-bit key for AES-256-GCM


class TamperDetectedError(Exception):
    """Raised when an encrypted blob or its metadata fails integrity/authenticity verification."""

    def __init__(self, doc_id: str, reason: str, failed_location: str | None = None) -> None:
        self.doc_id = doc_id
        self.reason = reason
        self.failed_location = failed_location or f"{doc_id}.bin"
        super().__init__(f"Tamper detected in {self.failed_location}: {reason}")


def load_or_derive_master_key(master_key_path: str | Path | None = None, master_key: bytes | None = None) -> bytes:
    """Load a 256-bit master key from disk (`keys/master.key`), or fall back to deterministic demo master key."""
    if master_key is not None:
        if len(master_key) == KEY_BYTES:
            return master_key
        return hashlib.sha256(master_key).digest()

    path = Path(master_key_path) if master_key_path is not None else Path("keys/master.key")
    if path.is_file():
        raw = path.read_bytes()
        if len(raw) == KEY_BYTES:
            return raw
        stripped = raw.strip()
        if len(stripped) == KEY_BYTES * 2:
            try:
                return bytes.fromhex(stripped.decode("ascii"))
            except ValueError:
                pass
        return hashlib.sha256(raw).digest()

    return hashlib.sha256(DEFAULT_DEMO_MASTER_SEED).digest()


class EncryptedBlobStore:
    """AES-256-GCM encrypted blob store with per-seller wrapped 256-bit data keys."""

    def __init__(
        self,
        blob_dir: str | Path = "data/blobs",
        master_key_path: str | Path | None = None,
        master_key: bytes | None = None,
    ) -> None:
        self.blob_dir = Path(blob_dir)
        self.blob_dir.mkdir(parents=True, exist_ok=True)
        self._master_key = load_or_derive_master_key(master_key_path=master_key_path, master_key=master_key)
        self._master_aead = AESGCM(self._master_key)
        self._seller_keys: dict[str, tuple[bytes, bytes, bytes]] = {}
        # Maps seller_id -> (raw_seller_key_32b, wrapped_key_bytes, wrapped_nonce_12b)

    def blob_path(self, doc_id: str) -> Path:
        return self.blob_dir / f"{doc_id}.bin"

    def meta_path(self, doc_id: str) -> Path:
        return self.blob_dir / f"{doc_id}.meta.json"

    def has_blob(self, doc_id: str) -> bool:
        return self.blob_path(doc_id).is_file() and self.meta_path(doc_id).is_file()

    def list_doc_ids(self) -> list[str]:
        doc_ids: set[str] = set()
        if not self.blob_dir.exists():
            return []
        for p in self.blob_dir.glob("*.meta.json"):
            name = p.name
            if name.endswith(".meta.json"):
                doc_ids.add(name[: -len(".meta.json")])
        for p in self.blob_dir.glob("*.bin"):
            doc_ids.add(p.stem)
        return sorted(doc_ids)

    def _get_or_create_seller_key(self, seller_id: str) -> tuple[bytes, bytes, bytes]:
        if seller_id in self._seller_keys:
            return self._seller_keys[seller_id]
        seller_key = os.urandom(KEY_BYTES)
        wrapped_nonce = os.urandom(NONCE_BYTES)
        wrap_aad = f"seller-key|{seller_id}".encode("utf-8")
        wrapped_key = self._master_aead.encrypt(wrapped_nonce, seller_key, wrap_aad)
        self._seller_keys[seller_id] = (seller_key, wrapped_key, wrapped_nonce)
        return seller_key, wrapped_key, wrapped_nonce

    def _unwrap_seller_key(self, doc_id: str, seller_id: str, wrapped_key_hex: str, wrapped_nonce_hex: str) -> bytes:
        try:
            wrapped_key = bytes.fromhex(wrapped_key_hex)
            wrapped_nonce = bytes.fromhex(wrapped_nonce_hex)
        except ValueError as exc:
            raise TamperDetectedError(
                doc_id,
                f"Corrupted wrapped key hex in {doc_id}.meta.json: {exc}",
                failed_location=f"{doc_id}.meta.json",
            ) from exc
        wrap_aad = f"seller-key|{seller_id}".encode("utf-8")
        try:
            return self._master_aead.decrypt(wrapped_nonce, wrapped_key, wrap_aad)
        except Exception as exc:
            raise TamperDetectedError(
                doc_id,
                f"Failed to unwrap seller key for {doc_id}: {exc}",
                failed_location=f"{doc_id}.bin",
            ) from exc

    @staticmethod
    def build_aad(file_sha256: str, seller_id: str) -> bytes:
        return f"{file_sha256}|{seller_id}".encode("utf-8")

    def put_blob(self, doc_id: str, seller_id: str, plaintext_bytes: bytes) -> str:
        """Encrypt `plaintext_bytes` under seller's 256-bit key and write `.bin` + `.meta.json`."""
        if not isinstance(plaintext_bytes, (bytes, bytearray, memoryview)):
            raise TypeError("plaintext_bytes must be bytes-like")
        raw_bytes = bytes(plaintext_bytes)
        file_sha256 = hashlib.sha256(raw_bytes).hexdigest()

        seller_key, wrapped_key, wrapped_nonce = self._get_or_create_seller_key(seller_id)
        nonce = os.urandom(NONCE_BYTES)
        aad = self.build_aad(file_sha256, seller_id)
        ciphertext = AESGCM(seller_key).encrypt(nonce, raw_bytes, aad)

        self.blob_dir.mkdir(parents=True, exist_ok=True)
        self.blob_path(doc_id).write_bytes(ciphertext)

        meta = {
            "doc_id": doc_id,
            "seller_id": seller_id,
            "file_sha256": file_sha256,
            "nonce_hex": nonce.hex(),
            "wrapped_key_hex": wrapped_key.hex(),
            "wrapped_nonce_hex": wrapped_nonce.hex(),
        }
        self.meta_path(doc_id).write_text(
            json.dumps(meta, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return file_sha256

    def get_metadata(self, doc_id: str) -> dict[str, Any]:
        mpath = self.meta_path(doc_id)
        if not mpath.is_file():
            raise TamperDetectedError(
                doc_id,
                f"Missing metadata file {doc_id}.meta.json",
                failed_location=f"{doc_id}.meta.json",
            )
        try:
            data = json.loads(mpath.read_text(encoding="utf-8"))
        except Exception as exc:
            raise TamperDetectedError(
                doc_id,
                f"Corrupted metadata JSON in {doc_id}.meta.json: {exc}",
                failed_location=f"{doc_id}.meta.json",
            ) from exc
        required = ("doc_id", "seller_id", "file_sha256", "nonce_hex", "wrapped_key_hex", "wrapped_nonce_hex")
        for key in required:
            if key not in data or not isinstance(data[key], str):
                raise TamperDetectedError(
                    doc_id,
                    f"Missing or invalid metadata field '{key}' in {doc_id}.meta.json",
                    failed_location=f"{doc_id}.meta.json",
                )
        if data["doc_id"] != doc_id:
            raise TamperDetectedError(
                doc_id,
                f"Metadata doc_id mismatch: expected {doc_id}, found {data['doc_id']}",
                failed_location=f"{doc_id}.meta.json",
            )
        return data

    def get_blob(self, doc_id: str) -> bytes:
        """Decrypt and verify AES-256-GCM tag + SHA-256 hash; raise TamperDetectedError on any modification."""
        bpath = self.blob_path(doc_id)
        if not bpath.is_file():
            raise TamperDetectedError(
                doc_id,
                f"Missing encrypted blob file {doc_id}.bin",
                failed_location=f"{doc_id}.bin",
            )
        meta = self.get_metadata(doc_id)
        seller_id = meta["seller_id"]
        expected_sha256 = meta["file_sha256"]

        seller_key = self._unwrap_seller_key(
            doc_id=doc_id,
            seller_id=seller_id,
            wrapped_key_hex=meta["wrapped_key_hex"],
            wrapped_nonce_hex=meta["wrapped_nonce_hex"],
        )

        try:
            nonce = bytes.fromhex(meta["nonce_hex"])
        except ValueError as exc:
            raise TamperDetectedError(
                doc_id,
                f"Invalid nonce_hex in {doc_id}.meta.json: {exc}",
                failed_location=f"{doc_id}.meta.json",
            ) from exc

        ciphertext = bpath.read_bytes()
        aad = self.build_aad(expected_sha256, seller_id)

        try:
            plaintext = AESGCM(seller_key).decrypt(nonce, ciphertext, aad)
        except InvalidTag as exc:
            raise TamperDetectedError(
                doc_id,
                f"AES-256-GCM authentication tag verification failed on {doc_id}.bin",
                failed_location=f"{doc_id}.bin",
            ) from exc
        except Exception as exc:
            raise TamperDetectedError(
                doc_id,
                f"Decryption failed on {doc_id}.bin: {exc}",
                failed_location=f"{doc_id}.bin",
            ) from exc

        actual_sha256 = hashlib.sha256(plaintext).hexdigest()
        if actual_sha256 != expected_sha256:
            raise TamperDetectedError(
                doc_id,
                f"SHA-256 mismatch on {doc_id}.bin: expected {expected_sha256}, got {actual_sha256}",
                failed_location=f"{doc_id}.bin",
            )
        return plaintext

    def tamper_flip_byte(self, doc_id: str, byte_offset: int = 0) -> Path:
        """Flip 1 byte in `<blob_dir>/<doc_id>.bin` to simulate an off-chain storage tamper attack."""
        bpath = self.blob_path(doc_id)
        if not bpath.is_file():
            raise FileNotFoundError(f"Encrypted blob not found for {doc_id}: {bpath}")
        raw = bytearray(bpath.read_bytes())
        if not raw:
            raw.append(0x01)
        else:
            idx = byte_offset % len(raw)
            raw[idx] ^= 0x01
        bpath.write_bytes(bytes(raw))
        return bpath

"""Comprehensive test suite for Agent A6: Encrypted Off-Chain Audit Store & On-Chain Merkle Anchoring."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from audit.chain import GENESIS_PREV_HASH, EventChain, append_event
from audit.disclose import build_selective_disclosure, verify_selective_disclosure
from audit.merkle import (
    build_event_batch_tree,
    build_field_tree,
    compute_field_leaf_hash,
    internal_hash,
    leaf_hash,
    verify_inclusion_proof,
)
from audit.store import EncryptedBlobStore, TamperDetectedError
from audit.verify import verify
from audit.versions import (
    build_version_created_payload,
    build_version_record,
    create_invoice_version,
    hash_version_record,
)
from chain.anchorer import BackgroundAnchorer, anchor_pending_batch
from chain.deploy import ANCHOR_ABI, ANCHOR_SOL_PATH, deploy_anchor_contract, get_anchor_source
from chain.rpc import AnchorRPCClient, RPCDownError
from contract.models import DocType, Invoice, LineItem, make_gstin


def _make_sample_invoice(invoice_id: str = "INV-2026-001", grand_total_paise: int = 11_80_000) -> Invoice:
    subtotal = (grand_total_paise * 100) // 118
    tax = grand_total_paise - subtotal
    cgst = tax // 2
    sgst = tax - cgst
    return Invoice(
        invoice_id=invoice_id,
        invoice_no="TS/25-26/001",
        financial_year="2025-26",
        doc_type=DocType.INVOICE,
        invoice_date="2026-01-15",
        due_date="2026-03-16",
        seller_id="P-SELL-01",
        seller_gstin=make_gstin("27", "AABCS1429B"),
        buyer_id="P-BUY-01",
        buyer_gstin=make_gstin("27", "AABCB5678C"),
        place_of_supply_state="27",
        po_id="D-PO-001",
        payment_terms_days=60,
        line_items=[
            LineItem(
                line_no=1,
                description="Precision CNC Spindle Assembly",
                hsn_sac="84669310",
                is_service=False,
                qty=10,
                unit="NOS",
                rate_paise=subtotal // 10,
                taxable_paise=subtotal,
                cgst_bps=900,
                sgst_bps=900,
                igst_bps=0,
                tax_paise=tax,
                total_paise=grand_total_paise,
            )
        ],
        subtotal_paise=subtotal,
        cgst_paise=cgst,
        sgst_paise=sgst,
        igst_paise=0,
        total_tax_paise=tax,
        grand_total_paise=grand_total_paise,
    )


def test_encrypted_blob_store_roundtrip_and_1byte_tamper_detection(tmp_path: Path) -> None:
    """Requirement 1: 1-byte file change in encrypted blob store is detected and exact doc_id file is named."""
    blob_dir = tmp_path / "blobs"
    store = EncryptedBlobStore(blob_dir=blob_dir)
    chain = EventChain(tmp_path / "audit.db")
    rpc = AnchorRPCClient()

    doc_id = "D-INV-2026-001"
    seller_id = "P-SELL-01"
    pdf_bytes = "%PDF-1.7\nLabyrinth Clean-Room Invoice Payload ₹11,800.00\n%%EOF".encode("utf-8")

    file_sha256 = store.put_blob(doc_id, seller_id, pdf_bytes)
    assert file_sha256 == hashlib.sha256(pdf_bytes).hexdigest()
    assert store.blob_path(doc_id).is_file()
    assert store.meta_path(doc_id).is_file()

    meta = store.get_metadata(doc_id)
    assert meta["doc_id"] == doc_id
    assert meta["seller_id"] == seller_id
    assert meta["file_sha256"] == file_sha256
    assert len(bytes.fromhex(meta["nonce_hex"])) == 12
    assert len(bytes.fromhex(meta["wrapped_nonce_hex"])) == 12
    assert len(bytes.fromhex(meta["wrapped_key_hex"])) >= 32

    # Roundtrip decryption works even from a fresh EncryptedBlobStore instance
    reopened_store = EncryptedBlobStore(blob_dir=blob_dir)
    assert reopened_store.get_blob(doc_id) == pdf_bytes

    inv = _make_sample_invoice()
    inv_version, version_rec, _ = create_invoice_version(
        inv,
        version_id="V-001",
        file_sha256=file_sha256,
    )
    payload = build_version_created_payload(version_rec, doc_id=doc_id)
    chain.append("invoice.version_created", inv.invoice_id, seller_id, payload)
    anchor_pending_batch(chain, rpc)

    report_ok = verify(chain, store, rpc)
    assert report_ok.integrity.status == "verified"
    assert report_ok.integrity.failed_location is None
    assert report_ok.anchoring.status == "anchored"

    # Flip 1 byte in <blob_dir>/<doc_id>.bin
    store.tamper_flip_byte(doc_id, byte_offset=10)

    with pytest.raises(TamperDetectedError) as exc_info:
        store.get_blob(doc_id)
    assert exc_info.value.doc_id == doc_id
    assert doc_id in exc_info.value.failed_location

    # Full audit verify reports integrity: failed and names the exact doc_id file
    report_tampered = verify(chain, store, rpc)
    assert report_tampered["integrity"]["status"] == "failed"
    assert report_tampered["integrity"]["failed_location"] == f"{doc_id}.bin"
    assert doc_id in report_tampered.integrity.failed_location


def test_mid_chain_event_edit_and_delete_detected_at_exact_seq(tmp_path: Path) -> None:
    """Requirement 2: Editing or deleting an event mid-chain is detected at the exact seq number."""
    chain = EventChain(tmp_path / "events_edit.db")
    for idx in range(1, 6):
        append_event(
            chain,
            f"request.step_{idx}",
            f"REQ-{idx:03d}",
            "P-SELL-01",
            {"step": idx, "amount_paise": idx * 100_000},
        )

    assert verify(chain).integrity.status == "verified"

    # Case A: Edit event payload mid-chain at seq=3
    chain.conn.execute(
        "UPDATE events SET payload_json = ? WHERE seq = 3",
        ('{"amount_paise":99999999,"step":3}',),
    )
    report_edit = verify(chain)
    assert report_edit.integrity.status == "failed"
    assert report_edit.integrity.failed_location == "seq:3"

    # Case B: Delete event mid-chain at seq=4 on a fresh chain
    chain2 = EventChain(tmp_path / "events_delete.db")
    for idx in range(1, 6):
        chain2.append(
            f"request.step_{idx}",
            f"REQ-{idx:03d}",
            "P-SELL-01",
            {"step": idx},
        )
    assert verify(chain2).integrity.status == "verified"

    chain2.conn.execute("DELETE FROM events WHERE seq = 4")
    report_delete = verify(chain2)
    assert report_delete.integrity.status == "failed"
    assert report_delete.integrity.failed_location == "seq:4"


def test_tail_truncation_detected_against_anchor_batch_and_lender_head(tmp_path: Path) -> None:
    """Requirement 3: Deleting the anchored tail of the event table is detected against anchored (last_seq, head_hash) / lender head."""
    # Subcase A: Detected against anchored batch (last_seq, head_hash)
    chain_batch = EventChain(tmp_path / "trunc_batch.db")
    rpc = AnchorRPCClient()
    for idx in range(1, 6):
        chain_batch.append("invoice.event", f"INV-{idx:03d}", "P-SELL-01", {"idx": idx})

    batch = anchor_pending_batch(chain_batch, rpc)
    assert batch is not None
    assert batch["last_seq"] == 5
    assert verify(chain_batch, rpc_client=rpc).integrity.status == "verified"

    # Delete anchored tail (seq 4 and 5)
    chain_batch.conn.execute("DELETE FROM events WHERE seq >= 4")
    report_batch_trunc = verify(chain_batch, rpc_client=rpc)
    assert report_batch_trunc.integrity.status == "failed"
    assert report_batch_trunc.integrity.failed_location is not None
    assert "4..5" in report_batch_trunc.integrity.failed_location
    assert "truncation" in report_batch_trunc.integrity.failed_location

    # Subcase B: Detected against lender_heads even if anchor_batches were not used
    chain_lender = EventChain(tmp_path / "trunc_lender.db")
    for idx in range(1, 5):
        chain_lender.append("reservation.confirmed", f"RSV-{idx:03d}", "L1", {"idx": idx})
    chain_lender.record_lender_head("L1")
    assert verify(chain_lender).integrity.status == "verified"

    # Delete tail event seq=4
    chain_lender.conn.execute("DELETE FROM events WHERE seq = 4")
    report_lender_trunc = verify(chain_lender)
    assert report_lender_trunc.integrity.status == "failed"
    assert report_lender_trunc.integrity.failed_location is not None
    assert "L1" in report_lender_trunc.integrity.failed_location
    assert "4" in report_lender_trunc.integrity.failed_location


def test_events_after_last_anchor_batch_report_verified_and_pending(tmp_path: Path) -> None:
    """Requirement 4: Events appended after the last anchor batch report integrity: 'verified' and anchoring: 'pending'."""
    chain = EventChain(tmp_path / "pending.db")
    store = EncryptedBlobStore(tmp_path / "blobs")
    rpc = AnchorRPCClient()

    doc_sha = store.put_blob("D-INV-001", "P-SELL-01", b"invoice-v1-bytes")
    for idx in range(1, 4):
        chain.append("invoice.step", "INV-001", "P-SELL-01", {"step": idx, "doc_id": "D-INV-001", "file_sha256": doc_sha})

    batch = anchor_pending_batch(chain, rpc)
    assert batch is not None and batch["status"] == "anchored"

    # Verify anchored events report 'anchored'
    anchored_report = verify(chain, store, rpc)
    assert anchored_report.integrity.status == "verified"
    assert anchored_report.anchoring.status == "anchored"

    # Append new event (seq 4) after the anchored batch (which ended at seq 3)
    new_ev = chain.append("decision.scored", "INV-001", "system", {"grade": "A", "score_points": 88})
    assert new_ev.seq == 4

    pending_report = verify(chain, store, rpc)
    assert pending_report["integrity"]["status"] == "verified"
    assert pending_report["anchoring"]["status"] == "pending"

    # Querying seq=2 specifically still reports 'anchored'
    seq2_report = verify(chain, store, rpc, event_seq=2)
    assert seq2_report.integrity.status == "verified"
    assert seq2_report.anchoring.status == "anchored"


def test_rpc_down_reports_anchoring_unavailable_while_integrity_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 5: When RPC is down (LABYRINTH_RPC=http://127.0.0.1:9), verify returns anchoring: 'unavailable' while integrity is 'verified'."""
    chain = EventChain(tmp_path / "rpc_down.db")
    store = EncryptedBlobStore(tmp_path / "blobs")
    rpc = AnchorRPCClient()

    sha = store.put_blob("D-INV-009", "P-SELL-01", b"clean-pdf-content")
    chain.append("invoice.uploaded", "INV-009", "P-SELL-01", {"doc_id": "D-INV-009", "file_sha256": sha})
    anchor_pending_batch(chain, rpc)

    # Point LABYRINTH_RPC to dead port 127.0.0.1:9
    monkeypatch.setenv("LABYRINTH_RPC", "http://127.0.0.1:9")

    with pytest.raises(RPCDownError):
        rpc.health_check()

    report = verify(chain, store, rpc)
    assert report["integrity"]["status"] == "verified"
    assert report["integrity"]["failed_location"] is None
    assert report["anchoring"]["status"] == "unavailable"

    # Also verify that attempting to anchor a new batch while RPC is down does not raise or block,
    # and records status='unavailable'
    chain.append("invoice.verified", "INV-009", "system", {"check": "pass"})
    batch_down = anchor_pending_batch(chain, rpc, max_retries=1, backoff_sec=0.0)
    assert batch_down is not None
    assert batch_down["status"] == "unavailable"

    report2 = verify(chain, store, rpc)
    assert report2.integrity.status == "verified"
    assert report2.anchoring.status == "unavailable"


def test_selective_disclosure_verifies_amount_and_rejects_wrong_salt_or_value(tmp_path: Path) -> None:
    """Requirement 6: Selective disclosure verifies grand_total_paise without revealing other fields; wrong salt or tampered value fails."""
    chain = EventChain(tmp_path / "disclose.db")
    store = EncryptedBlobStore(tmp_path / "blobs")
    rpc = AnchorRPCClient()

    inv = _make_sample_invoice(invoice_id="INV-DISC-01", grand_total_paise=23_60_000)
    file_sha256 = store.put_blob("D-INV-DISC-01", inv.seller_id, b"PDF-CONFIDENTIAL-INVOICE")

    inv_version, version_rec, field_proofs = create_invoice_version(
        inv,
        version_id="V-DISC-01",
        file_sha256=file_sha256,
    )
    ev_payload = build_version_created_payload(version_rec, doc_id="D-INV-DISC-01")
    ev = chain.append("invoice.version_created", inv.invoice_id, inv.seller_id, ev_payload)
    chain.append("request.submitted", "REQ-DISC-01", inv.seller_id, {"invoice_id": inv.invoice_id})

    batch = anchor_pending_batch(chain, rpc)
    assert batch is not None and batch["status"] == "anchored"

    bundle = build_selective_disclosure(
        inv,
        "grand_total_paise",
        version=inv_version,
        field_proofs=field_proofs,
        event=ev,
        chain=chain,
    )

    # Confirm bundle has all required selective disclosure fields and NO other confidential invoice fields
    for required_key in (
        "field_name",
        "field_value",
        "salt_hex",
        "field_proof",
        "field_root",
        "version_record",
        "event_inclusion_proof",
        "batch_root",
    ):
        assert required_key in bundle

    bundle_json_str = str(dict(bundle))
    assert "Precision CNC Spindle Assembly" not in bundle_json_str
    assert inv.seller_gstin not in bundle_json_str
    assert inv.buyer_gstin not in bundle_json_str
    assert bundle["field_name"] == "grand_total_paise"
    assert bundle["field_value"] == 23_60_000

    # Valid disclosure bundle passes verification
    assert verify_selective_disclosure(bundle) is True

    # Tampered field_value (even +1 paisa) fails verification
    tampered_val_bundle = dict(bundle)
    tampered_val_bundle["field_value"] = 23_60_001
    assert verify_selective_disclosure(tampered_val_bundle) is False

    # Tampered salt_hex fails verification
    tampered_salt_bundle = dict(bundle)
    orig_salt = bytes.fromhex(bundle["salt_hex"])
    flipped_salt = bytes([orig_salt[0] ^ 0xFF]) + orig_salt[1:]
    tampered_salt_bundle["salt_hex"] = flipped_salt.hex()
    assert verify_selective_disclosure(tampered_salt_bundle) is False

    # Tampered batch_root fails verification
    tampered_root_bundle = dict(bundle)
    tampered_root_bundle["batch_root"] = "f" * 64
    assert verify_selective_disclosure(tampered_root_bundle) is False


def test_rfc6962_merkle_domain_separation_and_background_anchorer(tmp_path: Path) -> None:
    """Verify RFC 6962 0x00/0x01 domain separation, Anchor.sol contract structure, and BackgroundAnchorer daemon."""
    data = b"sample-leaf-payload"
    lh = leaf_hash(data)
    assert lh == hashlib.sha256(b"\x00" + data).hexdigest()
    ih = internal_hash(lh, lh)
    assert ih == hashlib.sha256(b"\x01" + bytes.fromhex(lh) + bytes.fromhex(lh)).hexdigest()
    assert lh != ih

    # Verify Anchor.sol and ABI
    assert ANCHOR_SOL_PATH.is_file()
    sol_src = get_anchor_source()
    assert "event Anchored(bytes32 indexed root, uint64 firstSeq, uint64 lastSeq, bytes32 head);" in sol_src
    assert "function anchor(bytes32 root, uint64 firstSeq, uint64 lastSeq, bytes32 head)" in sol_src
    deployed = deploy_anchor_contract()
    assert deployed["abi"] == ANCHOR_ABI
    assert deployed["contract_address"].startswith("0x")

    # BackgroundAnchorer batches N=20 events automatically
    chain = EventChain(tmp_path / "bg_anchor.db")
    rpc = AnchorRPCClient()
    anchorer = BackgroundAnchorer(chain, rpc, batch_size=20, poll_interval=0.02)
    anchorer.start()
    try:
        for i in range(1, 21):
            chain.append("test.event", f"E-{i}", "system", {"i": i})
        anchorer.trigger()
        # Wait briefly for daemon to anchor the 20 events
        for _ in range(50):
            if chain.get_last_anchored_seq() == 20:
                break
            import time

            time.sleep(0.01)
        assert chain.get_last_anchored_seq() == 20
        rep = verify(chain, rpc_client=rpc)
        assert rep.integrity.status == "verified"
        assert rep.anchoring.status == "anchored"
    finally:
        anchorer.stop()

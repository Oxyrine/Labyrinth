"""Live engine behind the demo UI.

Every card the UI shows is produced by the real modules: `extract`, `match`, `dedup` (shared SQLite
registry), `network`, `risk`, `workflow`, the AES-GCM blob store, the SQLite hash chain and the anchorer.
Nothing here decides an outcome; it wires modules together and reshapes their output for the UI.

Demo-only simplifications (all surfaced in the UI):
- party/lender/buyer signing keys are derived from a public demo seed (`DEMO_KEY_SEED`); production needs a KMS;
- the anchor "chain" is a local simulated contract unless `TESSERA_RPC` (+ `TESSERA_ANCHOR_KEY`) is set;
- fixture cases carry deterministic stand-in file bytes; uploaded PDFs are stored exactly as received;
- the demo clock follows each request's `submitted_at` so fixture dates stay consistent.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from audit import (
    EncryptedBlobStore,
    EventChain,
    TamperDetectedError,
    build_selective_disclosure,
    build_version_created_payload,
    create_invoice_version,
    verify_selective_disclosure,
)
from audit import verify as audit_verify
from audit.merkle import compute_field_leaf_hash
from chain import AnchorRPCClient, anchor_pending_batch
from contract.context import CaseContext, InMemoryHistoryView
from contract.models import (
    BuyerAcceptance,
    CheckStatus,
    Decision,
    EvidenceDoc,
    FinancingRequest,
    Invoice,
    InvoiceVersion,
    Outcome,
    Party,
    Reservation,
    ReservationState,
    RequestState,
    Severity,
    Tier,
    canonical_json,
    format_inr,
)
from data.generator.world import derive_party_keypair
from dedup.check import compute_outstanding_paise
from dedup.registry import (
    ConsortiumRegistry,
    sign_buyer_acceptance,
    sign_financing_request,
    verify_ed25519_signature,
)
from extract import extract_document
from network.check import run as network_run
from workflow import (
    DisbursementInvariantError,
    OverrideViolationError,
    IllegalStateTransition,
    apply_override,
    clear_recorded_state_events,
    disburse,
    get_recorded_state_events,
    run_pipeline,
    transition_state,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "fixtures"
DEMO_KEY_SEED = 2601
TIER_RANK = {Tier.HIGH.value: 3, Tier.MEDIUM.value: 2, Tier.LOW.value: 1}

# (fixture folder, step number, step label, one-line description). Order = demo script order.
DEMO_STEPS: list[tuple[str, str, str, str]] = [
    ("ring_real_firms", "1", "Step 1 · Consistent 5-Firm Ring", "Every document check passes; the network check surfaces the ring."),
    ("double_financing_cross_lender", "2a", "Step 2a · Cross-Lender Double Financing", "Same invoice already held by another participant."),
    ("concurrent_reservation", "2b", "Step 2b · Concurrent Lender Race (L1 vs L2)", "Two lenders reserve at once; exactly one wins."),
    ("refinance_settled", "2c", "Step 2c · Refinance of Settled Invoice Blocked", "A settled invoice can never be financed again."),
    ("altered_amount_same_identity", "3a", "Step 3a · Edited Resubmission (Same Identity, Altered Amount)", "Same identity, different file and amount."),
    ("near_dup_INV-O042", "3b", "Step 3b · Near-Duplicate Homoglyph (INV-O042 vs INV-0042)", "Homoglyph near-duplicate goes to review."),
    ("clean_goods", "4", "Step 4 · Clean End-to-End Goods Trade", "Extraction, 3-way match, grade, buyer acceptance, confirm, disburse."),
    ("bad_arith", "4b", "Step 4b · Arithmetic Discrepancy Hard Reject", "Planted arithmetic error is a hard failure."),
    ("legit_circular", "6a", "Step 6a · Legitimate Circular Supply Chain", "Real deliveries and outside trade: the legitimacy guard keeps it out of review."),
    ("docs_unavailable", "5b", "Step 5b · Delivery Evidence Unavailable", "Unreachable source is `unavailable`, never `pass`."),
]


class Conflict(Exception):
    """A request the workflow refuses (maps to HTTP 409)."""


class NotFound(KeyError):
    """Unknown case / request / document (maps to HTTP 404)."""


def _now(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _standin_file(sha: str) -> bytes:
    """Deterministic stand-in for a fixture's original file (demo only)."""
    return b"%PDF-1.4\n% tessera demo stand-in for fixture file " + sha.encode("ascii") + b"\n%%EOF\n"


class _State:
    """Everything the engine remembers about one request."""

    def __init__(self, case_id: str, step: str, label: str, description: str, source: str) -> None:
        self.case_id, self.step, self.label, self.description, self.source = case_id, step, label, description, source
        self.ctx: CaseContext
        self.decision: Decision
        self.reservation_id: str | None = None
        self.acceptance: BuyerAcceptance | None = None
        self.file_doc_id = ""
        self.doc_ids: list[str] = []
        self.proofs: dict[str, list[dict[str, str]]] = {}
        self.version_event_seq = 0
        self.extraction: dict[str, Any] | None = None


class LiveEngine:
    def __init__(self, work_dir: str | Path | None = None, rpc_url: str | None = None) -> None:
        self.dir = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="tessera_live_"))
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        master = hashlib.sha256(b"tessera-demo-master-key").digest()  # demo key file stand-in; KMS in production
        self.store = EncryptedBlobStore(self.dir / "blobs", master_key=master)
        self.chain = EventChain(self.dir / "chain.db")
        url = rpc_url if rpc_url is not None else os.environ.get("TESSERA_RPC")
        self.rpc = AnchorRPCClient(rpc_url=url or "mock", private_key=os.environ.get("TESSERA_ANCHOR_KEY"))
        self.live_chain = bool(url and url != "mock")
        self.registry = ConsortiumRegistry(self.dir / "registry.db", audit_callback=self._on_registry_event)
        self.states: dict[str, _State] = {}
        self.order: list[str] = []
        self.lookup: dict[str, str] = {}
        self._originals: dict[str, bytes] = {}
        self._race_n = 0

    # ------------------------------------------------------------------ keys, chain, anchoring
    @staticmethod
    def key(party_id: str) -> tuple[str, str]:
        return derive_party_keypair(DEMO_KEY_SEED, party_id)

    def _on_registry_event(self, ev: Any) -> None:
        self.chain.append(ev.event_type, ev.entity_id, ev.actor_id, dict(ev.payload), timestamp=ev.timestamp)

    def _drain_state_events(self) -> None:
        for ev in get_recorded_state_events():
            self.chain.append(ev.event_type, ev.entity_id, ev.actor_id, dict(ev.payload), timestamp=ev.timestamp)
        clear_recorded_state_events()

    def anchor(self) -> None:
        """Anchor pending events; an outage records the batch as unavailable and never blocks the request."""
        anchor_pending_batch(self.chain, self.rpc, batch_size=500)

    def set_network_off(self, off: bool) -> None:
        self.rpc.set_network_off(off)
        if not off:
            self.anchor()

    @property
    def network_off(self) -> bool:
        return bool(self.rpc.network_off)

    def anchor_label(self) -> str:
        return "live testnet" if self.live_chain else "local simulated anchor contract (no testnet configured)"

    # ------------------------------------------------------------------ submission
    def _prepare(self, raw: dict[str, Any], case_id: str, file_bytes: bytes | None) -> tuple[CaseContext, bytes, dict[str, list[dict[str, str]]]]:
        parties = {
            pid: Party.model_validate(p).model_copy(update={"public_key_hex": self.key(pid)[1]})
            for pid, p in (raw["parties"].items() if isinstance(raw["parties"], dict) else ((p["party_id"], p) for p in raw["parties"]))
        }
        invoice = Invoice.model_validate(raw["invoice"])
        version = InvoiceVersion.model_validate(raw["version"])
        request = FinancingRequest.model_validate(raw["request"])
        evidence = [EvidenceDoc.model_validate(d) for d in raw.get("evidence", [])]
        history = InMemoryHistoryView.model_validate(raw.get("history", {}))

        # Original file: an upload is kept as received; a fixture gets its PDF or a deterministic stand-in.
        old_sha = version.file_sha256
        if file_bytes is None:
            pdf = FIXTURES_DIR / case_id / "invoice.pdf"
            file_bytes = pdf.read_bytes() if pdf.is_file() else _standin_file(old_sha)
        new_sha = hashlib.sha256(file_bytes).hexdigest()
        # Same fixture file hash must stay the same file hash everywhere (duplicates stay duplicates).
        remap = {old_sha: new_sha}
        history = history.model_copy(
            update={"versions": [v.model_copy(update={"file_sha256": remap.get(v.file_sha256, hashlib.sha256(_standin_file(v.file_sha256)).hexdigest())}) for v in history.versions]}
        )

        inv_version, _record, proofs = create_invoice_version(
            invoice,
            version_id=version.version_id,
            file_sha256=new_sha,
            version_no=version.version_no,
            kind=version.kind,
            invoice_identity=version.invoice_identity,
            created_at=version.created_at,
        )
        version = version.model_copy(update={"file_sha256": new_sha, "field_root": inv_version.field_root, "field_salts": inv_version.field_salts})

        # Real signature by the seller's demo key over the canonical request.
        request = request.model_copy(update={"seller_signature_hex": ""})
        request = request.model_copy(update={"seller_signature_hex": sign_financing_request(request, self.key(request.seller_id)[0])})

        # Evidence is stored encrypted; its hash is the hash of the stored bytes.
        stored_docs: list[EvidenceDoc] = []
        for doc in evidence:
            blob = canonical_json(doc.model_dump(mode="json"), allow_floats=True).encode("utf-8")
            sha = self.store.put_blob(doc.doc_id, doc.seller_id, blob)
            stored_docs.append(doc.model_copy(update={"file_sha256": sha}))

        meta = dict(history.metadata)
        meta["registry"] = self.registry
        meta.setdefault("case_id", case_id)
        if file_bytes.startswith(b"%PDF-"):
            meta["pdf_bytes"] = file_bytes
        history = history.model_copy(update={"metadata": meta})
        ctx = CaseContext(request=request, invoice=invoice, version=version, parties=parties, evidence=stored_docs, history=history)
        return ctx, file_bytes, proofs

    def submit(
        self,
        case_id: str,
        raw: dict[str, Any],
        *,
        step: str = "",
        label: str = "",
        description: str = "",
        file_bytes: bytes | None = None,
        extraction: dict[str, Any] | None = None,
        source: str = "fixture",
    ) -> dict[str, Any]:
        with self.lock:
            st = _State(case_id, step, label or case_id, description, source)
            ctx, file_bytes, proofs = self._prepare(raw, case_id, file_bytes)
            st.file_doc_id = f"D-INV-{ctx.invoice.invoice_id.removeprefix('INV-')}"
            self.store.put_blob(st.file_doc_id, ctx.request.seller_id, file_bytes)
            st.doc_ids = [d.doc_id for d in ctx.evidence] + [st.file_doc_id]
            st.proofs = proofs
            st.extraction = extraction

            req = ctx.request
            actor = req.seller_id
            vc = self.chain.append(
                "invoice.version_created",
                ctx.version.version_id,
                actor,
                build_version_created_payload(ctx.version, doc_id=st.file_doc_id),
                timestamp=ctx.version.created_at,
            )
            st.version_event_seq = vc.seq
            self.chain.append(
                "request.submitted",
                req.request_id,
                actor,
                {"request_id": req.request_id, "invoice_id": ctx.invoice.invoice_id, "lender_id": req.lender_id, "amount_paise": req.requested_amount_paise},
                timestamp=req.submitted_at,
            )

            clear_recorded_state_events()
            decision = run_pipeline(ctx, registry=self.registry)
            self._drain_state_events()
            self.chain.append(
                "workflow.decision_recorded",
                req.request_id,
                "pipeline",
                {"request_id": req.request_id, "outcome": decision.outcome.value, "state": decision.state.value,
                 "grade": decision.grade.value, "network_tier": decision.network_tier.value},
                timestamp=decision.decided_at,
            )
            st.ctx, st.decision = ctx, decision
            for chk in decision.checks:
                rid = chk.details.get("reservation_id") if isinstance(chk.details, dict) else None
                if isinstance(rid, str) and rid.startswith("RSV-"):
                    st.reservation_id = rid
            self.states[case_id] = st
            if case_id not in self.order:
                self.order.append(case_id)
            for key in (case_id, req.request_id, ctx.invoice.invoice_id, ctx.version.version_id):
                self.lookup[key] = case_id
            if st.reservation_id:
                self.lookup[st.reservation_id] = case_id
            for d in st.doc_ids:
                self.lookup.setdefault(d, case_id)
            self.anchor()
            return self.card(case_id)

    def load_fixtures(self) -> None:
        """Run every fixture case through the real pipeline (demo script order first)."""
        meta = {cid: (step, label, desc) for cid, step, label, desc in DEMO_STEPS}
        names = [cid for cid, *_ in DEMO_STEPS]
        names += sorted(p.parent.name for p in FIXTURES_DIR.glob("*/case.json") if p.parent.name not in meta)
        for cid in names:
            path = FIXTURES_DIR / cid / "case.json"
            if not path.is_file():
                continue
            step, label, desc = meta.get(cid, ("", f"Extra · {cid}", json.loads(path.read_text(encoding="utf-8")).get("description", "")))
            self.submit(cid, json.loads(path.read_text(encoding="utf-8")), step=step, label=label, description=desc)

    def submit_upload(self, base_case_id: str, pdf_bytes: bytes) -> dict[str, Any]:
        """Real extraction of an uploaded PDF, then the full pipeline against the chosen case's parties and evidence."""
        base = self.states.get(self.lookup.get(base_case_id, base_case_id))
        if base is None:
            raise NotFound(base_case_id)
        sha = hashlib.sha256(pdf_bytes).hexdigest()
        parties = base.ctx.parties
        by_gstin = {p.gstin: p.party_id for p in parties.values()}
        res = extract_document(pdf_bytes, version_id=f"V-UP-{sha[:10]}", created_at=base.ctx.version.created_at, parties_by_gstin=by_gstin)
        invoice, ext_version, ext_checks = res.invoice, res.version, res.checks
        # Resolve the printed PO / contract references and the invoice id against what the platform already holds.
        by_no = {d.doc_no: d.doc_id for d in base.ctx.evidence}
        po = by_no.get(invoice.po_id or "", invoice.po_id)
        contract = by_no.get(invoice.contract_id or "", invoice.contract_id)
        existing = next((i for i in [base.ctx.invoice, *base.ctx.history.invoices]
                         if i.seller_gstin == invoice.seller_gstin and i.invoice_no == invoice.invoice_no), None)
        invoice = invoice.model_copy(update={"po_id": po, "contract_id": contract,
                                             **({"invoice_id": existing.invoice_id} if existing else {})})
        ext_version = ext_version.model_copy(update={"invoice_id": invoice.invoice_id})
        request = base.ctx.request.model_copy(
            update={
                "request_id": f"REQ-UP-{sha[:10]}",
                "invoice_id": invoice.invoice_id,
                "version_id": ext_version.version_id,
                "seller_id": invoice.seller_id,
                "buyer_id": invoice.buyer_id,
                "requested_amount_paise": invoice.grand_total_paise,
            }
        )
        raw = {
            "parties": {pid: p.model_dump(mode="json") for pid, p in parties.items()},
            "invoice": invoice.model_dump(mode="json"),
            "version": ext_version.model_dump(mode="json"),
            "request": request.model_dump(mode="json"),
            "evidence": [d.model_dump(mode="json") for d in base.ctx.evidence],
            "history": base.ctx.history.model_copy(
                update={"metadata": {k: v for k, v in base.ctx.history.metadata.items() if k not in ("registry", "pdf_bytes")}}
            ).model_dump(mode="json"),
        }
        extraction = {
            "method": res.method,
            "validators": [c.model_dump(mode="json") for c in ext_checks],
            "provenance": {k: v.model_dump(mode="json") for k, v in ext_version.field_provenance.items()},
            "printed": {k: v for k, v in res.extras.items() if v},
        }
        card = self.submit(
            f"upload_{sha[:10]}", raw, step="", label=f"Upload · {invoice.invoice_no}",
            description="Uploaded PDF: real extraction, then the full pipeline.",
            file_bytes=pdf_bytes, extraction=extraction, source="upload",
        )
        st = self.states[f"upload_{sha[:10]}"]
        st.ctx = st.ctx.model_copy(update={"version": st.ctx.version.model_copy(update={"field_provenance": ext_version.field_provenance})})
        return self.card(st.case_id)

    # ------------------------------------------------------------------ lookup
    def get(self, ident: str) -> _State:
        cid = self.lookup.get(ident)
        if cid is None or cid not in self.states:
            raise NotFound(ident)
        return self.states[cid]

    # ------------------------------------------------------------------ workflow actions
    def _events(self, st: _State, actor: str, *, since_state: bool = True) -> None:
        self._drain_state_events()
        self.anchor()

    def accept(self, ident: str, buyer_id: str | None, decision: str = "accept") -> dict[str, Any]:
        with self.lock:
            st = self.get(ident)
            dec, ctx = st.decision, st.ctx
            if decision not in ("accept", "reject"):
                raise Conflict("BuyerAcceptance decision must be 'accept' or 'reject'.")
            if dec.outcome != Outcome.CONDITIONALLY_APPROVED:
                raise Conflict(f"Buyer acceptance is only possible after conditional approval (outcome is {dec.outcome.value}).")
            buyer = buyer_id or ctx.request.buyer_id
            if buyer != ctx.request.buyer_id:
                raise Conflict(f"Only the invoice's buyer ({ctx.request.buyer_id}) can accept it.")
            accepted_at = _now(_parse(ctx.request.submitted_at) + timedelta(hours=1))
            acc = BuyerAcceptance(
                acceptance_id=f"ACC-{ctx.request.request_id.removeprefix('REQ-')}",
                invoice_id=ctx.invoice.invoice_id,
                version_id=ctx.version.version_id,
                buyer_id=buyer,
                decision=decision,  # type: ignore[arg-type]
                accepted_amount_paise=ctx.invoice.grand_total_paise,
                accepted_at=accepted_at,
                signature_hex="",
            )
            acc = acc.model_copy(update={"signature_hex": sign_buyer_acceptance(acc, self.key(buyer)[0])})
            if not verify_ed25519_signature(acc, acc.signature_hex, self.key(buyer)[1]):
                raise Conflict("BuyerAcceptance signature does not verify against the buyer's key.")
            clear_recorded_state_events()
            try:
                if decision == "accept":
                    new_state = transition_state(dec.state, RequestState.BUYER_ACCEPTED, entity_id=ctx.request.request_id, actor_id=buyer, timestamp=accepted_at)
                else:
                    # A dispute returns the request to review.
                    new_state = transition_state(dec.state, RequestState.IN_REVIEW, entity_id=ctx.request.request_id, actor_id=buyer, reason="buyer disputed", timestamp=accepted_at)
            except IllegalStateTransition as exc:
                raise Conflict(str(exc)) from exc
            self.chain.append("buyer.acceptance_recorded", ctx.request.request_id, buyer,
                              {"acceptance_id": acc.acceptance_id, "decision": acc.decision, "version_id": acc.version_id,
                               "amount_paise": acc.accepted_amount_paise}, timestamp=accepted_at)
            self._drain_state_events()
            st.acceptance = acc
            update: dict[str, Any] = {"state": new_state}
            if decision == "reject":
                update["outcome"] = Outcome.REVIEW
            st.decision = dec.model_copy(update=update)
            self.anchor()
            return self.card(st.case_id)

    def confirm(self, ident: str, lender_id: str) -> dict[str, Any]:
        with self.lock:
            st = self.get(ident)
            dec, ctx = st.decision, st.ctx
            if st.acceptance is None or dec.state != RequestState.BUYER_ACCEPTED:
                raise Conflict("Cannot confirm: a signed buyer acceptance is required first.")
            if not st.reservation_id:
                raise Conflict("Cannot confirm: this request holds no consortium reservation.")
            if lender_id != ctx.request.lender_id:
                raise Conflict(f"Reservation is owned by lender {ctx.request.lender_id}, not {lender_id}.")
            outstanding = compute_outstanding_paise(ctx, self.registry)
            op_id = hashlib.sha256(f"{ctx.request.request_id}|confirm".encode()).hexdigest()
            res = self.registry.confirm(
                op_id, st.reservation_id, st.acceptance, outstanding_paise=outstanding,
                buyer_public_key_hex=self.key(st.acceptance.buyer_id)[1], now_iso=st.acceptance.accepted_at,
            )
            if not res.get("ok"):
                raise Conflict(f"Registry refused confirmation: {res.get('reason', 'unknown')}.")
            reservation = self.registry.get_reservation(st.reservation_id)
            clear_recorded_state_events()
            try:
                transition_state(dec.state, RequestState.RESERVATION_CONFIRMED, entity_id=ctx.request.request_id, actor_id=lender_id, timestamp=st.acceptance.accepted_at)
                done = disburse(
                    dec.model_copy(update={"state": RequestState.RESERVATION_CONFIRMED}),
                    reservation, st.acceptance, outstanding, lender_id, ctx.request,
                    disbursed_at=st.acceptance.accepted_at,
                )
            except (DisbursementInvariantError, IllegalStateTransition) as exc:
                raise Conflict(str(exc)) from exc
            self._drain_state_events()
            st.decision = done
            self.anchor()
            return self.card(st.case_id)

    def override(self, ident: str, reviewer: str, second: str | None, new_outcome: str, reason: str) -> dict[str, Any]:
        with self.lock:
            st = self.get(ident)
            clear_recorded_state_events()
            when = _now(_parse(st.ctx.request.submitted_at) + timedelta(hours=2))
            try:
                updated = apply_override(st.decision, reviewer, Outcome(new_outcome), reason, second, overridden_at=when)
            except (OverrideViolationError, PermissionError, IllegalStateTransition, ValueError) as exc:
                clear_recorded_state_events()
                raise Conflict(str(exc)) from exc
            # A reject auto-cancelled the reservation. Lifting it needs a fresh atomic reservation, and the
            # registry's capacity and ownership rules still apply: no override can lift them.
            held = self.registry.get_reservation(st.reservation_id) if st.reservation_id else None
            if updated.outcome != Outcome.REJECT and (held is None or held.state in (ReservationState.CANCELLED, ReservationState.EXPIRED)):
                ctx = st.ctx
                res = self.registry.reserve(
                    hashlib.sha256(f"{ctx.request.request_id}|reserve|override{len(updated.overridden_by)}".encode()).hexdigest(),
                    ctx.request, ctx.version.invoice_identity,
                    seller_public_key_hex=self.key(ctx.request.seller_id)[1], now_iso=when,
                    outstanding_paise=compute_outstanding_paise(ctx, self.registry),
                )
                if not res.get("ok"):
                    clear_recorded_state_events()
                    raise Conflict(f"Override refused: the registry will not re-reserve this invoice ({res.get('reason', 'conflict')}). No override can lift that.")
                st.reservation_id = str(res["reservation_id"])
                self.lookup[st.reservation_id] = st.case_id
            st.decision = updated
            ovr = st.decision.overridden_by[-1]
            self.chain.append("workflow.override", st.ctx.request.request_id, reviewer,
                              {"override_id": ovr.override_id, "second_reviewer_id": second, "prior_outcome": ovr.prior_outcome.value,
                               "new_outcome": ovr.new_outcome.value, "reason": ovr.reason, "scores_snapshot": ovr.scores_snapshot},
                              timestamp=ovr.overridden_at)
            self._drain_state_events()
            self.anchor()
            return {"status": "overridden", "request_id": st.ctx.request.request_id, "override": ovr.model_dump(mode="json"),
                    "decision": st.decision.model_dump(mode="json"), "card": self.card(st.case_id)}

    def race(self, ident: str) -> dict[str, Any]:
        """Two lenders reserve the same fresh invoice identity at the same instant on the real registry."""
        with self.lock:
            st = self.get(ident)
            self._race_n += 1
            n = self._race_n
            identity = hashlib.sha256(f"race:{st.ctx.version.invoice_identity}:{n}".encode()).hexdigest()
            seller = st.ctx.request.seller_id
        barrier = threading.Barrier(2)
        out: list[dict[str, Any]] = [{}, {}]

        def worker(slot: int, lender: str) -> None:
            req = st.ctx.request.model_copy(update={"request_id": f"REQ-RACE{n}-{lender}", "lender_id": lender, "seller_signature_hex": ""})
            req = req.model_copy(update={"seller_signature_hex": sign_financing_request(req, self.key(seller)[0])})
            barrier.wait(timeout=5)
            res = self.registry.reserve(
                hashlib.sha256(f"{req.request_id}|reserve".encode()).hexdigest(), req, identity,
                seller_public_key_hex=self.key(seller)[1], now_iso=req.submitted_at,
                outstanding_paise=st.ctx.invoice.grand_total_paise,
            )
            out[slot] = {"lender_id": lender, "request_id": req.request_id, "reservation_id": res.get("reservation_id"), "won": bool(res.get("ok")), "result": res}

        threads = [threading.Thread(target=worker, args=(i, lender)) for i, lender in enumerate(("L1", "L2"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        winners = [o for o in out if o.get("won")]
        losers = [o for o in out if not o.get("won")]
        with self.lock:
            self.anchor()
        active = len(self.registry.active_reservations(identity))
        return {
            "status": "completed", "invoice_identity": identity, "active_reservation_count": active,
            "winner": winners[0] if winners else None, "loser": losers[0] if losers else None, "outcomes": out,
            "explanation": (
                f"Two threads (L1, L2) called the real registry.reserve on identity {identity[:16]}… at the same instant. "
                f"SQLite BEGIN IMMEDIATE plus the partial unique index let exactly {active} reservation exist."
            ),
        }

    # ------------------------------------------------------------------ audit
    def tamper(self, target: str, reset: bool = False) -> dict[str, Any]:
        with self.lock:
            doc_id = self._doc_for(target)
            path = self.store.blob_path(doc_id)
            if reset:
                if doc_id in self._originals:
                    path.write_bytes(self._originals[doc_id])
                return {"status": "restored", "target": doc_id, "failed_file": None, "integrity": "verified", "message": f"Restored original ciphertext of {doc_id}."}
            self._originals.setdefault(doc_id, path.read_bytes())
            self.store.tamper_flip_byte(doc_id, 0)
            return {"status": "tampered", "target": doc_id, "failed_file": doc_id, "integrity": "failed", "message": f"Flipped 1 byte of the stored ciphertext {doc_id}.bin."}

    def restore_all(self) -> None:
        with self.lock:
            for doc_id, original in self._originals.items():
                self.store.blob_path(doc_id).write_bytes(original)

    def _doc_for(self, target: str) -> str:
        if self.store.has_blob(target):
            return target
        return self.get(target).doc_ids[0]

    def verify_target(self, target: str) -> dict[str, Any]:
        """Integrity and anchoring, answered separately, by the real audit module."""
        with self.lock:
            if self.store.has_blob(target):
                st, doc_ids = self.states[self.lookup[target]], [target]
            else:
                st = self.get(target)
                doc_ids = list(st.doc_ids)
            failure: TamperDetectedError | None = None
            for d in doc_ids:
                try:
                    self.store.get_blob(d)
                except TamperDetectedError as exc:
                    failure = exc
                    break
            report = audit_verify(self.chain, None, self.rpc, event_seq=st.version_event_seq or None)
            integrity_ok = failure is None and report.integrity["status"] == "verified"
            failed_file = (failure.doc_id if failure else None) if failure else None
            if failure is not None:
                detail = f"AES-256-GCM authentication failed for {failure.doc_id}.bin: stored ciphertext no longer matches what was encrypted."
            else:
                detail = report.integrity["details"]
            anchoring = report.anchoring
            if anchoring["status"] == "anchored":
                anc_detail = f"Batch {anchoring['batch_id']} root {anchoring['root'][:18]}… found on the {self.anchor_label()} (tx {str(anchoring['tx_hash'])[:18]}…)."
            else:
                anc_detail = anchoring["details"] + " Integrity is unaffected: it is checked locally."
            return {
                "target": target, "case_id": st.case_id,
                "integrity": "verified" if integrity_ok else "failed",
                "integrity_label": "verified" if integrity_ok else f"failed ({failed_file or report.integrity.get('failed_location')})",
                "integrity_detail": detail,
                "failed_file": failed_file, "location": failed_file or report.integrity.get("failed_location"),
                "anchoring": anchoring["status"], "anchoring_detail": anc_detail,
                "anchor_mode": "live" if self.live_chain else "simulated",
                "merkle_root": anchoring.get("root"), "tx_hash": anchoring.get("tx_hash"), "batch_id": anchoring.get("batch_id"),
                "field_root": st.ctx.version.field_root,
                "encrypted_blobs": [self._blob_row(d, st) for d in st.doc_ids],
                "selective_disclosure": self._disclosure(st),
                "events": [e.model_dump(mode="json") for e in self.chain.get_all_events() if e.entity_id in (st.ctx.request.request_id, st.ctx.version.version_id)],
            }

    def _blob_row(self, doc_id: str, st: _State) -> dict[str, Any]:
        meta = self.store.get_metadata(doc_id)
        return {"doc_id": doc_id, "doc_type": next((d.doc_type.value for d in st.ctx.evidence if d.doc_id == doc_id), "invoice_file"),
                "doc_no": next((d.doc_no for d in st.ctx.evidence if d.doc_id == doc_id), st.ctx.invoice.invoice_no),
                "cipher": "AES-256-GCM", "aad": f"{meta['file_sha256']}|{meta['seller_id']}", "file_sha256": meta["file_sha256"],
                "tampered": self._is_tampered(doc_id)}

    def _is_tampered(self, doc_id: str) -> bool:
        try:
            self.store.get_blob(doc_id)
            return False
        except TamperDetectedError:
            return True

    def _disclosure(self, st: _State, field: str = "grand_total_paise") -> dict[str, Any]:
        ev = self.chain.get_event(st.version_event_seq)
        bundle = build_selective_disclosure(
            st.ctx.invoice, field, version=st.ctx.version, field_proofs=st.proofs, event=ev, chain=self.chain,
        )
        value = int(bundle["field_value"])
        return {
            "disclosed_field": field, "disclosed_value_paise": value, "disclosed_formatted": format_inr(value),
            "disclosed_salt": bundle["salt_hex"],
            "leaf_hash": compute_field_leaf_hash(field, value, bundle["salt_hex"]),
            "field_root": bundle["field_root"], "verified": bool(verify_selective_disclosure(bundle)),
            "hidden_fields": sorted(k for k in st.ctx.version.field_salts if k != field),
        }

    # ------------------------------------------------------------------ cards
    def _ring_graph(self, st: _State) -> dict[str, Any]:
        chk = next((c for c in st.decision.checks if c.check == "network.ring"), None)
        ring = (chk.details or {}).get("ring") if chk else None
        return ring_graph_for_ui(ring, st.ctx.parties, list(st.ctx.evidence) + list(st.ctx.history.evidence_docs), (chk.details or {}).get("score", 0.0) if chk else 0.0)

    def _version_diff(self, st: _State) -> list[dict[str, str]]:
        ctx = st.ctx
        rows: list[dict[str, str]] = []
        for prior_inv, prior_ver in ctx.history.prior_versions(ctx.version.invoice_identity):
            if prior_ver.version_id == ctx.version.version_id:
                continue
            for field, label in (("grand_total_paise", "Grand total"), ("invoice_date", "Invoice date"), ("invoice_no", "Invoice number")):
                a, b = getattr(prior_inv, field), getattr(ctx.invoice, field)
                if a != b:
                    fmt = format_inr if field.endswith("_paise") else str
                    delta = f"{fmt(b - a)}" if field.endswith("_paise") else "changed"
                    rows.append({"field": label, "prior": fmt(a), "current": fmt(b), "delta": delta})
            rows.append({"field": "File SHA-256", "prior": prior_ver.file_sha256[:16] + "…", "current": ctx.version.file_sha256[:16] + "…",
                         "delta": "same file" if prior_ver.file_sha256 == ctx.version.file_sha256 else "different file"})
        for chk in st.decision.checks:
            if chk.check == "dedup.near_dup" and chk.status == CheckStatus.WARN:
                rows.append({"field": "Near-duplicate", "prior": "existing invoice", "current": ctx.invoice.invoice_no, "delta": " ".join(chk.evidence)[:200]})
        return rows

    def card(self, ident: str) -> dict[str, Any]:
        st = self.get(ident)
        ctx, dec = st.ctx, st.decision
        checks = [c.model_dump(mode="json") for c in dec.checks]
        stages = {s: [c for c in checks if c["check"].startswith(s + ".")] for s in ("extract", "match", "dedup", "network", "risk")}
        reservation = self.registry.get_reservation(st.reservation_id) if st.reservation_id else None
        hard = [c.check for c in dec.checks if c.status == CheckStatus.FAIL and c.severity == Severity.HARD]
        version = ctx.version.model_dump(mode="json")
        version["field_salts"] = {}
        request = ctx.request.model_dump(mode="json")
        request["state"] = dec.state.value
        proof = self.verify_target(st.case_id)
        decision = dec.model_dump(mode="json")
        return {
            "case_id": st.case_id, "step_number": st.step, "step_label": st.label, "description": st.description,
            "source": st.source, "request_id": ctx.request.request_id, "invoice_id": ctx.invoice.invoice_id, "version_id": ctx.version.version_id,
            "request": request, "invoice": ctx.invoice.model_dump(mode="json"), "version": version,
            "decision": decision, "pricing": decision.get("pricing"),
            "parties": {pid: {k: v for k, v in p.model_dump(mode="json").items() if k != "public_key_hex"} for pid, p in ctx.parties.items()},
            "evidence": [d.model_dump(mode="json") for d in ctx.evidence],
            "three_properties": three_properties(dec.checks, proof["integrity"], proof["anchoring"], self.anchor_label()),
            "checks_by_stage": stages, "ring_graph": self._ring_graph(st), "version_diff": self._version_diff(st),
            "reservation": reservation.model_dump(mode="json") if reservation else None,
            "buyer_acceptance": st.acceptance.model_dump(mode="json") if st.acceptance else None,
            "audit_proof": proof, "extraction": st.extraction,
            "hard_fail_checks": hard, "requires_dual_reviewer": bool(hard),
        }

    def summaries(self) -> list[dict[str, Any]]:
        out = []
        for cid in self.order:
            st = self.states[cid]
            inv, req, dec = st.ctx.invoice, st.ctx.request, st.decision
            out.append({
                "case_id": cid, "step_number": st.step, "step_label": st.label, "description": st.description, "source": st.source,
                "request_id": req.request_id, "invoice_id": inv.invoice_id, "invoice_no": inv.invoice_no,
                "seller_id": req.seller_id, "seller_name": st.ctx.parties.get(req.seller_id, Party.model_construct(name=req.seller_id)).name,
                "buyer_id": req.buyer_id, "buyer_name": st.ctx.parties.get(req.buyer_id, Party.model_construct(name=req.buyer_id)).name,
                "amount_paise": inv.grand_total_paise, "formatted_amount": format_inr(inv.grand_total_paise),
                "outcome": dec.outcome.value, "state": dec.state.value, "grade": dec.grade.value, "score_points": dec.score_points,
                "network_tier": dec.network_tier.value,
            })
        return out

    def queue(self) -> list[dict[str, Any]]:
        items = []
        for cid in self.order:
            st = self.states[cid]
            inv, req, dec = st.ctx.invoice, st.ctx.request, st.decision
            hard = [c.check for c in dec.checks if c.status == CheckStatus.FAIL and c.severity == Severity.HARD]
            tier = dec.network_tier.value
            items.append({
                "case_id": cid, "request_id": req.request_id, "invoice_id": inv.invoice_id, "invoice_no": inv.invoice_no,
                "network_tier": tier, "tier_rank": TIER_RANK.get(tier, 1), "exposure_paise": req.requested_amount_paise,
                "formatted_exposure": format_inr(req.requested_amount_paise), "outcome": dec.outcome.value, "state": dec.state.value,
                "grade": dec.grade.value, "score_points": dec.score_points, "hard_fail_count": len(hard),
                "requires_dual_reviewer": bool(hard), "top_reasons": dec.top_reasons, "conditions": dec.conditions,
                "priority_key": {"tier": tier, "exposure_paise": req.requested_amount_paise},
            })
        items.sort(key=lambda i: (-i["tier_rank"], -i["exposure_paise"], i["request_id"]))
        return items

    # ------------------------------------------------------------------ break-it
    def breakit(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Re-run the real network check on the ring template with the judge's parameters."""
        template = self.states.get("ring_real_firms")
        if template is None:
            raise NotFound("ring_real_firms")
        ctx = _breakit_context(template.ctx, payload)
        chk = network_run(ctx)[0]
        details = chk.details or {}
        tier = details.get("tier", "low")
        ring = details.get("ring")
        return {
            "ring_id": (ring or {}).get("ring_id"), "score": details.get("score", 0.0), "tier": tier, "status": chk.status.value,
            "outcome_recommendation": "review" if tier in ("medium", "high") else "conditionally_approved",
            "evidence": list(chk.evidence),
            "ring_graph": ring_graph_for_ui(ring, ctx.parties, list(ctx.evidence) + list(ctx.history.evidence_docs), details.get("score", 0.0)),
        }


# ---------------------------------------------------------------------- helpers
def three_properties(checks: list[Any], integrity: str, anchoring: str, anchor_label: str) -> dict[str, Any]:
    """Three separate badges: consistency (extract/match/dedup), integrity (audit), authenticity (network)."""
    cons = [c for c in checks if c.check.split(".")[0] in ("extract", "match", "dedup")]
    net = [c for c in checks if c.check.startswith("network.")]
    if any(c.status == CheckStatus.FAIL for c in cons):
        cs, cl = "fail", "Violation detected"
    elif any(c.status == CheckStatus.UNAVAILABLE for c in cons):
        cs, cl = "unavailable", "Missing input -> review"
    elif any(c.status == CheckStatus.WARN for c in cons):
        cs, cl = "warn", "Anomaly -> review"
    else:
        cs, cl = "pass", "All checks consistent"
    if any(c.status in (CheckStatus.WARN, CheckStatus.FAIL) for c in net):
        au, al = "warn", "Elevated ring anomaly (review)"
    elif any(c.status == CheckStatus.UNAVAILABLE for c in net):
        au, al = "unavailable", "Network check unavailable"
    else:
        au, al = "pass", "Low topology signal"
    return {
        "consistency": {"property": "Document Consistency", "modules": "extract + match + dedup", "status": cs, "badge": cl,
                        "summary": "Do the invoice, PO, GRN, contract and registry agree on amounts, rates, dates and identity?"},
        "integrity": {"property": "Document Integrity", "modules": "audit + chain",
                      "status": "pass" if integrity == "verified" and anchoring == "anchored" else ("fail" if integrity != "verified" else "warn"),
                      "integrity": integrity, "anchoring": anchoring,
                      "badge": f"Integrity: {integrity.upper()} · Anchor: {anchoring.upper()}",
                      "summary": f"Has the stored record changed since capture? Checked locally; anchored to the {anchor_label}."},
        "authenticity": {"property": "Trade Authenticity", "modules": "network + match/ewaybill", "status": au, "badge": al,
                         "summary": "Nothing proves goods moved. Ring topology, transit physics and related-party links raise the cost of fabrication."},
    }


def ring_graph_for_ui(ring: dict[str, Any] | None, parties: dict[str, Party], evidence: list[EvidenceDoc], score: float) -> dict[str, Any]:
    """Reshape `network.score.ScoredRing.to_ui_dict()` into the node/edge form ring.js draws."""
    if not ring:
        return {"ring_id": None, "score": score, "tier": "low", "nodes": [], "invoice_edges": [], "corporate_bridges": [], "signals": {}}
    ewb_by_inv: dict[str, list[EvidenceDoc]] = {}
    for d in evidence:
        if d.doc_type.value == "eway_bill" and d.invoice_id:
            ewb_by_inv.setdefault(d.invoice_id, []).append(d)
    retained = ring.get("retained_pct_by_firm", {})
    members = ring.get("members", [])
    nodes = []
    for i, pid in enumerate(members):
        p = parties.get(pid)
        nodes.append({"id": pid, "label": f"{(p.name if p else pid)[:28]} ({pid})", "city": p.city if p else "",
                      "retained_pct": retained.get(pid, 0.0), "role": "seller" if i == 0 else "interior"})
    edges = []
    for hop in ring.get("hops", []):
        inv_id = (hop.get("invoice_ids") or [""])[0]
        notes = [f"{d.vehicle_no} · {(d.generated_at or '')[11:16]}" for d in ewb_by_inv.get(inv_id, []) if d.vehicle_no]
        edges.append({"from": hop["from"], "to": hop["to"], "invoice_id": inv_id, "amount_paise": hop.get("total_paise", 0),
                      "formatted_amount": format_inr(int(hop.get("total_paise", 0))), "date": (hop.get("dates") or [""])[0],
                      "hsn": (hop.get("hsns") or [""])[0], "ewb_note": " ; ".join(notes)})
    bridges = []
    for br in ring.get("bridges", []):
        links = [f"Shared {kind}: {', '.join(map(str, vals))}" for kind, vals in sorted((br.get("links") or {}).items())]
        bridges.append({"from": br["from"], "to": br["to"], "links": links, "label": f"{br.get('link_count', len(links))} independent links"})
    return {"ring_id": ring.get("ring_id"), "score": ring.get("score", score), "tier": ring.get("tier", "low"), "nodes": nodes,
            "invoice_edges": edges, "corporate_bridges": bridges,
            "signals": {k: v for k, v in (ring.get("signals") or {}).items() if v is not None},
            "evidence": ring.get("evidence", [])}


def _breakit_context(base: CaseContext, p: dict[str, Any]) -> CaseContext:
    """Apply the judge's knobs to the ring template: speed, retention, related-party links, duration, HSN change."""
    from network.cities_lookup import road_distance_km  # local: keeps app import light

    speed = max(1.0, float(p.get("ewb_speed_kmh", 410)))
    retained = float(p.get("retained_pct", 0.7))
    links = max(0, min(3, int(p.get("links", p.get("links_count", 2)))))
    days = max(1, int(p.get("total_days", 2)))
    transform = bool(p.get("hsn_transformation", False))

    hist = base.history
    invoices = sorted(hist.invoices, key=lambda i: i.invoice_date)
    n = len(invoices)
    start = datetime.fromisoformat(invoices[0].invoice_date)
    base_amt = invoices[0].grand_total_paise
    new_invoices = []
    for i, inv in enumerate(invoices):
        amt = int(round(base_amt * (1 + retained / 100.0) ** i))
        date = (start + timedelta(days=round(i * days / max(1, n - 1)))).date().isoformat()
        items = inv.line_items
        if transform and items:
            items = [items[0].model_copy(update={"hsn_sac": ["72081000", "73089090", "84313990", "72044900", "72081000"][i % 5]})] + list(items[1:])
        new_invoices.append(inv.model_copy(update={"grand_total_paise": amt, "invoice_date": date, "line_items": items}))

    ewb = [d for d in hist.evidence_docs if d.doc_type.value == "eway_bill" and d.generated_at]
    ewb.sort(key=lambda d: d.generated_at or "")
    new_docs = []
    if len(ewb) >= 2:
        a, b = ewb[0], ewb[1]
        # Same vehicle: origin city of the first bill to origin city of the second bill.
        dist = road_distance_km(a.from_pincode, b.from_pincode) or float(a.distance_km or 1000)
        gap_h = max(0.01, dist / speed)
        t0 = datetime.strptime(a.generated_at, "%Y-%m-%dT%H:%M:%SZ")
        b2 = b.model_copy(update={"generated_at": (t0 + timedelta(hours=gap_h)).strftime("%Y-%m-%dT%H:%M:%SZ")})
        replaced = {b.doc_id: b2}
        new_docs = [replaced.get(d.doc_id, d) for d in hist.evidence_docs]
    else:
        new_docs = list(hist.evidence_docs)

    parties = dict(base.parties)
    ids = sorted(parties)
    first, last = ids[0], ids[-1]
    src = parties[first]
    last_p = parties[last]
    # Each independent link between the closing firm and the opening firm: director, bank, phone, address (PAN is kept distinct).
    uniq = hashlib.sha256(f"breakit:{links}".encode()).hexdigest()
    upd: dict[str, Any] = {
        "directors": list(src.directors) if links >= 1 else [f"DIN-{uniq[:8]}"],
        "bank_account_hash": src.bank_account_hash if links >= 2 else uniq,
        "phone": src.phone if links >= 3 else f"+91-{uniq[8:18]}",
        "address": src.address if links >= 3 else f"{uniq[18:24]} Industrial Estate",
        "pan": "ZZZZZ" + uniq[24:28].upper().translate(str.maketrans("ABCDEF", "123456")) + "Z",
    }
    parties[last] = last_p.model_copy(update=upd)
    new_hist = hist.model_copy(update={"invoices": new_invoices, "evidence_docs": new_docs})
    cur = next((i for i in new_invoices if i.invoice_id == base.invoice.invoice_id), base.invoice)
    return base.model_copy(update={"history": new_hist, "parties": parties, "invoice": cur})

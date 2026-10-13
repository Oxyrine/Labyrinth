"""Concurrent 4-stage verification pipeline, exception guard, auto-cancellation on reject, and batch runner."""

from __future__ import annotations

import importlib
import inspect
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from contract.context import CaseContext, InMemoryHistoryView
from contract.models import (
    CheckResult,
    CheckSource,
    CheckStatus,
    Decision,
    EvidenceDoc,
    FinancingRequest,
    Invoice,
    InvoiceVersion,
    Outcome,
    Party,
    PaymentRecord,
    Reservation,
    ReservationState,
    Severity,
    Tier,
    canonical_json,
)
from risk import check as risk_check
from workflow._stage_fallbacks import (
    fallback_dedup_run,
    fallback_extract_run,
    fallback_match_run,
    fallback_network_run,
)
from workflow.decision import evaluate

ROOT = Path(__file__).resolve().parent.parent

StageRunner = Callable[[CaseContext], list[CheckResult]]

PRIMARY_STAGE_CHECK_ID: dict[str, str] = {
    "extract": "extract.arith_total",
    "match": "match.three_way.parties",
    "dedup": "dedup.registry",
    "network": "network.ring",
    "risk": "risk.grade",
}

_FALLBACK_RUNNERS: dict[str, StageRunner] = {
    "extract": fallback_extract_run,
    "match": fallback_match_run,
    "dedup": fallback_dedup_run,
    "network": fallback_network_run,
    "risk": risk_check.run,
}


def _resolve_stage_runner(
    stage_name: str,
    custom_runners: dict[str, StageRunner] | None = None,
) -> StageRunner:
    if custom_runners and stage_name in custom_runners:
        return custom_runners[stage_name]
    if stage_name == "risk":
        return risk_check.run

    check_file = ROOT / stage_name / "check.py"
    if check_file.exists():
        def _imported_runner(ctx: CaseContext) -> list[CheckResult]:
            mod = importlib.import_module(f"{stage_name}.check")
            return list(mod.run(ctx))

        return _imported_runner

    return _FALLBACK_RUNNERS[stage_name]


def _safe_run_stage(
    stage_name: str,
    runner: StageRunner,
    ctx: CaseContext,
) -> tuple[list[CheckResult], int]:
    """Execute a stage runner, converting any unhandled exception into CheckStatus.UNAVAILABLE (never pass)."""
    t0 = time.perf_counter()
    check_id = PRIMARY_STAGE_CHECK_ID.get(stage_name, f"{stage_name}.check")
    try:
        results = list(runner(ctx))
        elapsed_ms = max(0, int(round((time.perf_counter() - t0) * 1000)))
        if not results:
            return (
                [
                    CheckResult(
                        check=check_id,
                        status=CheckStatus.UNAVAILABLE,
                        severity=Severity.HIGH,
                        source=CheckSource.LIVE,
                        evidence=[
                            f"Stage '{stage_name}' returned 0 check results for invoice {ctx.invoice.invoice_id}; treating as unavailable and routing to human review."
                        ],
                        refs=[ctx.invoice.invoice_id, ctx.request.request_id],
                    )
                ],
                elapsed_ms,
            )
        return results, elapsed_ms
    except Exception as exc:
        elapsed_ms = max(0, int(round((time.perf_counter() - t0) * 1000)))
        err_msg = str(exc).strip() or type(exc).__name__
        if err_msg.lower().startswith("score:"):
            err_msg = f"error value ({err_msg})"
        return (
            [
                CheckResult(
                    check=check_id,
                    status=CheckStatus.UNAVAILABLE,
                    severity=Severity.HIGH,
                    source=CheckSource.LIVE,
                    evidence=[
                        f"Stage '{stage_name}' raised {type(exc).__name__} on invoice {ctx.invoice.invoice_id} ({err_msg}); routing to human review."
                    ],
                    refs=[ctx.invoice.invoice_id, ctx.request.request_id],
                    details={"error_type": type(exc).__name__, "stage": stage_name},
                )
            ],
            elapsed_ms,
        )


def _auto_cancel_reservations_on_reject(
    ctx: CaseContext,
    checks: list[CheckResult],
    registry: Any = None,
) -> None:
    """Automatically cancel any reservation created for this request when the pipeline outcome is REJECT."""
    candidate_rsv_ids: list[str] = []
    for chk in checks:
        if chk.check.startswith("dedup."):
            rsv_id = chk.details.get("reservation_id")
            if isinstance(rsv_id, str) and rsv_id.startswith("RSV-"):
                candidate_rsv_ids.append(rsv_id)
            rsv_obj = chk.details.get("reservation")
            if isinstance(rsv_obj, dict) and isinstance(rsv_obj.get("reservation_id"), str):
                candidate_rsv_ids.append(rsv_obj["reservation_id"])
            for ref in chk.refs:
                if isinstance(ref, str) and ref.startswith("RSV-"):
                    candidate_rsv_ids.append(ref)

    # Check in-memory history reservations
    hist_reservations = getattr(ctx.history, "reservations", None)
    if isinstance(hist_reservations, list):
        for idx, rsv in enumerate(hist_reservations):
            if (
                rsv.request_id == ctx.request.request_id
                or rsv.reservation_id in candidate_rsv_ids
            ) and rsv.state == ReservationState.RESERVED:
                if rsv.reservation_id not in candidate_rsv_ids:
                    candidate_rsv_ids.append(rsv.reservation_id)
                hist_reservations[idx] = rsv.model_copy(
                    update={"state": ReservationState.CANCELLED}
                )

    # Discover registry object if not explicitly passed
    target_registries: list[Any] = []
    if registry is not None:
        target_registries.append(registry)
    hist_reg = getattr(ctx.history, "registry", None)
    if hist_reg is not None and hist_reg not in target_registries:
        target_registries.append(hist_reg)
    try:
        dedup_reg_mod = importlib.import_module("dedup.registry")
        for attr_name in ("DEFAULT_REGISTRY", "default_registry", "registry"):
            reg_inst = getattr(dedup_reg_mod, attr_name, None)
            if reg_inst is not None and hasattr(reg_inst, "cancel") and reg_inst not in target_registries:
                target_registries.append(reg_inst)
        if hasattr(dedup_reg_mod, "cancel") and dedup_reg_mod not in target_registries:
            target_registries.append(dedup_reg_mod)
    except Exception:
        pass

    for reg in target_registries:
        cancel_fn = getattr(reg, "cancel", None)
        if not callable(cancel_fn):
            continue
        rsv_targets = candidate_rsv_ids or [f"RSV-{ctx.request.request_id.removeprefix('REQ-')}"]
        for rsv_id in rsv_targets:
            _invoke_registry_cancel(cancel_fn, rsv_id, ctx)


def _invoke_registry_cancel(cancel_fn: Callable[..., Any], reservation_id: str, ctx: CaseContext) -> None:
    try:
        sig = inspect.signature(cancel_fn)
        params = list(sig.parameters.values())
        kwargs: dict[str, Any] = {}
        available = {
            "reservation_id": reservation_id,
            "rsv_id": reservation_id,
            "request_id": ctx.request.request_id,
            "invoice_identity": ctx.version.invoice_identity,
            "lender_id": ctx.request.lender_id,
            "actor_id": ctx.request.lender_id,
            "seller_id": ctx.request.seller_id,
            "op_id": f"OP-CANCEL-{ctx.request.request_id}",
            "reason": "auto_cancelled_on_reject",
        }
        accepts_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)
        for p in params:
            if p.name in available:
                kwargs[p.name] = available[p.name]
        if kwargs or accepts_var_kw:
            cancel_fn(**kwargs)
            return
    except Exception:
        pass

    # Positional fallbacks
    for args in (
        (reservation_id,),
        (reservation_id, ctx.request.lender_id),
        (ctx.version.invoice_identity,),
    ):
        try:
            cancel_fn(*args)
            return
        except Exception:
            continue


def run_pipeline(
    ctx: CaseContext,
    *,
    disable_network: bool = False,
    registry: Any = None,
    stage_runners: dict[str, StageRunner] | None = None,
    decided_at: str | None = None,
) -> Decision:
    """Execute the 4-stage verification and risk pipeline (`extract` -> `match||dedup||network` -> `risk` -> `decision`)."""
    stage_latencies_ms: dict[str, int] = {}
    stage_outputs: dict[str, list[CheckResult]] = dict(ctx.stage_outputs)

    # Stage 1: Document Extraction & Validators
    extract_runner = _resolve_stage_runner("extract", stage_runners)
    extract_checks, extract_ms = _safe_run_stage("extract", extract_runner, ctx)
    stage_latencies_ms["extract"] = extract_ms
    stage_outputs["extract"] = extract_checks

    ctx_stage2 = ctx.model_copy(update={"stage_outputs": dict(stage_outputs)})

    # Stage 2: Parallel Cross-Document Matching, Dedup/Registry, and Network Ring Analysis
    match_runner = _resolve_stage_runner("match", stage_runners)
    dedup_runner = _resolve_stage_runner("dedup", stage_runners)

    if disable_network:
        def _disabled_network_runner(c: CaseContext) -> list[CheckResult]:
            return [
                CheckResult(
                    check="network.ring",
                    status=CheckStatus.NOT_APPLICABLE,
                    severity=Severity.INFO,
                    source=CheckSource.LIVE,
                    evidence=[
                        f"Network ring analysis disabled in rules-only baseline mode for invoice {c.invoice.invoice_id}."
                    ],
                    refs=[c.invoice.invoice_id],
                    details={"tier": Tier.LOW.value},
                )
            ]

        network_runner: StageRunner = _disabled_network_runner
    else:
        network_runner = _resolve_stage_runner("network", stage_runners)

    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_match = pool.submit(_safe_run_stage, "match", match_runner, ctx_stage2)
        fut_dedup = pool.submit(_safe_run_stage, "dedup", dedup_runner, ctx_stage2)
        fut_network = pool.submit(_safe_run_stage, "network", network_runner, ctx_stage2)

        match_checks, match_ms = fut_match.result()
        dedup_checks, dedup_ms = fut_dedup.result()
        network_checks, network_ms = fut_network.result()

    stage_latencies_ms["match"] = match_ms
    stage_latencies_ms["dedup"] = dedup_ms
    stage_latencies_ms["network"] = network_ms

    stage_outputs["match"] = match_checks
    stage_outputs["dedup"] = dedup_checks
    stage_outputs["network"] = network_checks

    # Stage 3: Buyer & Receivables Risk (receives stage_outputs including network)
    ctx_stage3 = ctx.model_copy(update={"stage_outputs": dict(stage_outputs)})
    risk_runner = _resolve_stage_runner("risk", stage_runners)
    risk_checks, risk_ms = _safe_run_stage("risk", risk_runner, ctx_stage3)
    stage_latencies_ms["risk"] = risk_ms
    stage_outputs["risk"] = risk_checks

    all_checks: list[CheckResult] = [
        *extract_checks,
        *match_checks,
        *dedup_checks,
        *network_checks,
        *risk_checks,
    ]

    # Stage 4: Decision Evaluation & Auto-Cancel on Reject
    t_dec = time.perf_counter()
    ctx_stage4 = ctx.model_copy(update={"stage_outputs": dict(stage_outputs)})
    decision = evaluate(
        ctx_stage4,
        all_checks,
        stage_latencies_ms=stage_latencies_ms,
        decided_at=decided_at,
        disable_network=disable_network,
    )
    dec_ms = max(0, int(round((time.perf_counter() - t_dec) * 1000)))
    decision.stage_latencies_ms["decision"] = dec_ms

    if decision.outcome == Outcome.REJECT:
        _auto_cancel_reservations_on_reject(ctx_stage4, all_checks, registry=registry)

    return decision


def load_case_context_from_fixture(fixture_dir: Path) -> CaseContext:
    """Hydrate a CaseContext from a fixture folder containing `case.json`."""
    case_path = fixture_dir / "case.json" if fixture_dir.is_dir() else fixture_dir
    raw = json.loads(case_path.read_text(encoding="utf-8"))

    parties_raw = raw.get("parties", {})
    if isinstance(parties_raw, dict):
        parties = {k: Party.model_validate(v) for k, v in parties_raw.items()}
    else:
        parties = {p["party_id"]: Party.model_validate(p) for p in parties_raw}

    history_raw = dict(raw.get("history", {}))
    if "case_id" in raw:
        meta = dict(history_raw.get("metadata", {}))
        meta.setdefault("case_id", raw["case_id"])
        history_raw["metadata"] = meta

    history = InMemoryHistoryView.model_validate(history_raw)
    return CaseContext(
        request=FinancingRequest.model_validate(raw["request"]),
        invoice=Invoice.model_validate(raw["invoice"]),
        version=InvoiceVersion.model_validate(raw["version"]),
        parties=parties,
        evidence=[EvidenceDoc.model_validate(d) for d in raw.get("evidence", [])],
        history=history,
    )


def run_case_from_fixture(
    fixture_dir: Path | str,
    *,
    disable_network: bool = False,
    registry: Any = None,
    stage_runners: dict[str, StageRunner] | None = None,
) -> Decision:
    """Load `fixture_dir/case.json` and execute the full verification pipeline."""
    path = Path(fixture_dir)
    if not path.is_absolute():
        path = ROOT / path
    ctx = load_case_context_from_fixture(path)
    return run_pipeline(
        ctx,
        disable_network=disable_network,
        registry=registry,
        stage_runners=stage_runners,
    )


def _load_json_or_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.suffix == ".jsonl":
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    return []


def _load_contexts_for_regime(regime_dir: Path) -> list[CaseContext]:
    """Load CaseContexts from generated world artifacts in `data/out/regime_{regime}` or fallback to `fixtures/`."""
    cases_dir = regime_dir / "cases"
    if cases_dir.exists() and cases_dir.is_dir():
        case_files = sorted(cases_dir.rglob("case.json")) or sorted(cases_dir.glob("*.json"))
        if case_files:
            return [load_case_context_from_fixture(cf) for cf in case_files]

    cases_json = regime_dir / "cases.json"
    if cases_json.exists():
        raw_cases = json.loads(cases_json.read_text(encoding="utf-8"))
        if isinstance(raw_cases, list) and raw_cases:
            tmp_contexts: list[CaseContext] = []
            for raw in raw_cases:
                parties_raw = raw.get("parties", {})
                parties = (
                    {k: Party.model_validate(v) for k, v in parties_raw.items()}
                    if isinstance(parties_raw, dict)
                    else {p["party_id"]: Party.model_validate(p) for p in parties_raw}
                )
                history_raw = dict(raw.get("history", {}))
                meta = dict(history_raw.get("metadata", {}))
                meta.setdefault("case_id", raw.get("case_id"))
                history_raw["metadata"] = meta
                tmp_contexts.append(
                    CaseContext(
                        request=FinancingRequest.model_validate(raw["request"]),
                        invoice=Invoice.model_validate(raw["invoice"]),
                        version=InvoiceVersion.model_validate(raw["version"]),
                        parties=parties,
                        evidence=[EvidenceDoc.model_validate(d) for d in raw.get("evidence", [])],
                        history=InMemoryHistoryView.model_validate(history_raw),
                    )
                )
            return tmp_contexts

    cases_jsonl = regime_dir / "cases.jsonl"
    if cases_jsonl.exists():
        contexts: list[CaseContext] = []
        for line in cases_jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            parties_raw = raw.get("parties", {})
            parties = (
                {k: Party.model_validate(v) for k, v in parties_raw.items()}
                if isinstance(parties_raw, dict)
                else {p["party_id"]: Party.model_validate(p) for p in parties_raw}
            )
            contexts.append(
                CaseContext(
                    request=FinancingRequest.model_validate(raw["request"]),
                    invoice=Invoice.model_validate(raw["invoice"]),
                    version=InvoiceVersion.model_validate(raw["version"]),
                    parties=parties,
                    evidence=[EvidenceDoc.model_validate(d) for d in raw.get("evidence", [])],
                    history=InMemoryHistoryView.model_validate(raw.get("history", {})),
                )
            )
        if contexts:
            return contexts

    # Check if world files (requests.json/jsonl, invoices.json/jsonl, etc.) exist in regime_dir
    world_json = regime_dir / "world.json"
    if world_json.exists():
        w = json.loads(world_json.read_text(encoding="utf-8"))
        reqs = [FinancingRequest.model_validate(r) for r in w.get("requests", [])]
        invs = [Invoice.model_validate(i) for i in w.get("invoices", [])]
        vers = [InvoiceVersion.model_validate(v) for v in w.get("versions", [])]
        parties_raw = w.get("parties", {})
        parties = (
            {k: Party.model_validate(v) for k, v in parties_raw.items()}
            if isinstance(parties_raw, dict)
            else {p["party_id"]: Party.model_validate(p) for p in parties_raw}
        )
        ev_docs = [EvidenceDoc.model_validate(d) for d in w.get("evidence_docs", w.get("evidence", []))]
        payments = [PaymentRecord.model_validate(p) for p in w.get("payments", [])]
        reservations = [Reservation.model_validate(r) for r in w.get("reservations", [])]
        allocations = {k: int(v) for k, v in w.get("allocations_paise", {}).items()}
        return _build_contexts_from_tables(
            reqs, invs, vers, parties, ev_docs, payments, reservations, allocations
        )

    reqs_raw = _load_json_or_jsonl(regime_dir / "requests.jsonl") or _load_json_or_jsonl(
        regime_dir / "requests.json"
    )
    if reqs_raw:
        reqs = [FinancingRequest.model_validate(r) for r in reqs_raw]
        invs = [
            Invoice.model_validate(i)
            for i in (
                _load_json_or_jsonl(regime_dir / "invoices.jsonl")
                or _load_json_or_jsonl(regime_dir / "invoices.json")
            )
        ]
        vers = [
            InvoiceVersion.model_validate(v)
            for v in (
                _load_json_or_jsonl(regime_dir / "versions.jsonl")
                or _load_json_or_jsonl(regime_dir / "versions.json")
            )
        ]
        parties_list = _load_json_or_jsonl(regime_dir / "parties.jsonl") or _load_json_or_jsonl(
            regime_dir / "parties.json"
        )
        parties = {p["party_id"]: Party.model_validate(p) for p in parties_list}
        ev_docs = [
            EvidenceDoc.model_validate(d)
            for d in (
                _load_json_or_jsonl(regime_dir / "evidence.jsonl")
                or _load_json_or_jsonl(regime_dir / "evidence.json")
                or _load_json_or_jsonl(regime_dir / "evidence_docs.jsonl")
                or _load_json_or_jsonl(regime_dir / "evidence_docs.json")
            )
        ]
        payments = [
            PaymentRecord.model_validate(p)
            for p in (
                _load_json_or_jsonl(regime_dir / "payments.jsonl")
                or _load_json_or_jsonl(regime_dir / "payments.json")
            )
        ]
        reservations = [
            Reservation.model_validate(r)
            for r in (
                _load_json_or_jsonl(regime_dir / "reservations.jsonl")
                or _load_json_or_jsonl(regime_dir / "reservations.json")
            )
        ]
        return _build_contexts_from_tables(
            reqs, invs, vers, parties, ev_docs, payments, reservations, {}
        )

    # Fallback: load all fixture cases in fixtures/*/case.json
    fixtures_dir = ROOT / "fixtures"
    fixture_cases = sorted(fixtures_dir.glob("*/case.json"))
    return [load_case_context_from_fixture(fc) for fc in fixture_cases]


def _build_contexts_from_tables(
    reqs: list[FinancingRequest],
    invs: list[Invoice],
    vers: list[InvoiceVersion],
    parties: dict[str, Party],
    ev_docs: list[EvidenceDoc],
    payments: list[PaymentRecord],
    reservations: list[Reservation],
    allocations: dict[str, int],
) -> list[CaseContext]:
    inv_map = {i.invoice_id: i for i in invs}
    ver_map = {v.version_id: v for v in vers}
    ev_by_inv: dict[str, list[EvidenceDoc]] = {}
    for d in ev_docs:
        if d.invoice_id:
            ev_by_inv.setdefault(d.invoice_id, []).append(d)

    shared_history = InMemoryHistoryView(
        invoices=invs,
        versions=vers,
        payments=payments,
        evidence_docs=ev_docs,
        reservations=reservations,
        allocations_paise=allocations,
    )
    out: list[CaseContext] = []
    for req in reqs:
        inv = inv_map.get(req.invoice_id)
        ver = ver_map.get(req.version_id)
        if inv is None or ver is None:
            continue
        out.append(
            CaseContext(
                request=req,
                invoice=inv,
                version=ver,
                parties=parties,
                evidence=ev_by_inv.get(inv.invoice_id, []),
                history=shared_history,
            )
        )
    return out


def _write_extractions(target_dir: Path) -> None:
    """Extract every generated PDF / scan and record what the extractor read (eval compares it to ground truth)."""
    from extract import ExtractionError, extract_document

    rows: list[dict[str, Any]] = []
    for sub, kind in (("pdfs", "pdf"), ("scans", "scan")):
        for f in sorted((target_dir / sub).glob("*.pdf")):
            row: dict[str, Any] = {"file": f"{sub}/{f.name}", "kind": kind}
            try:
                res = extract_document(f.read_bytes())
                row.update(
                    status="ok", layout=res.extras.get("layout"), method=res.method,
                    invoice=res.invoice.model_dump(mode="json"),
                    min_confidence=min(p.confidence for p in res.version.field_provenance.values()),
                )
            except ExtractionError as exc:
                row.update(status="unreadable", error=str(exc), missing=exc.missing)
            rows.append(row)
    if rows:
        (target_dir / "extractions.jsonl").write_text(
            "\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n", encoding="utf-8"
        )


def run_batch_regime(
    regime: str = "A",
    *,
    out_dir: Path | None = None,
) -> tuple[list[Decision], list[dict[str, Any]]]:
    """Run both hybrid and rules-only baseline pipelines over `regime` and write JSONL/JSON outputs."""
    target_dir = out_dir if out_dir is not None else (ROOT / "data" / "out" / f"regime_{regime}")
    target_dir.mkdir(parents=True, exist_ok=True)

    _write_extractions(target_dir)
    contexts = _load_contexts_for_regime(target_dir)
    decisions: list[Decision] = []
    baseline_decisions: list[Decision] = []
    rings_by_id: dict[str, dict[str, Any]] = {}

    rings_path = target_dir / "rings.json"
    if rings_path.exists():
        try:
            existing_rings = json.loads(rings_path.read_text(encoding="utf-8"))
            if isinstance(existing_rings, list):
                for r in existing_rings:
                    if isinstance(r, dict) and "ring_id" in r:
                        rings_by_id[str(r["ring_id"])] = r
        except Exception:
            pass

    for ctx in contexts:
        dec = run_pipeline(ctx, disable_network=False)
        decisions.append(dec)

        base_dec = run_pipeline(ctx, disable_network=True)
        baseline_decisions.append(base_dec)

        for chk in dec.checks:
            if chk.check == "network.ring":
                det_rings = chk.details.get("rings")
                if isinstance(det_rings, list):
                    for r in det_rings:
                        if isinstance(r, dict) and "ring_id" in r:
                            rings_by_id[str(r["ring_id"])] = r
                elif isinstance(chk.details.get("ring_id"), str):
                    rid = chk.details["ring_id"]
                    rings_by_id.setdefault(
                        rid,
                        {
                            "ring_id": rid,
                            "tier": dec.network_tier.value,
                            "invoice_ids": [dec.invoice_id],
                            "evidence": list(chk.evidence),
                        },
                    )

    decisions_path = target_dir / "decisions.jsonl"
    decisions_path.write_text(
        "\n".join(canonical_json(d, allow_floats=True) for d in decisions) + ("\n" if decisions else ""),
        encoding="utf-8",
    )

    baseline_path = target_dir / "decisions_baseline.jsonl"
    baseline_path.write_text(
        "\n".join(canonical_json(d, allow_floats=True) for d in baseline_decisions)
        + ("\n" if baseline_decisions else ""),
        encoding="utf-8",
    )

    rings_list = list(rings_by_id.values())
    rings_path.write_text(
        json.dumps(rings_list, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return decisions, rings_list

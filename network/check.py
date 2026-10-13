"""Stage 2c Network Authenticity seam: network/check.py:run(ctx: CaseContext) -> list[CheckResult]."""

from __future__ import annotations

from contract.context import CaseContext
from contract.models import (
    CheckResult,
    CheckSource,
    CheckStatus,
    DocType,
    EvidenceDoc,
    Invoice,
    Severity,
    Tier,
    format_inr,
)
from network.cycles import find_cycles
from network.graph import TradeGraph
from network.score import ScoredRing, rank_rings, score_ring


def _collect_context_invoices(ctx: CaseContext) -> list[Invoice]:
    by_id: dict[str, Invoice] = {}
    if ctx.history is not None and hasattr(ctx.history, "all_invoices"):
        for inv in ctx.history.all_invoices():
            if isinstance(inv, Invoice) and inv.doc_type == DocType.INVOICE:
                by_id[inv.invoice_id] = inv
    if ctx.invoice is not None and ctx.invoice.doc_type == DocType.INVOICE:
        by_id[ctx.invoice.invoice_id] = ctx.invoice
    return [by_id[k] for k in sorted(by_id.keys())]


def _collect_context_evidence(ctx: CaseContext) -> list[EvidenceDoc]:
    by_id: dict[str, EvidenceDoc] = {}
    if ctx.history is not None and hasattr(ctx.history, "all_evidence"):
        for doc in ctx.history.all_evidence():
            if isinstance(doc, EvidenceDoc):
                by_id[doc.doc_id] = doc
    for doc in ctx.evidence or ():
        if isinstance(doc, EvidenceDoc):
            by_id[doc.doc_id] = doc
    return [by_id[k] for k in sorted(by_id.keys())]


def _select_touching_ring(
    scored_rings: list[ScoredRing],
    u: str,
    v: str,
) -> ScoredRing | None:
    if not scored_rings:
        return None

    # 1. Rings directly containing directed hop (u, v)
    direct_hop_rings = [r for r in scored_rings if (u, v) in r.invoice_hops]
    if direct_hop_rings:
        return rank_rings(direct_hop_rings)[0]

    # 2. Rings containing both endpoints u and v
    both_member_rings = [r for r in scored_rings if u in r.members and v in r.members]
    if both_member_rings:
        return rank_rings(both_member_rings)[0]

    # 3. Rings touching seller u or buyer v
    touching_rings = [r for r in scored_rings if u in r.members or v in r.members]
    if touching_rings:
        return rank_rings(touching_rings)[0]

    return None


def run(ctx: CaseContext) -> list[CheckResult]:
    """Execute Stage 2c Network Authenticity & Ring Analysis (`network.ring`)."""
    if ctx is None or ctx.invoice is None:
        return [
            CheckResult(
                check="network.ring",
                status=CheckStatus.UNAVAILABLE,
                severity=Severity.MEDIUM,
                source=CheckSource.LIVE,
                evidence=["Network ring check could not execute because invoice context is unavailable."],
                refs=[],
                details={"ring": None, "tier": Tier.LOW.value, "score": 0.0},
            )
        ]

    invoices = _collect_context_invoices(ctx)
    evidence_docs = _collect_context_evidence(ctx)
    graph = TradeGraph(
        parties=ctx.parties,
        invoices=invoices,
        evidence_docs=evidence_docs,
    )

    candidates = find_cycles(graph)
    scored_rings = [score_ring(cand, graph) for cand in candidates]

    u = graph.resolve(ctx.invoice.seller_id)
    v = graph.resolve(ctx.invoice.buyer_id)
    selected = _select_touching_ring(scored_rings, u, v)

    if selected is None:
        return [
            CheckResult(
                check="network.ring",
                status=CheckStatus.PASS,
                severity=Severity.INFO,
                source=CheckSource.LIVE,
                evidence=[
                    f"No circular trade ring or multi-link corporate bridge detected touching invoice "
                    f"{ctx.invoice.invoice_id} ({format_inr(ctx.invoice.grand_total_paise)}) between "
                    f"{u} and {v} across {len(invoices)} trade invoice(s)."
                ],
                refs=[ctx.invoice.invoice_id],
                details={
                    "ring": None,
                    "tier": Tier.LOW.value,
                    "score": 0.0,
                },
            )
        ]

    if selected.tier == Tier.HIGH:
        status = CheckStatus.WARN
        severity = Severity.HIGH
    elif selected.tier == Tier.MEDIUM:
        status = CheckStatus.WARN
        severity = Severity.MEDIUM
    else:
        status = CheckStatus.PASS
        severity = Severity.INFO

    ring_dict = selected.to_ui_dict(graph)
    hop_inv_ids: set[str] = {ctx.invoice.invoice_id}
    for hop in ring_dict["hops"]:
        hop_inv_ids.update(hop["invoice_ids"])

    return [
        CheckResult(
            check="network.ring",
            status=status,
            severity=severity,
            source=CheckSource.LIVE,
            evidence=list(selected.evidence),
            refs=sorted(hop_inv_ids),
            details={
                "ring": ring_dict,
                "tier": selected.tier.value,
                "score": round(float(selected.score), 4),
            },
        )
    ]

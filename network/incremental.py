"""Incremental ring discovery and re-scoring on invoice arrival and party updates."""

from __future__ import annotations

from collections import deque
from typing import Iterable, Mapping

from contract.models import EvidenceDoc, Invoice, Party
from network.cycles import (
    DEFAULT_MAX_HOPS,
    RingCandidate,
    assign_ring_ids,
)
from network.graph import TradeGraph
from network.score import ScoredRing, rank_rings, score_ring


def discover_on_edge(
    graph: TradeGraph,
    u: str,
    v: str,
    *,
    max_forward_hops: int = 6,
    max_combined_hops: int = DEFAULT_MAX_HOPS,
) -> list[RingCandidate]:
    """Discover all transaction-closed and corporate-bridge-closed rings traversed by invoice edge u -> v.

    (a) Bounded DFS v ~> u (<= 6 invoice hops) for transaction-closed rings.
    (b) Related-party closure: backward BFS from u and forward BFS from v (combined <= 7 hops),
        joining endpoints (x, y) that share >= 2 independent related links (Corporate Bridge).
    """
    cu = graph.resolve(u)
    cv = graph.resolve(v)
    if cu == cv or not graph.invoices_on_edge(cu, cv):
        return []

    found: dict[tuple[tuple[str, ...], tuple[tuple[str, str], ...]], RingCandidate] = {}

    # (a) Bounded iterative DFS from cv to cu (<= max_forward_hops invoice edges)
    path: list[str] = [cv]
    in_path: set[str] = {cu, cv}
    stack: list[list[int | str]] = [[cv, 0]]

    while stack:
        frame = stack[-1]
        curr = str(frame[0])
        nbr_idx = int(frame[1])
        nbrs = graph.out_neighbors(curr)

        if nbr_idx < len(nbrs):
            nxt = nbrs[nbr_idx]
            frame[1] = nbr_idx + 1
            if nxt == cu:
                # Cycle (cu, cv, ..., curr) has len(path) + 1 nodes and hops
                if (len(path) + 1) <= max_combined_hops:
                    cand = RingCandidate.from_cycle([cu] + path)
                    found[(cand.canonical_key, cand.bridges)] = cand
            elif nxt not in in_path and len(path) < max_forward_hops:
                path.append(nxt)
                in_path.add(nxt)
                stack.append([nxt, 0])
        else:
            stack.pop()
            popped = path.pop()
            if popped != cu:
                in_path.remove(popped)

    pure_cycle_member_sets = [frozenset(k[0]) for k in found.keys()]

    # (b) Related-party closure:
    # Backward BFS from cu along incoming invoice edges: paths x = b_0 -> ... -> cu
    back_paths: list[tuple[str, ...]] = [(cu,)]
    q_back: deque[tuple[str, ...]] = deque([(cu,)])
    while q_back:
        b_path = q_back.popleft()
        if (len(b_path) - 1) >= (max_combined_hops - 1):
            continue
        head = b_path[0]
        for prev_node in graph.in_neighbors(head):
            if prev_node == cv or prev_node in b_path:
                continue
            new_b = (prev_node,) + b_path
            back_paths.append(new_b)
            q_back.append(new_b)

    # Forward BFS from cv along outgoing invoice edges: paths cv -> ... -> y
    fwd_paths: list[tuple[str, ...]] = [(cv,)]
    q_fwd: deque[tuple[str, ...]] = deque([(cv,)])
    while q_fwd:
        f_path = q_fwd.popleft()
        if (len(f_path) - 1) >= (max_combined_hops - 1):
            continue
        tail = f_path[-1]
        for nxt_node in graph.out_neighbors(tail):
            if nxt_node == cu or nxt_node in f_path:
                continue
            new_f = f_path + (nxt_node,)
            fwd_paths.append(new_f)
            q_fwd.append(new_f)

    for b_path in back_paths:
        b_hops = len(b_path) - 1
        x = b_path[0]
        b_set = set(b_path)
        for f_path in fwd_paths:
            f_hops = len(f_path) - 1
            total_invoice_hops = b_hops + 1 + f_hops
            if total_invoice_hops < 2 or total_invoice_hops > max_combined_hops:
                continue
            y = f_path[-1]
            if x == y:
                continue
            # Lesson 4: endpoint (x, y) MUST have >= 2 independent related links
            if not graph.is_corporate_bridge(y, x):
                continue
            if not b_set.isdisjoint(set(f_path)):
                continue
            full_path = b_path + f_path
            cand = RingCandidate.from_bridge_path(full_path)
            cand_set = frozenset(cand.members)
            if not any(cand_set.issubset(ps) for ps in pure_cycle_member_sets):
                found[(cand.canonical_key, cand.bridges)] = cand

    return assign_ring_ids(found.values())


class IncrementalRingEngine:
    """Stateful online ring discovery and re-scoring engine."""

    def __init__(
        self,
        parties: Iterable[Party] | Mapping[str, Party] | None = None,
        invoices: Iterable[Invoice] | None = None,
        evidence_docs: Iterable[EvidenceDoc] | None = None,
    ) -> None:
        self.graph = TradeGraph(parties=parties, evidence_docs=evidence_docs)
        self._candidates: dict[tuple[tuple[str, ...], tuple[tuple[str, str], ...]], RingCandidate] = {}
        self._scored_rings: dict[str, ScoredRing] = {}

        if invoices is not None:
            for inv in invoices:
                self.add_invoice(inv)

    def add_evidence(self, doc: EvidenceDoc) -> list[ScoredRing]:
        """Add an evidence doc (e.g. EWAY_BILL or GRN) and re-score rings touching its parties."""
        self.graph.add_evidence(doc)
        return self._rescore_all()

    def add_invoice(self, invoice: Invoice) -> list[ScoredRing]:
        """Ingest a single invoice, discover any newly closed transaction or corporate-bridge rings,
        and re-score all rings affected by the new edge or outside volume change.
        """
        added = self.graph.add_invoice(invoice)
        if not added:
            return self.rings()

        u = self.graph.resolve(invoice.seller_id)
        v = self.graph.resolve(invoice.buyer_id)
        new_cands = discover_on_edge(self.graph, u, v)
        for cand in new_cands:
            self._candidates[(cand.canonical_key, cand.bridges)] = cand

        return self._rescore_all()

    def update_party(self, party: Party) -> list[ScoredRing]:
        """Update a party's attributes (e.g. directors, bank hash, phone, address), discover any
        newly formed corporate-bridge rings, and re-score affected rings.
        """
        canon_id = self.graph.add_or_update_party(party)
        # Check if new corporate bridges involving canon_id close any existing invoice paths
        for inv in self.graph.all_invoices():
            u = self.graph.resolve(inv.seller_id)
            v = self.graph.resolve(inv.buyer_id)
            for cand in discover_on_edge(self.graph, u, v):
                self._candidates[(cand.canonical_key, cand.bridges)] = cand

        return self._rescore_all(touching_party=canon_id)

    def rescore_party(self, party_id: str) -> list[ScoredRing]:
        """Re-score all rings touching party_id after relationship or outside-volume changes."""
        canon_id = self.graph.resolve(party_id)
        return self._rescore_all(touching_party=canon_id)

    def _rescore_all(self, touching_party: str | None = None) -> list[ScoredRing]:
        pure_member_sets = [
            frozenset(c.members) for c in self._candidates.values() if not c.bridges
        ]
        filtered = [
            c
            for c in self._candidates.values()
            if not c.bridges or not any(frozenset(c.members).issubset(ps) for ps in pure_member_sets)
        ]
        ordered_cands = assign_ring_ids(filtered)
        self._candidates = {(c.canonical_key, c.bridges): c for c in ordered_cands}
        scored: dict[str, ScoredRing] = {}
        for cand in ordered_cands:
            scored[cand.ring_id] = score_ring(cand, self.graph)
        self._scored_rings = scored
        return self.rings()

    def rings(self) -> list[ScoredRing]:
        """Return all discovered ScoredRings sorted by canonical ring_id (R01, R02, ...)."""
        return [self._scored_rings[rid] for rid in sorted(self._scored_rings.keys())]

    def ranked_rings(self) -> list[ScoredRing]:
        """Return all discovered ScoredRings sorted by review_priority (tier_rank, ring_exposure_paise)."""
        return rank_rings(self._scored_rings.values())


IncrementalNetworkEngine = IncrementalRingEngine
IncrementalRingDetector = IncrementalRingEngine

"""Depth-limited cycle and corporate-bridge ring discovery with explicit budgets."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Mapping, Sequence

from network.graph import TradeGraph
from network.scc import nontrivial_sccs

DEFAULT_MAX_HOPS = 7
DEFAULT_MAX_CYCLES = 50_000
DEFAULT_MAX_STEPS = 2_000_000


class BudgetExceeded(RuntimeError):
    """Raised loudly when cycle enumeration exceeds max_cycles or max_steps budget."""


def canonical_cycle_key(nodes: Sequence[str]) -> tuple[str, ...]:
    """Return the lexicographically smallest rotation of a cycle node sequence, preserving direction."""
    seq = tuple(nodes)
    n = len(seq)
    if n <= 1:
        return seq
    rotations = [seq[i:] + seq[:i] for i in range(n)]
    return min(rotations)


@dataclass(frozen=True)
class RingCandidate:
    """Topological representation of a transaction-closed or corporate-bridge-closed ring."""

    ring_id: str
    canonical_key: tuple[str, ...]
    members: tuple[str, ...]
    invoice_hops: tuple[tuple[str, str], ...]
    bridges: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def is_bridge_closed(self) -> bool:
        return len(self.bridges) > 0

    @classmethod
    def from_cycle(cls, nodes: Sequence[str], ring_id: str = "R01") -> RingCandidate:
        """Build a pure transaction-closed RingCandidate from a sequence of cycle nodes."""
        canon = canonical_cycle_key(nodes)
        k = len(canon)
        hops = tuple((canon[i], canon[(i + 1) % k]) for i in range(k))
        return cls(
            ring_id=ring_id,
            canonical_key=canon,
            members=canon,
            invoice_hops=hops,
            bridges=(),
        )

    @classmethod
    def from_bridge_path(
        cls,
        path_nodes: Sequence[str],
        ring_id: str = "R01",
    ) -> RingCandidate:
        """Build a corporate-bridge-closed RingCandidate from directed invoice path [p_0, ..., p_{k-1}]
        where (p_{k-1}, p_0) is closed by a >=2-link corporate bridge.
        """
        seq = tuple(path_nodes)
        canon = canonical_cycle_key(seq)
        k = len(seq)
        hops = tuple((seq[i], seq[i + 1]) for i in range(k - 1))
        bridge = ((seq[-1], seq[0]),)
        return cls(
            ring_id=ring_id,
            canonical_key=canon,
            members=seq,
            invoice_hops=hops,
            bridges=bridge,
        )


def assign_ring_ids(candidates: Iterable[RingCandidate]) -> list[RingCandidate]:
    """Deduplicate and assign R01, R02, ... in deterministic sorted canonical-key order."""
    by_unique_key: dict[tuple[tuple[str, ...], tuple[tuple[str, str], ...]], RingCandidate] = {}
    for cand in candidates:
        ukey = (cand.canonical_key, cand.bridges)
        if ukey not in by_unique_key:
            by_unique_key[ukey] = cand

    sorted_keys = sorted(by_unique_key.keys(), key=lambda k: (k[0], k[1]))
    out: list[RingCandidate] = []
    for idx, ukey in enumerate(sorted_keys, start=1):
        cand = by_unique_key[ukey]
        out.append(replace(cand, ring_id=f"R{idx:02d}"))
    return out


def find_cycles(
    adj_or_graph: Mapping[str, Iterable[str]] | TradeGraph,
    *,
    max_hops: int = DEFAULT_MAX_HOPS,
    max_cycles: int = DEFAULT_MAX_CYCLES,
    max_steps: int = DEFAULT_MAX_STEPS,
    include_corporate_bridges: bool = True,
) -> list[RingCandidate]:
    """Discover elementary directed cycles (<= max_hops) and optional corporate-bridge rings.

    Uses iterative Tarjan SCC decomposition followed by depth-limited DFS from each cycle's
    lexicographically smallest node. Raises BudgetExceeded loudly if max_cycles or max_steps
    is breached.
    """
    if max_hops < 2:
        return []

    if isinstance(adj_or_graph, TradeGraph):
        adj_map = adj_or_graph.adj_map()
        graph_obj: TradeGraph | None = adj_or_graph
    else:
        all_nodes: set[str] = set(adj_or_graph.keys())
        for nbrs in adj_or_graph.values():
            all_nodes.update(nbrs)
        adj_map = {u: sorted(set(adj_or_graph.get(u, ()))) for u in sorted(all_nodes)}
        graph_obj = None

    sccs = nontrivial_sccs(adj_map)
    discovered: dict[tuple[tuple[str, ...], tuple[tuple[str, str], ...]], RingCandidate] = {}

    for scc in sccs:
        scc_nodes = sorted(scc)
        comp_steps = 0

        for idx, start in enumerate(scc_nodes):
            allowed = set(scc_nodes[idx:])
            sub_adj: dict[str, list[str]] = {
                u: [v for v in adj_map.get(u, []) if v in allowed and v != u]
                for u in scc_nodes[idx:]
            }
            if not sub_adj.get(start):
                continue

            # Explicit iterative DFS stack: [current_node, neighbor_index]
            path: list[str] = [start]
            in_path: set[str] = {start}
            stack: list[list[ int | str ]] = [[start, 0]]

            while stack:
                frame = stack[-1]
                u = str(frame[0])
                nbr_idx = int(frame[1])
                nbrs = sub_adj.get(u, [])

                if nbr_idx < len(nbrs):
                    v = nbrs[nbr_idx]
                    frame[1] = nbr_idx + 1
                    comp_steps += 1
                    if comp_steps > max_steps:
                        raise BudgetExceeded(
                            f"Cycle search exceeded max_steps={max_steps} in component starting at {scc_nodes[0]}."
                        )

                    if v == start:
                        if len(path) >= 2:
                            cand = RingCandidate.from_cycle(path)
                            ukey = (cand.canonical_key, cand.bridges)
                            if ukey not in discovered:
                                discovered[ukey] = cand
                                if len(discovered) > max_cycles:
                                    raise BudgetExceeded(
                                        f"Cycle search exceeded max_cycles={max_cycles} (found {len(discovered)})."
                                    )
                    elif v not in in_path and len(path) < max_hops:
                        path.append(v)
                        in_path.add(v)
                        stack.append([v, 0])
                else:
                    stack.pop()
                    popped = path.pop()
                    in_path.remove(popped)

    # Also find corporate-bridge-closed rings if a TradeGraph with corporate bridges is provided
    if include_corporate_bridges and graph_obj is not None:
        bridges = graph_obj.corporate_bridges()
        if bridges:
            bridge_steps = 0
            pure_cycle_member_sets = [frozenset(k[0]) for k in discovered.keys()]
            # For every ordered pair (entry_firm, exit_firm) linked by a >=2-link corporate bridge,
            # find simple directed invoice paths entry_firm ~> exit_firm of 2..max_hops invoice edges
            for u_bridge, v_bridge, _links in bridges:
                for entry_firm, exit_firm in ((u_bridge, v_bridge), (v_bridge, u_bridge)):
                    path = [entry_firm]
                    in_path = {entry_firm}
                    stack = [[entry_firm, 0]]

                    while stack:
                        frame = stack[-1]
                        curr = str(frame[0])
                        nbr_idx = int(frame[1])
                        nbrs = adj_map.get(curr, [])

                        if nbr_idx < len(nbrs):
                            nxt = nbrs[nbr_idx]
                            frame[1] = nbr_idx + 1
                            bridge_steps += 1
                            if bridge_steps > max_steps:
                                raise BudgetExceeded(
                                    f"Corporate bridge search exceeded max_steps={max_steps}."
                                )

                            if nxt == exit_firm:
                                # Require at least 2 invoice hops (entry -> mid -> exit) so an interior firm exists
                                if len(path) >= 2 and (len(path) + 1) <= max(max_hops, 3):
                                    full_path = tuple(path + [exit_firm])
                                    cand = RingCandidate.from_bridge_path(full_path)
                                    cand_set = frozenset(cand.members)
                                    if not any(cand_set.issubset(ps) for ps in pure_cycle_member_sets):
                                        ukey = (cand.canonical_key, cand.bridges)
                                        if ukey not in discovered:
                                            discovered[ukey] = cand
                                            if len(discovered) > max_cycles:
                                                raise BudgetExceeded(
                                                    f"Corporate bridge search exceeded max_cycles={max_cycles}."
                                                )
                            elif nxt not in in_path and len(path) < max_hops:
                                path.append(nxt)
                                in_path.add(nxt)
                                stack.append([nxt, 0])
                        else:
                            stack.pop()
                            popped = path.pop()
                            in_path.remove(popped)

    return assign_ring_ids(discovered.values())

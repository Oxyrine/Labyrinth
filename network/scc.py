"""Iterative Tarjan's strongly connected components (SCC) algorithm."""

from __future__ import annotations

from typing import Iterable, Mapping

from network.graph import TradeGraph


def tarjan_scc(adj: Mapping[str, Iterable[str]] | TradeGraph) -> list[tuple[str, ...]]:
    """Compute strongly connected components iteratively without recursion limits.

    Returns SCCs as sorted tuples of node IDs, ordered deterministically by each SCC's smallest node.
    """
    if isinstance(adj, TradeGraph):
        adj_map: dict[str, list[str]] = adj.adj_map()
    else:
        all_nodes: set[str] = set(adj.keys())
        for nbrs in adj.values():
            all_nodes.update(nbrs)
        adj_map = {u: sorted(set(adj.get(u, ()))) for u in sorted(all_nodes)}

    index_counter = 0
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    tarjan_stack: list[str] = []
    sccs: list[tuple[str, ...]] = []

    for root in sorted(adj_map.keys()):
        if root in indices:
            continue

        # Explicit DFS call stack: list of [node, neighbor_idx]
        indices[root] = index_counter
        lowlink[root] = index_counter
        index_counter += 1
        tarjan_stack.append(root)
        on_stack.add(root)
        call_stack: list[list[object]] = [[root, 0]]

        while call_stack:
            frame = call_stack[-1]
            u = str(frame[0])
            nbr_idx = int(frame[1])
            nbrs = adj_map.get(u, [])

            if nbr_idx < len(nbrs):
                v = nbrs[nbr_idx]
                frame[1] = nbr_idx + 1
                if v not in indices:
                    indices[v] = index_counter
                    lowlink[v] = index_counter
                    index_counter += 1
                    tarjan_stack.append(v)
                    on_stack.add(v)
                    call_stack.append([v, 0])
                elif v in on_stack:
                    if indices[v] < lowlink[u]:
                        lowlink[u] = indices[v]
            else:
                call_stack.pop()
                if call_stack:
                    parent = str(call_stack[-1][0])
                    if lowlink[u] < lowlink[parent]:
                        lowlink[parent] = lowlink[u]

                if lowlink[u] == indices[u]:
                    comp: list[str] = []
                    while True:
                        w = tarjan_stack.pop()
                        on_stack.remove(w)
                        comp.append(w)
                        if w == u:
                            break
                    sccs.append(tuple(sorted(comp)))

    sccs.sort(key=lambda comp: comp[0] if comp else "")
    return sccs


def nontrivial_sccs(adj: Mapping[str, Iterable[str]] | TradeGraph) -> list[tuple[str, ...]]:
    """Return only strongly connected components with >= 2 nodes."""
    return [comp for comp in tarjan_scc(adj) if len(comp) >= 2]

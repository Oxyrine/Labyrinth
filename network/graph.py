"""Directed multigraph of invoices and >=2-link corporate relationship bridges."""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping

from contract.models import DocType, EvidenceDoc, IndustryClass, Invoice, Party
from network.canonicalize import (
    CanonicalParty,
    canonicalize_parties,
    extract_pan_from_gstin,
    normalize_party_address,
    normalize_party_name,
    normalize_phone,
)

RELATED_LINK_TYPES: tuple[str, ...] = (
    "shared_director",
    "same_pan",
    "same_address",
    "same_phone",
    "same_bank_hash",
)


def _is_valid_iso_date(val: Any) -> bool:
    if not isinstance(val, str) or len(val) != 10:
        return False
    try:
        date.fromisoformat(val)
        return True
    except ValueError:
        return False


def compute_pair_related_links(
    p1: CanonicalParty,
    p2: CanonicalParty,
) -> dict[str, tuple[str, ...]]:
    """Compute independent related-party link categories between two distinct canonical parties.

    Categories:
      - shared_director
      - same_pan
      - same_address
      - same_phone
      - same_bank_hash
    """
    if p1.party_id == p2.party_id:
        return {}

    links: dict[str, tuple[str, ...]] = {}

    shared_dirs = sorted(set(p1.normalized_directors) & set(p2.normalized_directors))
    if shared_dirs:
        # Map back to display names from p1/p2 where possible
        dir_display_map: dict[str, str] = {}
        for d in p1.directors + p2.directors:
            k = " ".join(d.strip().lower().split())
            if k not in dir_display_map:
                dir_display_map[k] = d.strip()
        links["shared_director"] = tuple(dir_display_map.get(k, k) for k in shared_dirs)

    shared_pans = sorted(set(p1.pans) & set(p2.pans))
    if shared_pans:
        links["same_pan"] = tuple(shared_pans)

    shared_addrs = sorted(set(p1.normalized_addresses) & set(p2.normalized_addresses))
    if shared_addrs:
        links["same_address"] = tuple(shared_addrs)

    shared_phones = sorted(set(p1.normalized_phones) & set(p2.normalized_phones))
    if shared_phones:
        links["same_phone"] = tuple(shared_phones)

    shared_banks = sorted(set(p1.bank_account_hashes) & set(p2.bank_account_hashes))
    if shared_banks:
        links["same_bank_hash"] = tuple(shared_banks)

    return links


class TradeGraph:
    """Directed multigraph of invoices plus undirected corporate bridges (>= 2 independent links)."""

    def __init__(
        self,
        parties: Iterable[Party] | Mapping[str, Party] | None = None,
        invoices: Iterable[Any] | None = None,
        evidence_docs: Iterable[EvidenceDoc] | None = None,
    ) -> None:
        self._raw_parties: list[Party] = []
        if parties is not None:
            if isinstance(parties, Mapping):
                self._raw_parties = list(parties.values())
            else:
                self._raw_parties = list(parties)

        canon = canonicalize_parties(self._raw_parties)
        self.parties: dict[str, CanonicalParty] = dict(canon.parties)
        self.party_models: dict[str, Party] = dict(canon.party_models)
        self.alias_map: dict[str, str] = dict(canon.alias_map)
        self.merges_count: int = canon.merges_count

        self.self_loops_dropped: int = 0
        self.malformed_dropped: int = 0

        self._edge_invoices: dict[tuple[str, str], dict[str, Invoice]] = {}
        self._out_neighbors: dict[str, set[str]] = {pid: set() for pid in self.parties}
        self._in_neighbors: dict[str, set[str]] = {pid: set() for pid in self.parties}
        self._invoices_by_id: dict[str, Invoice] = {}
        self._invoices_by_party: dict[str, set[str]] = {pid: set() for pid in self.parties}

        # Corporate link caches
        self._related_links_cache: dict[tuple[str, str], dict[str, tuple[str, ...]]] = {}
        self._corporate_bridges: dict[tuple[str, str], dict[str, tuple[str, ...]]] = {}
        self._corporate_neighbors: dict[str, set[str]] = {pid: set() for pid in self.parties}
        self._world_attr_firms: dict[tuple[str, str], set[str]] = {}

        # Evidence docs
        self._evidence_by_id: dict[str, EvidenceDoc] = {}
        self._evidence_by_invoice: dict[str, list[EvidenceDoc]] = {}
        self._evidence_by_pair: dict[tuple[str, str], list[EvidenceDoc]] = {}

        self._rebuild_corporate_relationships()

        if invoices is not None:
            for inv in invoices:
                self.add_invoice(inv)

        if evidence_docs is not None:
            for doc in evidence_docs:
                self.add_evidence(doc)

    @property
    def self_loop_count(self) -> int:
        return self.self_loops_dropped

    @property
    def malformed_count(self) -> int:
        return self.malformed_dropped

    def resolve(self, party_id: str) -> str:
        """Resolve an original party_id to its canonical party_id."""
        return self.alias_map.get(party_id, party_id)

    def _ensure_stub_party(self, party_id: str, gstin: str = "") -> str:
        """Ensure an invoice endpoint has a CanonicalParty entry even if omitted from initial parties."""
        canon_id = self.resolve(party_id)
        if canon_id in self.parties:
            return canon_id

        clean_gstin = (gstin or "").strip().upper()
        # Check if any existing canonical party already owns this exact GSTIN
        if clean_gstin:
            for existing_id in sorted(self.parties.keys()):
                if clean_gstin in self.parties[existing_id].gstins:
                    self.alias_map[party_id] = existing_id
                    return existing_id

        pan = extract_pan_from_gstin(clean_gstin) or "AAAAA0000A"
        state_code = clean_gstin[:2] if len(clean_gstin) >= 2 and clean_gstin[:2].isdigit() else "27"
        stub_model = Party(
            party_id=party_id,
            name=party_id,
            gstin=clean_gstin or f"{state_code}{pan}1Z5",
            pan=pan,
            state_code=state_code,
            industry_class=IndustryClass.TRADING,
            address="",
            pincode="400001",
            city="Mumbai",
            phone="",
            bank_account_hash="",
            directors=[],
        )
        cp = CanonicalParty(
            party_id=party_id,
            party=stub_model,
            original_party_ids=(party_id,),
            names=(party_id,),
            normalized_names=(normalize_party_name(party_id),),
            gstins=(clean_gstin,) if clean_gstin else (),
            pans=(pan,) if clean_gstin else (),
            addresses=(),
            normalized_addresses=(),
            phones=(),
            normalized_phones=(),
            bank_account_hashes=(),
            directors=(),
            normalized_directors=(),
            pincodes=(),
            cities=(),
        )
        self.parties[party_id] = cp
        self.party_models[party_id] = stub_model
        self.alias_map[party_id] = party_id
        self._out_neighbors.setdefault(party_id, set())
        self._in_neighbors.setdefault(party_id, set())
        self._invoices_by_party.setdefault(party_id, set())
        self._corporate_neighbors.setdefault(party_id, set())
        self._rebuild_corporate_relationships()
        return party_id

    def _rebuild_corporate_relationships(self) -> None:
        self._related_links_cache.clear()
        self._corporate_bridges.clear()
        self._world_attr_firms.clear()
        for pid in self.parties:
            self._corporate_neighbors[pid] = set()

        pids = sorted(self.parties.keys())
        for pid in pids:
            cp = self.parties[pid]
            for d in cp.normalized_directors:
                self._world_attr_firms.setdefault(("shared_director", d), set()).add(pid)
            for p in cp.pans:
                self._world_attr_firms.setdefault(("same_pan", p), set()).add(pid)
            for a in cp.normalized_addresses:
                self._world_attr_firms.setdefault(("same_address", a), set()).add(pid)
            for ph in cp.normalized_phones:
                self._world_attr_firms.setdefault(("same_phone", ph), set()).add(pid)
            for bh in cp.bank_account_hashes:
                self._world_attr_firms.setdefault(("same_bank_hash", bh), set()).add(pid)

        n = len(pids)
        for i in range(n):
            u = pids[i]
            cu = self.parties[u]
            for j in range(i + 1, n):
                v = pids[j]
                cv = self.parties[v]
                links = compute_pair_related_links(cu, cv)
                if links:
                    self._related_links_cache[(u, v)] = links
                    self._related_links_cache[(v, u)] = links
                    # Lesson 4: ONLY >= 2 independent related links create a Corporate Bridge
                    if len(links) >= 2:
                        self._corporate_bridges[(u, v)] = links
                        self._corporate_bridges[(v, u)] = links
                        self._corporate_neighbors[u].add(v)
                        self._corporate_neighbors[v].add(u)

    def add_or_update_party(self, party: Party) -> str:
        """Add or update a party in the graph and recompute canonicalization and corporate bridges."""
        self._raw_parties = [p for p in self._raw_parties if p.party_id != party.party_id]
        self._raw_parties.append(party)
        canon = canonicalize_parties(self._raw_parties)
        self.parties.update(canon.parties)
        self.party_models.update(canon.party_models)
        self.alias_map.update(canon.alias_map)
        self.merges_count = canon.merges_count
        for pid in self.parties:
            self._out_neighbors.setdefault(pid, set())
            self._in_neighbors.setdefault(pid, set())
            self._invoices_by_party.setdefault(pid, set())
            self._corporate_neighbors.setdefault(pid, set())
        self._rebuild_corporate_relationships()
        return self.resolve(party.party_id)

    def add_invoice(self, invoice: Any) -> bool:
        """Validate and add a directed invoice edge u -> v to the multigraph.

        Drops and counts malformed records and self-loops (u == v).
        Returns True if added, False if dropped.
        """
        if invoice is None:
            self.malformed_dropped += 1
            return False

        if isinstance(invoice, dict):
            try:
                invoice = Invoice.model_validate(invoice)
            except Exception:
                self.malformed_dropped += 1
                return False

        if not isinstance(invoice, Invoice):
            self.malformed_dropped += 1
            return False

        # Ignore credit/debit notes gracefully (not trade edges, not malformed unless invalid fields)
        if invoice.doc_type != DocType.INVOICE:
            return False

        if (
            not invoice.invoice_id
            or not invoice.seller_id
            or not invoice.buyer_id
            or not invoice.seller_id.strip()
            or not invoice.buyer_id.strip()
            or invoice.grand_total_paise <= 0
            or invoice.subtotal_paise < 0
            or not invoice.line_items
            or not _is_valid_iso_date(invoice.invoice_date)
        ):
            self.malformed_dropped += 1
            return False

        u = self._ensure_stub_party(invoice.seller_id, invoice.seller_gstin)
        v = self._ensure_stub_party(invoice.buyer_id, invoice.buyer_gstin)

        if u == v:
            self.self_loops_dropped += 1
            return False

        edge_bucket = self._edge_invoices.setdefault((u, v), {})
        edge_bucket[invoice.invoice_id] = invoice
        self._out_neighbors.setdefault(u, set()).add(v)
        self._in_neighbors.setdefault(v, set()).add(u)
        self._invoices_by_id[invoice.invoice_id] = invoice
        self._invoices_by_party.setdefault(u, set()).add(invoice.invoice_id)
        self._invoices_by_party.setdefault(v, set()).add(invoice.invoice_id)
        return True

    def add_evidence(self, doc: EvidenceDoc) -> None:
        """Index an EvidenceDoc (e.g. EWAY_BILL, GRN) by doc_id, invoice_id, and canonical (seller, buyer)."""
        if doc is None or not isinstance(doc, EvidenceDoc):
            return
        prev = self._evidence_by_id.get(doc.doc_id)
        self._evidence_by_id[doc.doc_id] = doc
        if doc.invoice_id:
            bucket = self._evidence_by_invoice.setdefault(doc.invoice_id, [])
            if prev is not None:
                bucket[:] = [d for d in bucket if d.doc_id != doc.doc_id]
            bucket.append(doc)
            bucket.sort(key=lambda d: (d.doc_date, d.doc_id))
        u = self.resolve(doc.seller_id)
        v = self.resolve(doc.buyer_id)
        pair_bucket = self._evidence_by_pair.setdefault((u, v), [])
        if prev is not None:
            pair_bucket[:] = [d for d in pair_bucket if d.doc_id != doc.doc_id]
        pair_bucket.append(doc)
        pair_bucket.sort(key=lambda d: (d.doc_date, d.doc_id))

    def invoices_on_edge(self, u: str, v: str) -> list[Invoice]:
        """Return all invoices on directed edge (u, v) sorted deterministically by (invoice_date, invoice_id)."""
        cu, cv = self.resolve(u), self.resolve(v)
        bucket = self._edge_invoices.get((cu, cv))
        if not bucket:
            return []
        return sorted(bucket.values(), key=lambda inv: (inv.invoice_date, inv.invoice_id))

    def get_edge_invoices(self, u: str, v: str) -> list[Invoice]:
        return self.invoices_on_edge(u, v)

    def edge_total_paise(self, u: str, v: str) -> int:
        """Sum of grand_total_paise across all invoices on directed edge (u, v)."""
        return sum(inv.grand_total_paise for inv in self.invoices_on_edge(u, v))

    def all_invoices(self) -> list[Invoice]:
        return sorted(self._invoices_by_id.values(), key=lambda inv: (inv.invoice_date, inv.invoice_id))

    def invoices_touching_party(self, party_id: str) -> list[Invoice]:
        cid = self.resolve(party_id)
        inv_ids = self._invoices_by_party.get(cid, set())
        return sorted(
            (self._invoices_by_id[iid] for iid in inv_ids if iid in self._invoices_by_id),
            key=lambda inv: (inv.invoice_date, inv.invoice_id),
        )

    def invoices_touching_parties(self, party_ids: Iterable[str]) -> list[Invoice]:
        seen: set[str] = set()
        for pid in party_ids:
            cid = self.resolve(pid)
            seen.update(self._invoices_by_party.get(cid, set()))
        return sorted(
            (self._invoices_by_id[iid] for iid in seen if iid in self._invoices_by_id),
            key=lambda inv: (inv.invoice_date, inv.invoice_id),
        )

    def out_neighbors(self, u: str) -> list[str]:
        cu = self.resolve(u)
        return sorted(self._out_neighbors.get(cu, set()))

    def in_neighbors(self, v: str) -> list[str]:
        cv = self.resolve(v)
        return sorted(self._in_neighbors.get(cv, set()))

    def nodes(self) -> list[str]:
        all_nodes = set(self.parties.keys()) | set(self._out_neighbors.keys()) | set(self._in_neighbors.keys())
        return sorted(all_nodes)

    def adj_map(self) -> dict[str, list[str]]:
        return {u: self.out_neighbors(u) for u in self.nodes()}

    def rev_adj_map(self) -> dict[str, list[str]]:
        return {v: self.in_neighbors(v) for v in self.nodes()}

    def related_links(self, x: str, y: str) -> dict[str, tuple[str, ...]]:
        cx, cy = self.resolve(x), self.resolve(y)
        return dict(self._related_links_cache.get((cx, cy), {}))

    def is_corporate_bridge(self, x: str, y: str) -> bool:
        """Return True iff x and y share >= 2 independent related links (Lesson 4)."""
        cx, cy = self.resolve(x), self.resolve(y)
        return (cx, cy) in self._corporate_bridges

    def corporate_neighbors(self, x: str) -> list[str]:
        cx = self.resolve(x)
        return sorted(self._corporate_neighbors.get(cx, set()))

    def corporate_bridges(self) -> list[tuple[str, str, dict[str, tuple[str, ...]]]]:
        out: list[tuple[str, str, dict[str, tuple[str, ...]]]] = []
        for (u, v), links in sorted(self._corporate_bridges.items()):
            if u < v:
                out.append((u, v, dict(links)))
        return out

    def world_sharing_count(self, category: str, value: str) -> int:
        """Number of canonical firms in the graph sharing a given normalized (category, value)."""
        if category == "shared_director":
            norm_val = " ".join(value.strip().lower().split())
        elif category == "same_address":
            norm_val = normalize_party_address(value)
        elif category == "same_phone":
            norm_val = normalize_phone(value)
        elif category == "same_pan":
            norm_val = value.strip().upper()
        else:
            norm_val = value.strip()
        firms = self._world_attr_firms.get((category, norm_val), set())
        return max(len(firms), 2)

    def evidence_for_invoice(self, invoice_id: str) -> list[EvidenceDoc]:
        return list(self._evidence_by_invoice.get(invoice_id, []))

    def evidence_for_pair(self, u: str, v: str) -> list[EvidenceDoc]:
        cu, cv = self.resolve(u), self.resolve(v)
        return list(self._evidence_by_pair.get((cu, cv), []))

    def all_evidence(self) -> list[EvidenceDoc]:
        return sorted(self._evidence_by_id.values(), key=lambda d: (d.doc_date, d.doc_id))

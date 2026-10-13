"""Deterministic party canonicalization (exact GSTIN or normalized name+address match)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from contract.models import Party

_PUNCT_RE = re.compile(r"[^a-z0-9\s]+")
_SPACE_RE = re.compile(r"\s+")

_NAME_TOKEN_MAP: dict[str, str] = {
    "pvt": "private",
    "ltd": "limited",
    "co": "company",
    "rd": "road",
    "indl": "industrial",
}

_ADDR_TOKEN_MAP: dict[str, str] = {
    "rd": "road",
    "indl": "industrial",
    "pvt": "private",
    "ltd": "limited",
    "co": "company",
}


def _normalize_tokens(text: str, token_map: Mapping[str, str]) -> str:
    if not text:
        return ""
    cleaned = _PUNCT_RE.sub(" ", text.strip().lower())
    raw_tokens = _SPACE_RE.split(cleaned.strip())
    expanded: list[str] = []
    for tok in raw_tokens:
        if not tok:
            continue
        expanded.append(token_map.get(tok, tok))
    return " ".join(expanded)


def normalize_party_name(name: str) -> str:
    """Normalize party name: lowercase, strip punctuation, expand corporate suffixes, collapse spaces."""
    return _normalize_tokens(name, _NAME_TOKEN_MAP)


def normalize_party_address(address: str) -> str:
    """Normalize party address: lowercase, strip punctuation, expand street/industrial suffixes, collapse spaces."""
    return _normalize_tokens(address, _ADDR_TOKEN_MAP)


def normalize_phone(phone: str) -> str:
    """Normalize phone string by keeping digits (or lowercase stripped token if no digits)."""
    if not phone:
        return ""
    digits = re.sub(r"\D+", "", phone)
    if len(digits) >= 10:
        return digits[-10:]
    if digits:
        return digits
    return phone.strip().lower()


def extract_pan_from_gstin(gstin: str) -> str:
    """Extract 10-character PAN embedded at characters [2:12] of a valid-length GSTIN."""
    s = (gstin or "").strip().upper()
    if len(s) >= 12:
        return s[2:12]
    return ""


@dataclass(frozen=True)
class CanonicalParty:
    """Canonical party node preserving all merged attributes and original IDs."""

    party_id: str
    party: Party
    original_party_ids: tuple[str, ...] = field(default_factory=tuple)
    names: tuple[str, ...] = field(default_factory=tuple)
    normalized_names: tuple[str, ...] = field(default_factory=tuple)
    gstins: tuple[str, ...] = field(default_factory=tuple)
    pans: tuple[str, ...] = field(default_factory=tuple)
    addresses: tuple[str, ...] = field(default_factory=tuple)
    normalized_addresses: tuple[str, ...] = field(default_factory=tuple)
    phones: tuple[str, ...] = field(default_factory=tuple)
    normalized_phones: tuple[str, ...] = field(default_factory=tuple)
    bank_account_hashes: tuple[str, ...] = field(default_factory=tuple)
    directors: tuple[str, ...] = field(default_factory=tuple)
    normalized_directors: tuple[str, ...] = field(default_factory=tuple)
    pincodes: tuple[str, ...] = field(default_factory=tuple)
    cities: tuple[str, ...] = field(default_factory=tuple)

    @property
    def name(self) -> str:
        return self.party.name

    @property
    def gstin(self) -> str:
        return self.party.gstin

    @property
    def pan(self) -> str:
        return self.party.pan

    @property
    def industry_class(self):
        return self.party.industry_class

    @property
    def address(self) -> str:
        return self.party.address

    @property
    def pincode(self) -> str:
        return self.party.pincode

    @property
    def city(self) -> str:
        return self.party.city

    @property
    def phone(self) -> str:
        return self.party.phone

    @property
    def bank_account_hash(self) -> str:
        return self.party.bank_account_hash


@dataclass(frozen=True)
class CanonicalizationResult:
    """Output of deterministic party canonicalization."""

    parties: dict[str, CanonicalParty]
    party_models: dict[str, Party]
    alias_map: dict[str, str]
    merges_count: int

    def resolve(self, party_id: str) -> str:
        return self.alias_map.get(party_id, party_id)


def canonicalize_parties(
    parties: Iterable[Party] | Mapping[str, Party],
) -> CanonicalizationResult:
    """Deterministically merge parties sharing exact GSTIN or exact normalized (name, address).

    Never uses fuzzy string matching. Unions directors and preserves all GSTINs, PANs,
    bank hashes, phones, and addresses on the canonical party node.
    """
    if isinstance(parties, Mapping):
        party_list = list(parties.values())
    else:
        party_list = list(parties)

    # Deduplicate exact identical party_id inputs deterministically while sorting by party_id
    by_id: dict[str, list[Party]] = {}
    for p in party_list:
        by_id.setdefault(p.party_id, []).append(p)

    ordered_ids = sorted(by_id.keys())
    if not ordered_ids:
        return CanonicalizationResult(
            parties={},
            party_models={},
            alias_map={},
            merges_count=0,
        )

    parent: dict[str, str] = {pid: pid for pid in ordered_ids}

    def find(x: str) -> str:
        root = x
        while parent[root] != root:
            root = parent[root]
        curr = x
        while curr != root:
            nxt = parent[curr]
            parent[curr] = root
            curr = nxt
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        # Always keep lexicographically smaller party_id as root
        if ra < rb:
            parent[rb] = ra
        else:
            parent[ra] = rb

    gstin_owner: dict[str, str] = {}
    name_addr_owner: dict[tuple[str, str], str] = {}

    for pid in ordered_ids:
        for p in by_id[pid]:
            gstin_norm = (p.gstin or "").strip().upper()
            if gstin_norm:
                if gstin_norm in gstin_owner:
                    union(pid, gstin_owner[gstin_norm])
                else:
                    gstin_owner[gstin_norm] = pid

            norm_name = normalize_party_name(p.name)
            norm_addr = normalize_party_address(p.address)
            if norm_name and norm_addr:
                key = (norm_name, norm_addr)
                if key in name_addr_owner:
                    union(pid, name_addr_owner[key])
                else:
                    name_addr_owner[key] = pid

    groups: dict[str, list[str]] = {}
    alias_map: dict[str, str] = {}
    for pid in ordered_ids:
        canon_id = find(pid)
        alias_map[pid] = canon_id
        groups.setdefault(canon_id, []).append(pid)

    canonical_parties: dict[str, CanonicalParty] = {}
    party_models: dict[str, Party] = {}

    for canon_id in sorted(groups.keys()):
        member_ids = sorted(groups[canon_id])
        member_records: list[Party] = []
        for mid in member_ids:
            member_records.extend(by_id[mid])

        primary = member_records[0]

        names: set[str] = set()
        norm_names: set[str] = set()
        gstins: set[str] = set()
        pans: set[str] = set()
        addresses: set[str] = set()
        norm_addresses: set[str] = set()
        phones: set[str] = set()
        norm_phones: set[str] = set()
        bank_hashes: set[str] = set()
        directors_by_norm: dict[str, str] = {}
        pincodes: set[str] = set()
        cities: set[str] = set()

        for rec in member_records:
            if rec.name and rec.name.strip():
                names.add(rec.name.strip())
                nn = normalize_party_name(rec.name)
                if nn:
                    norm_names.add(nn)
            if rec.gstin and rec.gstin.strip():
                g = rec.gstin.strip().upper()
                gstins.add(g)
                g_pan = extract_pan_from_gstin(g)
                if g_pan:
                    pans.add(g_pan)
            if rec.pan and rec.pan.strip():
                pans.add(rec.pan.strip().upper())
            if rec.address and rec.address.strip():
                addresses.add(rec.address.strip())
                na = normalize_party_address(rec.address)
                if na:
                    norm_addresses.add(na)
            if rec.phone and rec.phone.strip():
                phones.add(rec.phone.strip())
                np_val = normalize_phone(rec.phone)
                if np_val:
                    norm_phones.add(np_val)
            if rec.bank_account_hash and rec.bank_account_hash.strip():
                bank_hashes.add(rec.bank_account_hash.strip())
            if rec.pincode and rec.pincode.strip():
                pincodes.add(rec.pincode.strip())
            if rec.city and rec.city.strip():
                cities.add(rec.city.strip())
            for d in rec.directors:
                d_clean = d.strip()
                if d_clean:
                    d_norm = _SPACE_RE.sub(" ", d_clean.lower())
                    if d_norm not in directors_by_norm:
                        directors_by_norm[d_norm] = d_clean

        sorted_norm_dirs = tuple(sorted(directors_by_norm.keys()))
        sorted_display_dirs = tuple(directors_by_norm[k] for k in sorted_norm_dirs)

        merged_party_model = primary.model_copy(
            update={
                "party_id": canon_id,
                "directors": list(sorted_display_dirs),
            }
        )
        party_models[canon_id] = merged_party_model
        canonical_parties[canon_id] = CanonicalParty(
            party_id=canon_id,
            party=merged_party_model,
            original_party_ids=tuple(member_ids),
            names=tuple(sorted(names)),
            normalized_names=tuple(sorted(norm_names)),
            gstins=tuple(sorted(gstins)),
            pans=tuple(sorted(pans)),
            addresses=tuple(sorted(addresses)),
            normalized_addresses=tuple(sorted(norm_addresses)),
            phones=tuple(sorted(phones)),
            normalized_phones=tuple(sorted(norm_phones)),
            bank_account_hashes=tuple(sorted(bank_hashes)),
            directors=sorted_display_dirs,
            normalized_directors=sorted_norm_dirs,
            pincodes=tuple(sorted(pincodes)),
            cities=tuple(sorted(cities)),
        )

    total_input_records = len(party_list)
    merges_count = max(0, total_input_records - len(canonical_parties))

    return CanonicalizationResult(
        parties=canonical_parties,
        party_models=party_models,
        alias_map=alias_map,
        merges_count=merges_count,
    )

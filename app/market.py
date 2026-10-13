"""The market map: every firm in the generated world, who invoiced whom, which firms are related, and which loops the
network check flagged. Built from pipeline outputs only (world, invoices, decisions); ground-truth labels are never read."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

from contract import format_inr

REGIME_DIR = Path(__file__).resolve().parent.parent / "data" / "out" / "regime_A"
SHARED_KINDS = (("pan", "same PAN"), ("bank_account_hash", "same bank account"), ("address", "same address"), ("phone", "same phone"))


def _flagged_rings(decisions_path: Path) -> list[dict[str, Any]]:
    seen: dict[tuple[str, ...], dict[str, Any]] = {}
    with decisions_path.open(encoding="utf-8") as fh:
        for line in fh:
            for check in json.loads(line).get("checks", []):
                if check.get("check") != "network.ring" or check.get("status") == "pass":
                    continue
                ring = (check.get("details") or {}).get("ring") or {}
                key = tuple(ring.get("canonical_key") or ())
                if key and key not in seen:
                    seen[key] = ring
    out = []
    for n, (_, ring) in enumerate(sorted(seen.items()), start=1):
        out.append({
            "ring_id": f"M{n:02d}",
            "members": ring.get("members") or list(ring["canonical_key"]),
            "tier": ring.get("tier", "high"),
            "score": ring.get("score"),
            "exposure": format_inr(int(ring.get("ring_exposure_paise", 0))),
            "retained_pct": {k: round(v, 1) for k, v in (ring.get("retained_pct_by_firm") or {}).items()},
            "signals": {k: round(v, 2) for k, v in (ring.get("signals") or {}).items() if v is not None},
            "evidence": [re.sub(r"Ring R\d+", f"Loop M{n:02d}", s) for s in (ring.get("evidence") or [])],
            "hops": [{"from": h["from"], "to": h["to"]} for h in ring.get("hops", [])],
        })
    return out


def build_market(root: Path = REGIME_DIR) -> dict[str, Any]:
    world_path, inv_path, dec_path = root / "world.json", root / "invoices.json", root / "decisions.jsonl"
    if not (world_path.is_file() and inv_path.is_file() and dec_path.is_file()):
        return {"available": False, "nodes": [], "trade_edges": [], "related_edges": [], "rings": [],
                "message": "Run `python run.py world` and `python run.py pipeline` to generate the market."}

    firms = {p["party_id"]: p for p in json.loads(world_path.read_text(encoding="utf-8"))["parties"] if p["party_id"].startswith("P-")}
    invoices = json.loads(inv_path.read_text(encoding="utf-8"))
    rings = _flagged_rings(dec_path)

    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    volume: dict[str, int] = defaultdict(int)
    count: dict[str, int] = defaultdict(int)
    for inv in invoices:
        a, b = inv.get("seller_id"), inv.get("buyer_id")
        if a not in firms or b not in firms or a == b:
            continue
        e = pairs.setdefault((a, b), {"from": a, "to": b, "count": 0, "total_paise": 0})
        e["count"] += 1
        e["total_paise"] += int(inv["grand_total_paise"])
        for pid in (a, b):
            volume[pid] += int(inv["grand_total_paise"])
            count[pid] += 1

    # Related firms: anything two firms share that unrelated businesses would not.
    related: dict[tuple[str, str], list[str]] = defaultdict(list)
    for attr, label in SHARED_KINDS:
        groups: dict[str, list[str]] = defaultdict(list)
        for pid, p in firms.items():
            if p.get(attr):
                groups[p[attr]].append(pid)
        for members in groups.values():
            if 2 <= len(members) <= 8:
                for a, b in combinations(sorted(members), 2):
                    related[(a, b)].append(label)
    groups = defaultdict(list)
    for pid, p in firms.items():
        for din in p.get("directors", []):
            groups[din].append(pid)
    for din, members in groups.items():
        if 2 <= len(members) <= 8:
            for a, b in combinations(sorted(members), 2):
                related[(a, b)].append(f"director {din}")

    ring_of: dict[str, list[str]] = defaultdict(list)
    for r in rings:
        for pid in r["members"]:
            ring_of[pid].append(r["ring_id"])

    nodes = [{
        "id": pid, "name": p["name"], "city": p["city"], "gstin": p["gstin"], "industry": p["industry_class"],
        "directors": len(p.get("directors", [])), "invoices": count[pid], "volume": format_inr(volume[pid]),
        "volume_paise": volume[pid], "rings": ring_of.get(pid, []),
    } for pid, p in sorted(firms.items())]
    return {
        "available": True,
        "source": f"Generated market · {len(firms)} firms · {len(invoices)} invoices · regime A",
        "nodes": nodes,
        "trade_edges": [{**e, "total": format_inr(e["total_paise"])} for e in pairs.values()],
        "related_edges": [{"from": a, "to": b, "links": sorted(set(v))} for (a, b), v in sorted(related.items())],
        "rings": rings,
    }

"""Physical goods movement & e-way bill plausibility signal (s_movement, Lesson 5)."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from contract.models import DocType, EvidenceDoc, derive_evidence_weight
from network.cities_lookup import resolve_pincode, road_distance_km
from network.cycles import RingCandidate
from network.graph import TradeGraph


def _doc_weight(doc: EvidenceDoc) -> float:
    derived = derive_evidence_weight(doc.verification_status)
    if doc.evidence_weight != derived:
        return derived
    return float(doc.evidence_weight)


def _parse_dt(ts: str | None, fallback_date: str | None = None) -> datetime | None:
    if ts and ts.strip():
        s = ts.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    if fallback_date and fallback_date.strip():
        try:
            d = date.fromisoformat(fallback_date.strip())
            return datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _norm_vehicle(vehicle_no: str | None) -> str:
    if not vehicle_no:
        return ""
    return re.sub(r"[^A-Z0-9]+", "", vehicle_no.strip().upper())


def _collect_ring_evidence(
    ring: RingCandidate,
    graph: TradeGraph,
) -> tuple[list[EvidenceDoc], dict[str, list[EvidenceDoc]]]:
    """Collect deduplicated EWAY_BILLs and GRNs associated with ring invoices or ring hops."""
    ewb_by_id: dict[str, EvidenceDoc] = {}
    grns_by_inv: dict[str, list[EvidenceDoc]] = {}

    ring_inv_ids: set[str] = set()
    for u, v in ring.invoice_hops:
        for inv in graph.invoices_on_edge(u, v):
            ring_inv_ids.add(inv.invoice_id)
            for doc in graph.evidence_for_invoice(inv.invoice_id):
                if doc.doc_type == DocType.EWAY_BILL:
                    ewb_by_id[doc.doc_id] = doc
                elif doc.doc_type == DocType.GRN:
                    grns_by_inv.setdefault(inv.invoice_id, []).append(doc)

        for doc in graph.evidence_for_pair(u, v):
            if doc.invoice_id and doc.invoice_id not in ring_inv_ids:
                continue
            if doc.doc_type == DocType.EWAY_BILL:
                ewb_by_id[doc.doc_id] = doc
            elif doc.doc_type == DocType.GRN and doc.invoice_id:
                bucket = grns_by_inv.setdefault(doc.invoice_id, [])
                if all(existing.doc_id != doc.doc_id for existing in bucket):
                    bucket.append(doc)

    ewbs = sorted(ewb_by_id.values(), key=lambda d: (d.generated_at or d.doc_date, d.doc_id))
    return ewbs, grns_by_inv


def _bill_pincodes(doc: EvidenceDoc, graph: TradeGraph) -> tuple[str | None, str | None]:
    from_pin = doc.from_pincode
    to_pin = doc.to_pincode
    if not from_pin:
        seller_cp = graph.parties.get(graph.resolve(doc.seller_id))
        if seller_cp and seller_cp.pincode:
            from_pin = seller_cp.pincode
    if not to_pin:
        buyer_cp = graph.parties.get(graph.resolve(doc.buyer_id))
        if buyer_cp and buyer_cp.pincode:
            to_pin = buyer_cp.pincode
    return from_pin, to_pin


def movement_details(ring: RingCandidate, graph: TradeGraph) -> dict[str, Any]:
    """Inspect ring e-way bills for vehicle reuse (>80 km/h), fast GRN transit (>60 km/h),
    post-GRN generation, and single-transporter concentration.
    """
    ewbs, grns_by_inv = _collect_ring_evidence(ring, graph)
    if not ewbs:
        return {
            "eway_bill_count": 0,
            "anomalies": [],
            "score": None,
        }

    anomalies: list[dict[str, Any]] = []
    anomaly_scores: list[float] = []

    # 1. Same vehicle_no on two e-way bills with implied speed > 80 km/h
    by_vehicle: dict[str, list[EvidenceDoc]] = {}
    for b in ewbs:
        vno = _norm_vehicle(b.vehicle_no)
        if vno:
            by_vehicle.setdefault(vno, []).append(b)

    for vno in sorted(by_vehicle.keys()):
        v_bills = by_vehicle[vno]
        if len(v_bills) < 2:
            continue
        # Sort by generated_at timestamp
        dated_bills: list[tuple[datetime, EvidenceDoc]] = []
        for b in v_bills:
            dt = _parse_dt(b.generated_at, b.doc_date)
            if dt is not None:
                dated_bills.append((dt, b))
        dated_bills.sort(key=lambda item: (item[0], item[1].doc_id))

        for i in range(len(dated_bills)):
            t1, b1 = dated_bills[i]
            f1, to1 = _bill_pincodes(b1, graph)
            for j in range(i + 1, len(dated_bills)):
                t2, b2 = dated_bills[j]
                f2, to2 = _bill_pincodes(b2, graph)

                dist_candidates: list[float] = []
                for p_start, p_end in ((f1, f2), (to1, f2), (f1, to1), (f1, to2)):
                    d_km = road_distance_km(p_start, p_end)
                    if d_km is not None and d_km > 0:
                        dist_candidates.append(d_km)
                if not dist_candidates and b1.distance_km and b1.distance_km > 0:
                    dist_candidates.append(float(b1.distance_km))

                if not dist_candidates:
                    continue

                dist_km = max(dist_candidates)
                hours = abs((t2 - t1).total_seconds()) / 3600.0
                implied_speed = 9999.0 if hours <= 1e-6 else (dist_km / hours)

                if implied_speed > 80.0:
                    weight = 0.5 * (_doc_weight(b1) + _doc_weight(b2))
                    severity_val = min(1.0, 0.85 + 0.15 * min(1.0, (implied_speed - 80.0) / 120.0))
                    weighted_val = severity_val * weight
                    c1 = resolve_pincode(f1)
                    c2 = resolve_pincode(f2 or to1)
                    anomalies.append(
                        {
                            "kind": "vehicle_speed",
                            "vehicle_no": b1.vehicle_no or vno,
                            "doc_ids": [b1.doc_id, b2.doc_id],
                            "from_pincode": f1 or "",
                            "to_pincode": (f2 or to1 or ""),
                            "from_city": c1.city if c1 else (f1 or "Origin"),
                            "to_city": c2.city if c2 else (f2 or to1 or "Destination"),
                            "distance_km": round(dist_km, 1),
                            "hours": round(hours, 2),
                            "implied_speed_kmh": round(implied_speed, 1),
                            "evidence_weight": round(weight, 2),
                            "score": weighted_val,
                            "t1": b1.generated_at or b1.doc_date,
                            "t2": b2.generated_at or b2.doc_date,
                        }
                    )
                    anomaly_scores.append(weighted_val)

    # 2 & 3. Single e-way bill transit vs GRN date/time (faster than distance_km / 60 km/h, or generated after GRN)
    for b in ewbs:
        if not b.invoice_id:
            continue
        grns = grns_by_inv.get(b.invoice_id, [])
        if not grns:
            continue
        f_pin, t_pin = _bill_pincodes(b, graph)
        road_km = road_distance_km(f_pin, t_pin)
        dist_km = float(b.distance_km) if (b.distance_km and b.distance_km > 0) else (road_km or 0.0)
        t_ewb = _parse_dt(b.generated_at, b.doc_date)
        b_weight = _doc_weight(b)

        for grn in grns:
            grn_date_obj = date.fromisoformat(grn.doc_date)
            ewb_date_obj = t_ewb.date() if t_ewb is not None else date.fromisoformat(b.doc_date)
            t_grn = _parse_dt(grn.generated_at, None) if grn.generated_at else None

            # Condition 3: E-way bill generated_at after GRN date/timestamp
            is_after_grn = False
            if t_ewb is not None and t_grn is not None and t_ewb > t_grn:
                is_after_grn = True
            elif ewb_date_obj > grn_date_obj:
                is_after_grn = True

            if is_after_grn:
                weighted_val = 0.95 * b_weight
                anomalies.append(
                    {
                        "kind": "eway_after_grn",
                        "doc_ids": [b.doc_id, grn.doc_id],
                        "invoice_id": b.invoice_id,
                        "generated_at": b.generated_at or b.doc_date,
                        "grn_date": grn.generated_at or grn.doc_date,
                        "evidence_weight": round(b_weight, 2),
                        "score": weighted_val,
                    }
                )
                anomaly_scores.append(weighted_val)
                continue

            # Condition 2: Transit time faster than distance_km / 60 km/h
            if dist_km > 0 and t_ewb is not None:
                min_hours = dist_km / 60.0
                if t_grn is not None:
                    transit_hours = (t_grn - t_ewb).total_seconds() / 3600.0
                else:
                    # Only calendar date on GRN: compare against end of GRN business day (18:00 UTC)
                    grn_eod = datetime(
                        grn_date_obj.year, grn_date_obj.month, grn_date_obj.day, 18, 0, 0, tzinfo=timezone.utc
                    )
                    transit_hours = max(0.25, (grn_eod - t_ewb).total_seconds() / 3600.0)

                if 0.0 <= transit_hours < min_hours:
                    implied_speed = dist_km / max(transit_hours, 0.1)
                    severity_val = min(
                        1.0, 0.82 + 0.18 * min(1.0, (min_hours - transit_hours) / max(min_hours, 1.0))
                    )
                    weighted_val = severity_val * b_weight
                    c1 = resolve_pincode(f_pin)
                    c2 = resolve_pincode(t_pin)
                    anomalies.append(
                        {
                            "kind": "fast_grn_transit",
                            "doc_ids": [b.doc_id, grn.doc_id],
                            "invoice_id": b.invoice_id,
                            "from_city": c1.city if c1 else (f_pin or "Origin"),
                            "to_city": c2.city if c2 else (t_pin or "Destination"),
                            "distance_km": round(dist_km, 1),
                            "transit_hours": round(transit_hours, 2),
                            "min_hours_at_60kmh": round(min_hours, 2),
                            "implied_speed_kmh": round(implied_speed, 1),
                            "generated_at": b.generated_at or b.doc_date,
                            "grn_date": grn.generated_at or grn.doc_date,
                            "evidence_weight": round(b_weight, 2),
                            "score": weighted_val,
                        }
                    )
                    anomaly_scores.append(weighted_val)

    # 4. All ring legs using a single transporter_id
    bills_with_transporter = [b for b in ewbs if b.transporter_id and b.transporter_id.strip()]
    if len(bills_with_transporter) >= 2:
        transporters = {b.transporter_id.strip().upper() for b in bills_with_transporter if b.transporter_id}
        distinct_hops_covered = {
            (graph.resolve(b.seller_id), graph.resolve(b.buyer_id)) for b in bills_with_transporter
        }
        if len(transporters) == 1 and len(distinct_hops_covered) >= 2:
            only_transporter = next(iter(transporters))
            avg_weight = sum(sorted(_doc_weight(b) for b in bills_with_transporter)) / len(
                bills_with_transporter
            )
            weighted_val = 0.78 * avg_weight
            anomalies.append(
                {
                    "kind": "single_transporter",
                    "transporter_id": only_transporter,
                    "leg_count": len(distinct_hops_covered),
                    "doc_ids": [b.doc_id for b in bills_with_transporter],
                    "evidence_weight": round(avg_weight, 2),
                    "score": weighted_val,
                }
            )
            anomaly_scores.append(weighted_val)

    if not anomaly_scores:
        return {
            "eway_bill_count": len(ewbs),
            "anomalies": [],
            "score": 0.0,
        }

    sorted_scores = sorted(anomaly_scores, reverse=True)
    best = sorted_scores[0]
    bonus = 0.12 * sum(sorted_scores[1:])
    final_score = max(0.0, min(1.0, best + bonus))

    return {
        "eway_bill_count": len(ewbs),
        "anomalies": anomalies,
        "score": final_score,
    }


def s_movement(ring: RingCandidate, graph: TradeGraph) -> float | None:
    """Compute e-way bill movement plausibility signal in [0.0, 1.0], or None if no e-way bills exist."""
    return movement_details(ring, graph)["score"]

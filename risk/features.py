"""Buyer payment history, receivables concentration/dilution, invoice anomaly, and network tier feature extractor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Any

from contract.context import CaseContext
from contract.models import CheckStatus, DocType, Invoice, PaymentRecord, Severity, Tier


def _parse_iso_date(val: str | None) -> date | None:
    if not val or len(val) < 10:
        return None
    try:
        return date.fromisoformat(val[:10])
    except ValueError:
        return None


@dataclass(frozen=True)
class RiskFeatures:
    """Extracted buyer, receivables, invoice anomaly, and network features for scorecard evaluation."""

    buyer_id: str
    seller_id: str
    payment_count: int
    on_time_rate: float
    avg_dpd: float
    worst_dpd: int
    avg_days_to_pay: float
    dispute_rate: float
    six_month_trend: float
    buyer_concentration_share: float
    ageing_over_90d_share: float
    dilution_rate: float
    financed_share: float
    tenor_days: int
    amount_to_seller_median_ratio: float
    is_new_pair: bool
    network_tier: Tier

    def to_int_summary(self) -> dict[str, Any]:
        """Return a canonical-JSON-safe (float-free) summary using basis points and integers."""
        return {
            "buyer_id": self.buyer_id,
            "seller_id": self.seller_id,
            "payment_count": self.payment_count,
            "on_time_rate_bps": int(round(self.on_time_rate * 10000)),
            "avg_dpd_tenths": int(round(self.avg_dpd * 10)),
            "worst_dpd": self.worst_dpd,
            "avg_days_to_pay_tenths": int(round(self.avg_days_to_pay * 10)),
            "dispute_rate_bps": int(round(self.dispute_rate * 10000)),
            "six_month_trend_tenths": int(round(self.six_month_trend * 10)),
            "buyer_concentration_bps": int(round(self.buyer_concentration_share * 10000)),
            "ageing_over_90d_bps": int(round(self.ageing_over_90d_share * 10000)),
            "dilution_rate_bps": int(round(self.dilution_rate * 10000)),
            "financed_share_bps": int(round(self.financed_share * 10000)),
            "tenor_days": self.tenor_days,
            "amount_to_seller_median_bps": int(round(self.amount_to_seller_median_ratio * 10000)),
            "is_new_pair": self.is_new_pair,
            "network_tier": self.network_tier.value,
        }


def extract_network_tier(ctx: CaseContext) -> Tier:
    """Extract network_tier (Tier.LOW, Tier.MEDIUM, Tier.HIGH) from ctx.stage_outputs['network']."""
    network_checks = ctx.stage_outputs.get("network", [])
    highest = Tier.LOW
    rank = {Tier.LOW: 0, Tier.MEDIUM: 1, Tier.HIGH: 2}

    for chk in network_checks:
        raw_tier = chk.details.get("tier") or chk.details.get("network_tier")
        candidate: Tier | None = None
        if isinstance(raw_tier, Tier):
            candidate = raw_tier
        elif isinstance(raw_tier, str):
            low = raw_tier.strip().lower()
            if low in ("low", "medium", "high"):
                candidate = Tier(low)

        if candidate is None:
            if chk.status == CheckStatus.FAIL:
                candidate = Tier.HIGH
            elif chk.status == CheckStatus.WARN:
                candidate = Tier.HIGH if chk.severity in (Severity.HIGH, Severity.HARD) else Tier.MEDIUM
            else:
                candidate = Tier.LOW

        if rank[candidate] > rank[highest]:
            highest = candidate

    return highest


def compute_features(ctx: CaseContext) -> RiskFeatures:
    """Compute buyer payment history, receivables, invoice anomaly, and network tier features."""
    buyer_id = ctx.invoice.buyer_id
    seller_id = ctx.invoice.seller_id
    inv_date = _parse_iso_date(ctx.invoice.invoice_date)

    # 1. Buyer payment history features
    buyer_payments: list[PaymentRecord] = list(ctx.history.payments_by_buyer(buyer_id))
    payment_count = len(buyer_payments)

    all_hist_invoices: list[Invoice] = list(ctx.history.all_invoices())
    inv_by_id: dict[str, Invoice] = {inv.invoice_id: inv for inv in all_hist_invoices}

    if payment_count > 0:
        on_time_count = sum(
            1 for p in buyer_payments if p.paid_date is not None and p.dpd <= 0 and not p.disputed
        )
        on_time_rate = on_time_count / payment_count
        dpds = [max(0, int(p.dpd)) for p in buyer_payments]
        avg_dpd = sum(dpds) / payment_count
        worst_dpd = max(dpds)
        dispute_count = sum(1 for p in buyer_payments if p.disputed)
        dispute_rate = dispute_count / payment_count

        days_to_pay_list: list[float] = []
        for p in buyer_payments:
            paid_dt = _parse_iso_date(p.paid_date)
            due_dt = _parse_iso_date(p.due_date)
            linked_inv = inv_by_id.get(p.invoice_id)
            if linked_inv is not None and paid_dt is not None:
                linked_inv_dt = _parse_iso_date(linked_inv.invoice_date)
                if linked_inv_dt is not None:
                    days_to_pay_list.append(float(max(1, (paid_dt - linked_inv_dt).days)))
                    continue
            if paid_dt is not None and due_dt is not None:
                delta_vs_due = (paid_dt - due_dt).days
                base_terms = (
                    linked_inv.payment_terms_days
                    if linked_inv is not None
                    else ctx.invoice.payment_terms_days
                )
                days_to_pay_list.append(float(max(1, base_terms + delta_vs_due)))
            else:
                base_terms = (
                    linked_inv.payment_terms_days
                    if linked_inv is not None
                    else ctx.invoice.payment_terms_days
                )
                days_to_pay_list.append(float(max(1, base_terms + max(0, int(p.dpd)))))
        avg_days_to_pay = sum(days_to_pay_list) / len(days_to_pay_list)

        if payment_count >= 2:
            sorted_payments = sorted(buyer_payments, key=lambda p: (p.due_date, p.payment_id))
            mid = payment_count // 2
            older = sorted_payments[:mid]
            recent = sorted_payments[mid:]
            older_avg = sum(max(0, int(p.dpd)) for p in older) / len(older)
            recent_avg = sum(max(0, int(p.dpd)) for p in recent) / len(recent)
            six_month_trend = recent_avg - older_avg
        else:
            six_month_trend = 0.0
    else:
        # Neutral defaults when buyer has no recorded payment history
        on_time_rate = 0.88
        avg_dpd = 5.0
        worst_dpd = 10
        avg_days_to_pay = float(max(30, ctx.invoice.payment_terms_days))
        dispute_rate = 0.0
        six_month_trend = 0.0

    # 2. Receivables features
    seller_invoices: list[Invoice] = [
        inv for inv in all_hist_invoices if inv.seller_id == seller_id and inv.doc_type == DocType.INVOICE
    ]
    if all(inv.invoice_id != ctx.invoice.invoice_id for inv in seller_invoices):
        seller_invoices.append(ctx.invoice)

    seller_total_paise = sum(inv.grand_total_paise for inv in seller_invoices)
    pair_total_paise = sum(
        inv.grand_total_paise for inv in seller_invoices if inv.buyer_id == buyer_id
    )
    if seller_total_paise > 0:
        buyer_concentration_share = min(1.0, max(0.0, pair_total_paise / seller_total_paise))
    else:
        buyer_concentration_share = 1.0

    # Ageing > 90d share (across buyer payments and unpaid buyer invoices)
    if payment_count > 0:
        total_pay_paise = sum(max(1, p.amount_paise) for p in buyer_payments)
        aged_90_paise = 0
        for p in buyer_payments:
            is_over_90 = p.dpd > 90
            if not is_over_90 and p.paid_date is None and inv_date is not None:
                due_dt = _parse_iso_date(p.due_date)
                if due_dt is not None and (inv_date - due_dt).days > 90:
                    is_over_90 = True
            if is_over_90:
                aged_90_paise += max(1, p.amount_paise)
        ageing_over_90d_share = min(1.0, max(0.0, aged_90_paise / total_pay_paise))
    else:
        ageing_over_90d_share = 0.0

    # Dilution rate = credit notes / invoiced value
    credit_note_ids_seen: set[str] = set()
    credit_note_paise = 0
    for inv in seller_invoices:
        for cn in ctx.history.credit_notes_for_invoice(inv.invoice_id):
            if cn.invoice_id not in credit_note_ids_seen:
                credit_note_ids_seen.add(cn.invoice_id)
                credit_note_paise += cn.grand_total_paise

    raw_hist_invoices = getattr(ctx.history, "invoices", [])
    for inv in raw_hist_invoices:
        if (
            inv.doc_type == DocType.CREDIT_NOTE
            and (inv.seller_id == seller_id or inv.buyer_id == buyer_id)
            and inv.invoice_id not in credit_note_ids_seen
        ):
            credit_note_ids_seen.add(inv.invoice_id)
            credit_note_paise += inv.grand_total_paise

    for ev in list(ctx.evidence) + list(ctx.history.all_evidence()):
        if (
            ev.doc_type == DocType.CREDIT_NOTE
            and (ev.seller_id == seller_id or ev.buyer_id == buyer_id)
            and ev.doc_id not in credit_note_ids_seen
        ):
            credit_note_ids_seen.add(ev.doc_id)
            credit_note_paise += ev.total_paise

    if seller_total_paise > 0:
        dilution_rate = min(1.0, max(0.0, credit_note_paise / seller_total_paise))
    else:
        dilution_rate = 0.0

    # Financed share = active allocations / reservations across seller's invoices
    financed_paise = 0
    for inv in seller_invoices:
        alloc = ctx.history.allocations_for_invoice(inv.invoice_id)
        financed_paise += max(0, int(alloc))

    raw_reservations = getattr(ctx.history, "reservations", [])
    for rsv in raw_reservations:
        if rsv.seller_id == seller_id and rsv.state.value in ("reserved", "financed"):
            financed_paise += rsv.amount_paise

    if seller_total_paise > 0:
        financed_share = min(1.0, max(0.0, financed_paise / seller_total_paise))
    else:
        financed_share = 0.0

    # 3. Invoice anomaly features
    tenor_days = int(ctx.request.tenor_days)
    seller_amounts = [inv.grand_total_paise for inv in seller_invoices if inv.grand_total_paise > 0]
    if seller_amounts:
        med_amount = float(median(seller_amounts))
        amount_to_seller_median_ratio = (
            ctx.invoice.grand_total_paise / med_amount if med_amount > 0 else 1.0
        )
    else:
        amount_to_seller_median_ratio = 1.0

    prior_pair_invoices = [
        inv
        for inv in all_hist_invoices
        if inv.seller_id == seller_id
        and inv.buyer_id == buyer_id
        and inv.invoice_id != ctx.invoice.invoice_id
    ]
    prior_pair_payments = [p for p in buyer_payments if p.seller_id == seller_id]
    is_new_pair = len(prior_pair_invoices) == 0 and len(prior_pair_payments) == 0

    # 4. Network tier
    network_tier = extract_network_tier(ctx)

    return RiskFeatures(
        buyer_id=buyer_id,
        seller_id=seller_id,
        payment_count=payment_count,
        on_time_rate=on_time_rate,
        avg_dpd=avg_dpd,
        worst_dpd=worst_dpd,
        avg_days_to_pay=avg_days_to_pay,
        dispute_rate=dispute_rate,
        six_month_trend=six_month_trend,
        buyer_concentration_share=buyer_concentration_share,
        ageing_over_90d_share=ageing_over_90d_share,
        dilution_rate=dilution_rate,
        financed_share=financed_share,
        tenor_days=tenor_days,
        amount_to_seller_median_ratio=amount_to_seller_median_ratio,
        is_new_pair=is_new_pair,
        network_tier=network_tier,
    )

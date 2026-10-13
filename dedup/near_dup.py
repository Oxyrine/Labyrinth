"""Fuzzy near-duplicate invoice detector (`dedup.near_dup`)."""

from __future__ import annotations

import re
from datetime import date

from contract.context import CaseContext
from contract.models import CheckResult, CheckStatus, Invoice, Severity, format_inr
from dedup.identity import _get_dedup_source, normalize_identity_token

_PREFIX_PATTERN = re.compile(r"^(?:(?:INVOICE|INV|BILL|TAX)[\s\-/_]*)+", re.IGNORECASE)
_SEP_PATTERN = re.compile(r"[\s\-/_]+")


def aggressive_normalize_inv_no(s: str) -> str:
    """Aggressively normalize an invoice number:
    uppercase, strip common prefixes (INV, BILL, TAX) and separators (-, /, _, space),
    and map homoglyphs O->0, I->1, l->1.
    """
    raw = str(s).strip()
    stripped = _PREFIX_PATTERN.sub("", raw)
    if not stripped:
        stripped = raw
    # Map lowercase 'l' -> '1' prior to uppercasing so literal 'L' (as in BILL) is not turned into '1'
    homoglyphed = stripped.replace("l", "1").upper().replace("O", "0").replace("I", "1")
    return _SEP_PATTERN.sub("", homoglyphed)


def _line_item_tokens(invoice: Invoice) -> set[str]:
    """Extract normalized alphanumeric tokens from line-item HSN/SAC codes and descriptions."""
    tokens: set[str] = set()
    for li in invoice.line_items:
        text = f"{li.hsn_sac} {li.description}".lower()
        for tok in re.findall(r"[a-z0-9]+", text):
            tokens.add(tok)
    return tokens


def compute_line_jaccard(inv_a: Invoice, inv_b: Invoice) -> float:
    """Compute Jaccard similarity over line-item HSN/SAC and description tokens."""
    tokens_a = _line_item_tokens(inv_a)
    tokens_b = _line_item_tokens(inv_b)
    if not tokens_a and not tokens_b:
        return 1.0
    union = tokens_a | tokens_b
    if not union:
        return 0.0
    return len(tokens_a & tokens_b) / len(union)


def check_near_duplicate(ctx: CaseContext) -> CheckResult:
    """Check `dedup.near_dup`: scans seller's invoices for near-duplicates against same buyer."""
    check_id = "dedup.near_dup"
    source = _get_dedup_source()

    meta = (
        ctx.history.case_metadata()
        if hasattr(ctx.history, "case_metadata") and callable(ctx.history.case_metadata)
        else {}
    )
    unavailable = (meta or {}).get("unavailable_checks") or []
    if check_id in unavailable:
        return CheckResult(
            check=check_id,
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[
                f"Historical party invoice records are unavailable to run near-duplicate check for {ctx.invoice.invoice_id}."
            ],
        )

    curr = ctx.invoice
    curr_norm_no = aggressive_normalize_inv_no(curr.invoice_no)
    curr_min_no = normalize_identity_token(curr.invoice_no)
    curr_date = date.fromisoformat(curr.invoice_date)

    candidates: list[Invoice] = []
    if hasattr(ctx.history, "invoices_by_party") and callable(ctx.history.invoices_by_party):
        candidates = list(ctx.history.invoices_by_party(curr.seller_id))

    matches: list[tuple[Invoice, float, int]] = []
    for other in candidates:
        if other.invoice_id == curr.invoice_id:
            continue
        if other.seller_id != curr.seller_id or other.buyer_id != curr.buyer_id:
            continue
        # Exact same minimally-normalized invoice_no is handled by dedup.identity / dedup.registry
        if normalize_identity_token(other.invoice_no) == curr_min_no:
            continue

        # 1. Aggressive normalized invoice number match
        if aggressive_normalize_inv_no(other.invoice_no) != curr_norm_no:
            continue

        # 2. Amount within 1% (abs(other - curr) <= 0.01 * curr)
        if abs(other.grand_total_paise - curr.grand_total_paise) * 100 > curr.grand_total_paise:
            continue

        # 3. Invoice dates within 7 days
        other_date = date.fromisoformat(other.invoice_date)
        day_diff = abs((other_date - curr_date).days)
        if day_diff > 7:
            continue

        # 4. Line-item description/HSN token Jaccard similarity >= 0.8
        jaccard = compute_line_jaccard(curr, other)
        if jaccard >= 0.8:
            matches.append((other, jaccard, day_diff))

    if matches:
        other, jaccard, day_diff = matches[0]
        return CheckResult(
            check=check_id,
            status=CheckStatus.WARN,
            severity=Severity.HIGH,
            source=source,
            evidence=[
                f"Near-duplicate detected: Invoice {curr.invoice_id} ('{curr.invoice_no}', {format_inr(curr.grand_total_paise)}, {curr.invoice_date}) "
                f"closely matches prior invoice {other.invoice_id} ('{other.invoice_no}', {format_inr(other.grand_total_paise)}, {other.invoice_date}) "
                f"between seller {curr.seller_id} and buyer {curr.buyer_id} (normalized '{curr_norm_no}', {day_diff}d apart, "
                f"line-item Jaccard similarity {jaccard * 100:.0f}%)."
            ],
            refs=[other.invoice_id],
            details={
                "matched_invoice_id": other.invoice_id,
                "matched_invoice_no": other.invoice_no,
                "normalized_inv_no": curr_norm_no,
                "days_apart": day_diff,
                "jaccard_bps": int(round(jaccard * 10000)),
            },
        )

    return CheckResult(
        check=check_id,
        status=CheckStatus.PASS,
        severity=Severity.INFO,
        source=source,
        evidence=[
            f"No near-duplicate invoice found for {curr.invoice_id} ('{curr.invoice_no}', normalized '{curr_norm_no}') "
            f"across {len(candidates)} historical invoice(s) for seller {curr.seller_id}."
        ],
    )

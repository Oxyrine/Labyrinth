"""E-way bill requirement and verification under simplified rules (`match.ewaybill`)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from contract.context import CaseContext
from contract.models import CheckResult, CheckStatus, DocType, Severity, format_inr
from match.three_way import (
    get_case_evidence,
    get_check_source,
    is_check_unavailable,
    is_pure_services,
)

_RULES_DIR = Path(__file__).resolve().parent / "rules"


def load_ewaybill_rules() -> dict[str, Any]:
    """Load simplified e-way bill rules from match/rules/ewaybill.toml."""
    with open(_RULES_DIR / "ewaybill.toml", "rb") as f:
        data = tomllib.load(f)
    return {
        "threshold_paise": int(data.get("threshold_paise", 5_000_000)),
        "exempt_hsn_prefixes": list(data.get("exempt_hsn_prefixes", ["0101", "0701", "1001", "4901"])),
        "simplified_notice": str(
            data.get(
                "simplified_notice",
                "Evaluated under simplified e-way bill rules table (₹50,000 threshold).",
            )
        ),
        "intrastate_thresholds_paise": dict(data.get("intrastate_thresholds_paise", {})),
    }


def _is_exempt_hsn(hsn_sac: str, exempt_prefixes: list[str]) -> bool:
    code = hsn_sac.strip()
    return any(code.startswith(prefix) for prefix in exempt_prefixes)


def check_ewaybill(ctx: CaseContext) -> CheckResult:
    """Check `match.ewaybill`: verifies E-way bill requirement and presence under simplified rules."""
    check_id = "match.ewaybill"
    source = get_check_source()
    rules = load_ewaybill_rules()
    threshold_paise: int = rules["threshold_paise"]
    exempt_prefixes: list[str] = rules["exempt_hsn_prefixes"]

    # 1. Pure services -> not_applicable
    if is_pure_services(ctx.invoice):
        return CheckResult(
            check=check_id,
            status=CheckStatus.NOT_APPLICABLE,
            severity=Severity.INFO,
            source=source,
            evidence=[
                f"Invoice {ctx.invoice.invoice_id} covers pure services; e-way bill is not applicable under simplified rules table."
            ],
        )

    # 2. Exempt HSN or grand_total_paise <= 5_000_000 -> not_applicable (unless explicitly marked unavailable)
    goods_lines = [li for li in ctx.invoice.line_items if not li.is_service]
    all_exempt = bool(goods_lines) and all(_is_exempt_hsn(li.hsn_sac, exempt_prefixes) for li in goods_lines)

    if is_check_unavailable(ctx, check_id):
        return CheckResult(
            check=check_id,
            status=CheckStatus.UNAVAILABLE,
            severity=Severity.MEDIUM,
            source=source,
            evidence=[
                f"E-way bill verification is unavailable for goods invoice {ctx.invoice.invoice_id} ({format_inr(ctx.invoice.grand_total_paise)})."
            ],
        )

    if all_exempt or ctx.invoice.grand_total_paise <= threshold_paise:
        reason = (
            f"all goods HSN codes ({', '.join(li.hsn_sac for li in goods_lines)}) are exempt"
            if all_exempt
            else f"grand total {format_inr(ctx.invoice.grand_total_paise)} does not exceed threshold {format_inr(threshold_paise)}"
        )
        return CheckResult(
            check=check_id,
            status=CheckStatus.NOT_APPLICABLE,
            severity=Severity.INFO,
            source=source,
            evidence=[
                f"E-way bill is not applicable for invoice {ctx.invoice.invoice_id} because {reason} under simplified rules table."
            ],
        )

    docs = get_case_evidence(ctx)
    ewb_docs = [d for d in docs if d.doc_type == DocType.EWAY_BILL]

    if not ewb_docs:
        return CheckResult(
            check=check_id,
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            source=source,
            evidence=[
                f"Goods invoice {ctx.invoice.invoice_id} for {format_inr(ctx.invoice.grand_total_paise)} exceeds "
                f"{format_inr(threshold_paise)} threshold under simplified rules table, but required e-way bill is missing."
            ],
        )

    ewb = ewb_docs[0]
    vehicle_info = f", vehicle {ewb.vehicle_no}" if ewb.vehicle_no else ""
    return CheckResult(
        check=check_id,
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        source=source,
        evidence_weight=ewb.evidence_weight,
        verification_status=ewb.verification_status,
        evidence=[
            f"E-way bill {ewb.doc_id} ({ewb.doc_no}{vehicle_info}) verified for goods invoice {ctx.invoice.invoice_id} "
            f"({format_inr(ctx.invoice.grand_total_paise)}) against {format_inr(threshold_paise)} threshold in simplified rules table."
        ],
        refs=[ewb.doc_id],
        details={"simplified_notice": rules["simplified_notice"], "threshold_paise": threshold_paise},
    )

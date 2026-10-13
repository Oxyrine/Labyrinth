"""Entrypoint for Stage 2a cross-document matching checks (`match.*`)."""

from __future__ import annotations

from contract.context import CaseContext
from contract.models import CheckResult
from match.contract_terms import check_contract_terms
from match.ewaybill import check_ewaybill
from match.ordering import check_date_ordering
from match.partials import check_over_invoicing
from match.services import check_service_acceptance
from match.three_way import (
    check_grn_presence,
    check_hsn,
    check_parties,
    check_qty,
    check_rate,
    check_total,
    get_case_metadata,
    get_check_source,
)


def run(ctx: CaseContext) -> list[CheckResult]:
    """Run all `match.*` checks for the given CaseContext, respecting `LABYRINTH_MATCH_LIVE`."""
    source = get_check_source()
    meta = get_case_metadata(ctx)
    if source.value == "fixture" and "fixture_match_results" in meta:
        raw = meta["fixture_match_results"]
        return [
            r if isinstance(r, CheckResult) else CheckResult.model_validate(r)
            for r in raw
        ]

    return [
        check_parties(ctx),
        check_hsn(ctx),
        check_qty(ctx),
        check_rate(ctx),
        check_total(ctx),
        check_grn_presence(ctx),
        check_service_acceptance(ctx),
        check_date_ordering(ctx),
        check_contract_terms(ctx),
        check_over_invoicing(ctx),
        check_ewaybill(ctx),
    ]

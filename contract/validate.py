"""Export JSON Schemas from contract/models.py and validate JSON artifacts across fixtures/, data/out/, and eval/."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contract.models import (
    AuditEvent,
    BuyerAcceptance,
    CheckResult,
    Decision,
    EvidenceDoc,
    FinancingRequest,
    Invoice,
    InvoiceVersion,
    Party,
    PaymentRecord,
    Reservation,
)

SCHEMA_MODELS = {
    "Party": Party,
    "Invoice": Invoice,
    "InvoiceVersion": InvoiceVersion,
    "EvidenceDoc": EvidenceDoc,
    "BuyerAcceptance": BuyerAcceptance,
    "FinancingRequest": FinancingRequest,
    "Reservation": Reservation,
    "CheckResult": CheckResult,
    "Decision": Decision,
    "AuditEvent": AuditEvent,
    "PaymentRecord": PaymentRecord,
}

MONEY_KEYS = {
    "rate_paise",
    "taxable_paise",
    "tax_paise",
    "total_paise",
    "subtotal_paise",
    "cgst_paise",
    "sgst_paise",
    "igst_paise",
    "total_tax_paise",
    "grand_total_paise",
    "accepted_amount_paise",
    "requested_amount_paise",
    "amount_paise",
    "advance_amount_paise",
    "discount_paise",
    "net_disbursement_paise",
    "cgst_bps",
    "sgst_bps",
    "igst_bps",
    "advance_rate_bps",
    "annual_rate_bps",
}


def export_schemas(schema_dir: Path | None = None) -> list[Path]:
    target_dir = schema_dir or (ROOT / "contract" / "schemas")
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, model_cls in SCHEMA_MODELS.items():
        schema = model_cls.model_json_schema()
        out_path = target_dir / f"{name}.json"
        out_path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(out_path)
    return written


def _check_no_float_money(obj: Any, path: str = "$") -> list[str]:
    errors: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            sub = f"{path}.{k}"
            if k in MONEY_KEYS and isinstance(v, float):
                errors.append(f"{sub}: float literal {v} in integer money/bps field '{k}'")
            errors.extend(_check_no_float_money(v, sub))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            errors.extend(_check_no_float_money(item, f"{path}[{idx}]"))
    return errors


def validate_case_bundle(bundle: dict[str, Any], source_path: Path) -> list[str]:
    errors = _check_no_float_money(bundle, str(source_path))
    try:
        if "request" in bundle:
            FinancingRequest.model_validate(bundle["request"])
        if "invoice" in bundle:
            Invoice.model_validate(bundle["invoice"])
        if "version" in bundle:
            InvoiceVersion.model_validate(bundle["version"])
        if "parties" in bundle:
            parties = bundle["parties"]
            if isinstance(parties, dict):
                for p in parties.values():
                    Party.model_validate(p)
            elif isinstance(parties, list):
                for p in parties:
                    Party.model_validate(p)
        if "evidence" in bundle:
            for doc in bundle["evidence"]:
                EvidenceDoc.model_validate(doc)
        if "checks" in bundle:
            for chk in bundle["checks"]:
                CheckResult.model_validate(chk)
        if "decision" in bundle:
            Decision.model_validate(bundle["decision"])
    except Exception as exc:
        errors.append(f"{source_path}: {exc}")
    return errors


def validate_all() -> tuple[int, list[str]]:
    checked = 0
    errors: list[str] = []

    schema_dir = ROOT / "contract" / "schemas"
    if not schema_dir.exists() or not list(schema_dir.glob("*.json")):
        export_schemas(schema_dir)

    for folder_name in ("fixtures", "data/out", "eval"):
        folder = ROOT / folder_name
        if not folder.exists():
            continue
        for json_file in sorted(folder.rglob("*.json")):
            checked += 1
            try:
                raw_text = json_file.read_text(encoding="utf-8")
                data = json.loads(raw_text)
            except Exception as exc:
                errors.append(f"{json_file}: invalid JSON ({exc})")
                continue
            errors.extend(_check_no_float_money(data, str(json_file)))
            if json_file.name == "case.json":
                errors.extend(validate_case_bundle(data, json_file))
            elif json_file.name == "check_examples.json" and isinstance(data, list):
                for item in data:
                    try:
                        CheckResult.model_validate(item)
                    except Exception as exc:
                        errors.append(f"{json_file}: {exc}")
        for jsonl_file in sorted(folder.rglob("*.jsonl")):
            checked += 1
            for line_no, line in enumerate(jsonl_file.read_text(encoding="utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except Exception as exc:
                    errors.append(f"{jsonl_file}:{line_no}: invalid JSONL ({exc})")
                    continue
                errors.extend(_check_no_float_money(item, f"{jsonl_file}:{line_no}"))
                if jsonl_file.name == "decisions.jsonl":
                    try:
                        Decision.model_validate(item)
                    except Exception as exc:
                        errors.append(f"{jsonl_file}:{line_no}: {exc}")

    return checked, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Labyrinth JSON artifacts and export schemas.")
    parser.add_argument("--export-schemas", action="store_true", help="Export JSON schemas to contract/schemas/")
    args = parser.parse_args()

    if args.export_schemas:
        written = export_schemas()
        print(f"Exported {len(written)} JSON schemas to contract/schemas/")

    checked, errors = validate_all()
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        print(f"Validation failed: {len(errors)} error(s) across {checked} file(s).", file=sys.stderr)
        return 1
    print(f"Validated {checked} artifact file(s) successfully (schema_version=1).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

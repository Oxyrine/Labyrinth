"""CLI entrypoint invoked by `python run.py report` to generate eval/report.json and eval/report.md."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.report import generate_reports


def main() -> int:
    json_path, md_path = generate_reports(ROOT)
    print(f"[eval] Wrote deterministic reports: {json_path.relative_to(ROOT)} and {md_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

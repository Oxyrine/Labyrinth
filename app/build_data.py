"""Write the offline snapshot (`app/static/data.js`) from a real run of the live engine.

The UI falls back to this file when no server is reachable (e.g. opened from `file://`). It is a recording of
real pipeline output, labelled as a snapshot in the UI; nothing in it is hand-written.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.live import LiveEngine  # noqa: E402

OUT = Path(__file__).resolve().parent / "static" / "data.js"


def build_snapshot() -> dict:
    engine = LiveEngine()
    engine.load_fixtures()
    report = ROOT / "eval" / "report.json"
    return {
        "generated_by": "app.live.LiveEngine (real pipeline run over fixtures)",
        "anchor": engine.anchor_label(),
        "case_order": list(engine.order),
        "cases": {cid: engine.card(cid) for cid in engine.order},
        "queue": engine.queue(),
        "eval_summary": json.loads(report.read_text(encoding="utf-8")) if report.is_file() else None,
    }


def write_offline_js(path: Path = OUT) -> Path:
    snapshot = build_snapshot()
    text = json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
    path.write_text("window.TESSERA_OFFLINE_DATA = " + text + ";\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    print(f"wrote {write_offline_js()}")

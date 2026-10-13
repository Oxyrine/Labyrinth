"""Tessera CLI entry point (`world`, `pipeline`, `report`, `demo`, `test`, `guards`)."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def cmd_test(args: argparse.Namespace) -> int:
    from contract.validate import export_schemas, validate_all

    export_schemas()
    checked, errors = validate_all()
    if errors:
        for err in errors:
            print(f"VALIDATION ERROR: {err}", file=sys.stderr)
        return 1
    print(f"[contract] Validated {checked} JSON/JSONL artifact(s).")
    pytest_cmd = [sys.executable, "-m", "pytest"]
    if not getattr(args, "all_tests", False):
        pytest_cmd.extend(["-m", "not slow"])
    return subprocess.call(pytest_cmd, cwd=str(ROOT))


def cmd_world(args: argparse.Namespace) -> int:
    gen_script = ROOT / "data" / "generate.py"
    if not gen_script.exists():
        print("[world] Generator stub (M0): generating M0 seed fixtures only.")
        from contract.seed_m0 import build_seed_fixtures

        build_seed_fixtures()
        return 0
    cmd = [
        sys.executable,
        str(gen_script),
        "--regime",
        args.regime,
    ]
    if args.seed is not None:
        cmd.extend(["--seed", str(args.seed)])
    if args.json_only:
        cmd.append("--json-only")
    return subprocess.call(cmd, cwd=str(ROOT))


def cmd_pipeline(args: argparse.Namespace) -> int:
    from workflow.pipeline import run_batch_regime

    run_batch_regime(regime=args.regime)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    eval_run = ROOT / "eval" / "run.py"
    if not eval_run.exists():
        print("[report] eval/run.py not yet present.", file=sys.stderr)
        return 1
    return subprocess.call([sys.executable, str(eval_run)], cwd=str(ROOT))


def cmd_demo(args: argparse.Namespace) -> int:
    import uvicorn

    os.environ.setdefault("TESSERA_DEMO", "1")
    port = int(os.environ.get("PORT", str(args.port)))
    uvicorn.run("app.server:app", host="127.0.0.1", port=port, reload=False)
    return 0


def cmd_guards(_args: argparse.Namespace) -> int:
    errors: list[str] = []

    # 1. No innerHTML in app/static
    static_dir = ROOT / "app" / "static"
    if static_dir.exists():
        for f in static_dir.rglob("*"):
            if f.is_file() and f.suffix in {".js", ".html"}:
                content = f.read_text(encoding="utf-8")
                if "innerHTML" in content:
                    errors.append(f"Guard violation: 'innerHTML' found in {f}")

    # 2. eval/ imports nothing from internal module packages
    eval_dir = ROOT / "eval"
    forbidden_pkgs = {"extract", "match", "dedup", "network", "risk", "workflow", "audit", "chain", "app"}
    if eval_dir.exists():
        import_re = re.compile(r"^\s*(?:from|import)\s+([a-zA-Z0-9_]+)", re.MULTILINE)
        for f in eval_dir.rglob("*.py"):
            content = f.read_text(encoding="utf-8")
            for m in import_re.finditer(content):
                pkg = m.group(1)
                if pkg in forbidden_pkgs:
                    errors.append(f"Guard violation: {f} imports forbidden module package '{pkg}'")

    # 3. Validate schemas and ensure no float literals in money fields
    from contract.validate import validate_all

    _, val_errors = validate_all()
    errors.extend(val_errors)

    if errors:
        for e in errors:
            print(f"GUARD FAIL: {e}", file=sys.stderr)
        return 1
    print("All CI security and architectural boundary guards passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="run.py", description="Tessera unified task runner")
    sub = parser.add_subparsers(dest="command", required=True)

    p_world = sub.add_parser("world", help="Generate synthetic economy")
    p_world.add_argument("--regime", choices=["A", "B"], default="A")
    p_world.add_argument("--seed", type=int, default=None, help="default: 2601 for A, 2602 for B")
    p_world.add_argument("--json-only", action="store_true")
    p_world.set_defaults(func=cmd_world)

    p_pipe = sub.add_parser("pipeline", help="Run full verification pipeline over generated world")
    p_pipe.add_argument("--regime", choices=["A", "B"], default="A")
    p_pipe.set_defaults(func=cmd_pipeline)

    p_rep = sub.add_parser("report", help="Run evaluation and generate report.md / report.json")
    p_rep.set_defaults(func=cmd_report)

    p_demo = sub.add_parser("demo", help="Launch FastAPI demo server")
    p_demo.add_argument("--port", type=int, default=8000)
    p_demo.set_defaults(func=cmd_demo)

    p_test = sub.add_parser("test", help="Validate contract and run pytest suite")
    p_test.add_argument("--all", dest="all_tests", action="store_true", help="Include slow tests")
    p_test.set_defaults(func=cmd_test)

    p_guards = sub.add_parser("guards", help="Run architectural & security CI guards")
    p_guards.set_defaults(func=cmd_guards)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

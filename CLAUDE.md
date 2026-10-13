# Tessera

Synesis 26 Problem 2 invoice-financing prototype. See README.md for what is real and what is simplified.

## Commands
- `python run.py test` (contract validation + fast tests; `--all` adds slow), `python run.py guards`
- `python run.py world --regime A|B`, `python run.py pipeline --regime A|B`, `python run.py report`
- `TESSERA_DEMO=1 python run.py demo`; `python app/build_data.py` refreshes `app/static/data.js`
- Regenerate fixtures with `python fixtures/build_fixtures.py` (rewrites fixture PDFs and case.json)

## Rules that keep it honest
- Every module exposes `check.run(ctx) -> list[CheckResult]`; missing input is `unavailable`, never `pass`.
- Money is integer paise, rates are basis points. No floats in money fields.
- Extraction reads printed text only: no embedded answer keys, no default values for missing fields.
- `eval/` reads pipeline outputs and ground truth only; it must not import module packages (`run.py guards` checks).
- The demo app (`app/live.py`) must never hand-write a decision, signature or audit result; the UI says "simulated" for the anchor chain.
- No `innerHTML` in `app/static`. Demo-only routes exist only with `TESSERA_DEMO=1`.
- Thresholds are frozen in `eval/thresholds.lock.json` before regime B is generated.

## Git
Commit as `Aakash A <230161983+Oxyrine@users.noreply.github.com>` (author and committer). No Claude co-author trailer.

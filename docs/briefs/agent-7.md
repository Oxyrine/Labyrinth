# Agent A7 Brief — FastAPI Server & Offline-Capable Static Web Interface (`app/`)

## Ownership & Boundaries
- **Owned folders**: `app/`, `tests/test_app.py`
- **Forbidden folders**: Never mutate other modules' files.

## Deliverables
- `app/server.py`: FastAPI routes (`POST /requests`, `GET /requests/{id}`, `POST /requests/{id}/accept`, `POST /reservations/{op}`, `POST /overrides`, `GET /queue`, `GET /audit/verify/{target}`, `POST /breakit`). Demo routes (`POST /demo/tamper`, `POST /demo/network-off`) registered **only when `LABYRINTH_DEMO=1`**.
- Security baseline: CSP & security headers middleware, role tokens per demo identity, 10 MB payload limit, PDF magic-byte check (`%PDF-`), in-process rate limiter on `/breakit` and `/requests`.
- `app/static/`: `index.html`, `seller.js`, `lender.js`, `queue.js`, `verifier.js`, `ring.js` (SVG ring with solid invoice hops and dashed corporate bridges), `tour.js`, `app.css`.
- **Zero `innerHTML` rule**: Every dynamic DOM insertion uses `textContent` or `createElement` (enforced by CI grep).
- `app/build_data.py` → `app/static/data.js` (and `app/data.js`) offline snapshot so `index.html` works directly via `file://` if the server is stopped.

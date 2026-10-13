"""Labyrinth FastAPI server. Thin HTTP layer over `app.live.LiveEngine`; all decisions come from the real modules.

Auth note: the demo identifies callers by a role token header (`X-Labyrinth-Role: lender:L1`). That is a demo
convention, not authentication; production needs real identity, per-party keys and mTLS.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.live import Conflict, LiveEngine, NotFound  # noqa: E402
from app.market import build_market  # noqa: E402
from extract import ExtractionError  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_PAYLOAD_BYTES = 10 * 1024 * 1024
ROLES = {"seller", "buyer", "lender", "reviewer", "auditor"}

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}

DEMO_IDENTITIES = [
    {"token": "seller:P-0001", "role": "seller", "label": "Seller (demo)"},
    {"token": "buyer:P-0002", "role": "buyer", "label": "Buyer (demo)"},
    {"token": "lender:L1", "role": "lender", "label": "Lender L1"},
    {"token": "lender:L2", "role": "lender", "label": "Lender L2"},
    {"token": "reviewer:REV-1", "role": "reviewer", "label": "Reviewer 1"},
    {"token": "reviewer:REV-2", "role": "reviewer", "label": "Reviewer 2"},
    {"token": "auditor:AUD-1", "role": "auditor", "label": "Auditor"},
]


class TokenBucketRateLimiter:
    """Thread-safe in-process token bucket for POST /requests and POST /breakit."""

    def __init__(self, capacity: int = 40, refill_per_sec: float = 10.0) -> None:
        self.capacity, self.refill_per_sec = capacity, refill_per_sec
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, float]] = {}

    def allow(self, key: str, cost: float = 1.0) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self.capacity), now))
            tokens = min(float(self.capacity), tokens + max(0.0, now - last) * self.refill_per_sec)
            if tokens < cost:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - cost, now)
            return True


def caller(request: Request, default_role: str) -> dict[str, str]:
    """Role/actor from `X-Labyrinth-Role` or `Authorization: Bearer role:actor`; unknown roles are rejected."""
    token = request.headers.get("X-Labyrinth-Role", "").strip()
    if not token:
        auth = request.headers.get("Authorization", "").strip()
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token:
        return {"role": default_role, "actor": "", "explicit": "false"}
    role, _, actor = token.partition(":")
    role = role.strip().lower()
    if role not in ROLES:
        raise HTTPException(status_code=403, detail=f"Unknown role '{role}'.")
    return {"role": role, "actor": actor.strip(), "explicit": "true"}


def require(request: Request, allowed: set[str], default_role: str) -> dict[str, str]:
    who = caller(request, default_role)
    if who["explicit"] == "true" and who["role"] not in allowed:
        raise HTTPException(status_code=403, detail=f"Role '{who['role']}' is not permitted here (needs one of {sorted(allowed)}).")
    return who


async def json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def eval_summary() -> dict[str, Any] | None:
    path = ROOT / "eval" / "report.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def create_app(demo_mode: bool | None = None, engine: LiveEngine | None = None) -> FastAPI:
    is_demo = demo_mode if demo_mode is not None else (os.environ.get("LABYRINTH_DEMO", "0") == "1")
    app = FastAPI(title="Labyrinth", version="1.0.0", docs_url="/api/docs" if is_demo else None, redoc_url=None)

    if engine is None:
        engine = LiveEngine()
        engine.load_fixtures()
    app.state.engine = engine
    app.state.is_demo = is_demo
    app.state.limiter = TokenBucketRateLimiter(capacity=int(os.environ.get("LABYRINTH_RATE_LIMIT_CAPACITY", "40")))

    @app.middleware("http")
    async def guards(request: Request, call_next: Any) -> Any:
        def deny(status: int, detail: str) -> JSONResponse:
            resp = JSONResponse({"detail": detail}, status_code=status)
            resp.headers.update(SECURITY_HEADERS)
            return resp

        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > MAX_PAYLOAD_BYTES:
            return deny(413, f"Payload size {cl} exceeds the 10 MB limit.")
        if request.method == "POST" and request.url.path in {"/requests", "/breakit"}:
            key = f"{request.client.host if request.client else 'local'}:{request.url.path}"
            if not app.state.limiter.allow(key):
                return deny(429, "Rate limit exceeded. Please retry shortly.")
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    @app.exception_handler(Conflict)
    async def _conflict(_: Request, exc: Conflict) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> Response:
        api = request.url.path.startswith(("/requests", "/audit", "/cases", "/network", "/queue", "/overrides", "/reservations", "/breakit", "/demo", "/health"))
        if exc.status_code == 404 and not api and "text/html" in request.headers.get("accept", ""):
            return HTMLResponse((STATIC_DIR / "404.html").read_text(encoding="utf-8"), status_code=404)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(ExtractionError)
    async def _unreadable(_: Request, exc: ExtractionError) -> JSONResponse:
        return JSONResponse({"detail": f"{exc} Route to manual entry or review.", "missing": exc.missing}, status_code=422)

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse({"detail": f"'{exc.args[0]}' not found."}, status_code=404)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "demo_mode": is_demo, "anchor": engine.anchor_label(), "network_off": engine.network_off}

    market = build_market()

    @app.get("/network")
    async def network() -> dict[str, Any]:
        return market

    @app.get("/cases")
    async def cases() -> dict[str, Any]:
        items = engine.summaries()
        return {"cases": items, "total": len(items), "demo_mode": is_demo, "roles": DEMO_IDENTITIES,
                "anchor": engine.anchor_label(), "eval_summary": eval_summary()}

    @app.post("/requests")
    async def submit(request: Request) -> dict[str, Any]:
        require(request, {"seller", "lender", "reviewer"}, "seller")
        ctype = request.headers.get("content-type", "").lower()
        if "multipart/form-data" in ctype:
            form = await request.form()
            upload = form.get("file")
            case_id = str(form.get("case_id") or "clean_goods")
            raw_attach = form.get("attach")
            attach = None if raw_attach is None else {x for x in str(raw_attach).split(",") if x}
            if upload is None or not hasattr(upload, "read"):
                raise HTTPException(status_code=400, detail="Multipart upload needs a 'file' part.")
            data = await upload.read()
            if len(data) > MAX_PAYLOAD_BYTES:
                raise HTTPException(status_code=413, detail="Uploaded file exceeds the 10 MB limit.")
            if not data.startswith(b"%PDF-"):
                raise HTTPException(status_code=400, detail="Invalid upload: file must begin with %PDF- magic bytes.")
            return engine.submit_upload(case_id, data, attach)
        body = await json_body(request)
        for key in ("file_magic", "file_content"):
            if key in body and not str(body[key]).startswith("%PDF-"):
                raise HTTPException(status_code=400, detail="Invalid upload: file must start with %PDF- magic bytes.")
        if isinstance(body.get("requested_amount_paise"), float):
            raise HTTPException(status_code=422, detail="Floats are not allowed in paise fields.")
        # A fixture case is already run through the real pipeline at startup; return its current card.
        return engine.card(str(body.get("case_id") or body.get("request_id") or "clean_goods"))

    @app.get("/requests/{ident}")
    async def card(ident: str) -> dict[str, Any]:
        return engine.card(ident)

    @app.post("/requests/{ident}/accept")
    async def accept(ident: str, request: Request) -> dict[str, Any]:
        who = require(request, {"buyer"}, "buyer")
        body = await json_body(request)
        # The demo holds the buyer's seeded key and signs on the buyer's behalf; the registry still verifies it.
        card_ = engine.accept(ident, None, str(body.get("decision") or "accept"))
        return {"status": "accepted" if card_["buyer_acceptance"]["decision"] == "accept" else "disputed",
                "request_id": card_["request_id"], "state": card_["decision"]["state"],
                "acceptance": card_["buyer_acceptance"], "card": card_, "actor": who["actor"]}

    @app.post("/reservations/{op}")
    async def reservations(op: str, request: Request) -> dict[str, Any]:
        body = await json_body(request)
        target = str(body.get("request_id") or body.get("case_id") or "clean_goods")
        if op == "concurrent-demo":
            require(request, {"lender", "reviewer", "auditor"}, "lender")
            return engine.race(str(body.get("case_id") or "concurrent_reservation"))
        if op == "confirm":
            who = require(request, {"lender"}, "lender")
            lender = str(body.get("lender_id") or who["actor"] or "L1")
            card_ = engine.confirm(target, lender)
            return {"status": "confirmed", "request_id": card_["request_id"], "state": card_["decision"]["state"],
                    "reservation": card_["reservation"], "card": card_}
        if op == "reserve":
            raise HTTPException(status_code=400, detail="A reservation is created atomically when the request is submitted.")
        raise HTTPException(status_code=400, detail=f"Unsupported reservation operation: {op}")

    @app.get("/queue")
    async def queue(all_cases: bool = False) -> dict[str, Any]:
        items = engine.queue()
        in_review = [i for i in items if i["outcome"] == "review"]
        return {"sort_order": "(tier, exposure_paise)", "queue": items if all_cases else in_review,
                "all_prioritized": items, "total_in_review": len(in_review)}

    @app.post("/overrides")
    async def overrides(request: Request) -> dict[str, Any]:
        who = require(request, {"reviewer"}, "reviewer")
        body = await json_body(request)
        reviewer = str(body.get("reviewer_id") or who["actor"] or "REV-1").strip()
        if not reviewer.startswith("REV"):
            raise HTTPException(status_code=403, detail=f"Invalid reviewer identity '{reviewer}'.")
        second = body.get("second_reviewer_id")
        return engine.override(
            str(body.get("request_id") or body.get("case_id") or "ring_real_firms"),
            reviewer, str(second).strip() if second else None,
            str(body.get("new_outcome") or "conditionally_approved"), str(body.get("reason") or ""),
        )

    @app.get("/audit/verify/{target}")
    async def verify(target: str) -> dict[str, Any]:
        return engine.verify_target(target)

    @app.post("/breakit")
    async def breakit(request: Request) -> dict[str, Any]:
        return engine.breakit(await json_body(request))

    if is_demo:
        # Demo-only: these routes do not exist unless LABYRINTH_DEMO=1.
        @app.post("/demo/tamper")
        async def tamper(request: Request) -> dict[str, Any]:
            body = await json_body(request)
            return engine.tamper(str(body.get("target") or "D-PO-0001"), bool(body.get("reset", False)))

        @app.get("/demo/sample-invoice")
        async def sample_invoice(layout: str = "tabular_gst", variant: str = "clean") -> Response:
            if variant not in {"clean", "bad_arith"}:
                raise HTTPException(status_code=400, detail="variant must be 'clean' or 'bad_arith'.")
            pdf = engine.sample_pdf(layout, variant)
            return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="sample-{layout}-{variant}.pdf"'})

        @app.post("/demo/network-off")
        async def network_off(request: Request) -> dict[str, Any]:
            body = await json_body(request)
            off = bool(body["offline"]) if "offline" in body else (not bool(body["online"]) if "online" in body else not engine.network_off)
            engine.set_network_off(off)
            if not body.get("keep_tamper", False):
                engine.restore_all()
            return {"status": "ok", "rpc_online": not off, "network_off": off}

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/{asset_name}")
    async def root_asset(asset_name: str) -> FileResponse:
        if asset_name in {"app.css", "data.js", "seller.js", "lender.js", "queue.js", "verifier.js", "ring.js", "tour.js", "workspace.js", "landing.js", "network.js"}:
            path = STATIC_DIR / asset_name
            if path.exists():
                return FileResponse(str(path), media_type="text/css" if asset_name.endswith(".css") else "application/javascript")
        raise HTTPException(status_code=404, detail=f"Resource '{asset_name}' not found.")

    return app


app = create_app()

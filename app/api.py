"""HTTP surface — the Tavily-compatible contract (§3 / §8) + health probes.

Error bodies match the contract table exactly:
  400 {"error":"validation"} · 401 {"error":"unauthorized"}
  429 {"error":"rate_limited","retry_after_s":N} · 5xx {"error":"upstream"}
"""
from __future__ import annotations

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

from . import auth, config, fetch_service, scrapling_client, searxng_client
from .models import BulkFetchRequest, FetchRequest, SearchRequest
from .service import run_search

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    """Liveness — does not touch SearXNG / Scrapling."""
    caps = ["web_search"]
    if config.ENABLE_FETCH:
        caps.append("web_fetch")
    return {
        "status": "ok",
        "version": _version(),
        "backends": {
            "search": "searxng",
            "fetch": "scrapling" if scrapling_client.is_configured() else "http-fallback",
        },
        "capabilities": caps,
    }


@router.get("/ready")
async def ready() -> dict:
    """Readiness — process is up and serving."""
    return {"status": "ready"}


@router.post("/search")
async def search(req: SearchRequest, authorization: str | None = Header(default=None)):
    if not auth.is_authorized(authorization):
        return JSONResponse(status_code=401, content={"error": "unauthorized"})

    if not req.query or not req.query.strip():
        return JSONResponse(status_code=400, content={"error": "validation"})

    try:
        resp = await run_search(req)
    except searxng_client.RateLimited as exc:
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limited", "retry_after_s": exc.retry_after_s},
        )
    except searxng_client.UpstreamError:
        return JSONResponse(status_code=502, content={"error": "upstream"})

    # exact response keys: query, answer, results[].{title,url,content,score}
    return JSONResponse(content=resp.model_dump(exclude_none=False))


@router.post("/fetch")
async def fetch(req: FetchRequest, authorization: str | None = Header(default=None)):
    if not auth.is_authorized(authorization):
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    if not config.ENABLE_FETCH:
        return JSONResponse(status_code=404, content={"error": "fetch_disabled"})
    try:
        result = await fetch_service.run_fetch(req)
    except fetch_service.FetchValidationError as exc:
        return JSONResponse(status_code=400, content={"error": "validation", "detail": str(exc)})
    except fetch_service.FetchBackendError as exc:
        return JSONResponse(status_code=502, content={"error": "fetch_failed", "detail": str(exc)})
    except scrapling_client.ScraplingError as exc:
        return JSONResponse(status_code=502, content={"error": "fetch_failed", "detail": str(exc)})
    return JSONResponse(content=result.model_dump(exclude_none=False))


@router.post("/fetch/bulk")
async def fetch_bulk(req: BulkFetchRequest, authorization: str | None = Header(default=None)):
    if not auth.is_authorized(authorization):
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    if not config.ENABLE_FETCH:
        return JSONResponse(status_code=404, content={"error": "fetch_disabled"})
    try:
        resp = await fetch_service.run_bulk_fetch(req)
    except fetch_service.FetchValidationError as exc:
        return JSONResponse(status_code=400, content={"error": "validation", "detail": str(exc)})
    return JSONResponse(content=resp.model_dump(exclude_none=False))


def _version() -> str:
    from . import __version__

    return __version__

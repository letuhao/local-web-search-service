"""Fetch orchestrator — pull a specific page's clean content.

Backed by the Scrapling sidecar (browser / anti-bot), with an in-process
httpx+trafilatura fallback for plain HTTP when no sidecar is configured.

Modes (how hard to try):
  http     — fast HTTP GET (Scrapling `get`, or in-process fallback)
  browser  — real browser, JS rendering (Scrapling `fetch`)
  stealth  — anti-bot / Cloudflare (Scrapling `stealthy_fetch`)
  auto     — http first, escalate to stealth if blocked / content too thin
"""
from __future__ import annotations

import asyncio
from typing import Optional
from urllib.parse import urlparse

import anyio
import httpx

from . import config, extract, scrapling_client
from .models import (
    BulkFetchRequest,
    BulkFetchResponse,
    FetchRequest,
    FetchResult,
)

_VALID_MODES = ("auto", "http", "browser", "stealth")
_VALID_FORMATS = ("markdown", "text", "html")
_FMT_TO_EXTRACTION = {"markdown": "markdown", "text": "text", "html": "html"}
_UA = {"User-Agent": "Mozilla/5.0 (compatible; local-web-search-service/1.0; +fetch)"}


class FetchValidationError(ValueError):
    """Bad request (empty/non-http url, unknown mode/format)."""


class FetchBackendError(RuntimeError):
    """The fetch could not be completed by any available backend."""


def _is_http_url(url: str) -> bool:
    try:
        return urlparse(url).scheme in ("http", "https")
    except ValueError:
        return False


def _cap(text: str, max_chars: int) -> str:
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + " …"
    return text


def _derive_title(content: str) -> str:
    """Best-effort title from the first markdown heading (ATX or setext)."""
    if not content:
        return ""
    lines = content.splitlines()
    for i, ln in enumerate(lines[:6]):
        s = ln.strip()
        if not s:
            continue
        if s.startswith("# "):
            return s[2:].strip()
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if nxt and set(nxt) <= {"=", "-"} and len(nxt) >= 3:
            return s
        break
    return ""


def _common_args(url: str, fmt: str, css: Optional[str]) -> dict:
    args = {"url": url, "extraction_type": _FMT_TO_EXTRACTION.get(fmt, "markdown")}
    if css:
        args["css_selector"] = css
    return args


async def _scrape(url: str, engine: str, fmt: str, css: Optional[str]) -> dict:
    """One Scrapling call for the given engine; returns normalized fields."""
    args = _common_args(url, fmt, css)
    if engine == "http":
        args["timeout"] = int(config.FETCH_TIMEOUT_S)  # get => seconds
        structured = await scrapling_client.call_tool("get", args)
    elif engine == "browser":
        args["network_idle"] = True
        args["timeout"] = int(config.FETCH_TIMEOUT_S * 1000)  # ms
        structured = await scrapling_client.call_tool("fetch", args)
    elif engine == "stealth":
        args["solve_cloudflare"] = True
        args["network_idle"] = True
        args["timeout"] = int(config.FETCH_TIMEOUT_S * 1000)  # ms
        structured = await scrapling_client.call_tool("stealthy_fetch", args)
    else:  # pragma: no cover - guarded by caller
        raise FetchValidationError(f"unknown engine {engine}")
    return scrapling_client.normalize(structured, url)


async def _http_fallback(url: str, fmt: str) -> dict:
    """In-process plain-HTTP fetch + extract (no Scrapling). css unsupported."""
    try:
        async with httpx.AsyncClient(
            timeout=config.FETCH_TIMEOUT_S, follow_redirects=True
        ) as client:
            resp = await client.get(url, headers=_UA)
    except httpx.HTTPError as exc:
        raise FetchBackendError(f"http fetch failed: {exc}") from exc
    content = await anyio.to_thread.run_sync(
        extract.to_format, resp.text, str(resp.url), fmt
    )
    return {"status": resp.status_code, "final_url": str(resp.url), "content": content}


def _is_thin(norm: dict) -> bool:
    status = norm.get("status")
    if status is None or status >= 400:
        return True
    return len((norm.get("content") or "").strip()) < config.FETCH_AUTO_MIN_CHARS


async def _dispatch(url: str, mode: str, fmt: str, css: Optional[str]) -> tuple[dict, str]:
    have_sidecar = scrapling_client.is_configured()

    if mode == "http":
        if have_sidecar:
            return await _scrape(url, "http", fmt, css), "http"
        return await _http_fallback(url, fmt), "http"

    if mode in ("browser", "stealth"):
        if not have_sidecar:
            raise FetchBackendError(
                f"mode={mode} requires the Scrapling sidecar (SCRAPLING_MCP_URL unset)"
            )
        return await _scrape(url, mode, fmt, css), mode

    # auto: http first, escalate to stealth when blocked / thin
    if have_sidecar:
        norm = await _scrape(url, "http", fmt, css)
        if _is_thin(norm):
            try:
                esc = await _scrape(url, "stealth", fmt, css)
                if not _is_thin(esc) or len(esc.get("content") or "") > len(
                    norm.get("content") or ""
                ):
                    return esc, "stealth"
            except scrapling_client.ScraplingError:
                pass
        return norm, "http"
    return await _http_fallback(url, fmt), "http"


async def run_fetch(req: FetchRequest) -> FetchResult:
    url = (req.url or "").strip()
    if not _is_http_url(url):
        raise FetchValidationError("url must be an absolute http(s) URL")

    mode = (req.mode or config.FETCH_DEFAULT_MODE).lower()
    if mode not in _VALID_MODES:
        raise FetchValidationError(f"mode must be one of {_VALID_MODES}")
    fmt = (req.format or config.FETCH_DEFAULT_FORMAT).lower()
    if fmt not in _VALID_FORMATS:
        raise FetchValidationError(f"format must be one of {_VALID_FORMATS}")
    max_chars = req.max_chars if (req.max_chars and req.max_chars > 0) else config.FETCH_MAX_CHARS

    norm, engine = await _dispatch(url, mode, fmt, req.css_selector)
    content = _cap((norm.get("content") or "").strip(), max_chars)
    return FetchResult(
        url=url,
        final_url=norm.get("final_url") or url,
        status=norm.get("status"),
        title=_derive_title(content),
        content=content,
        content_format=fmt,
        length=len(content),
        engine=engine,
    )


async def run_bulk_fetch(req: BulkFetchRequest) -> BulkFetchResponse:
    urls = [u for u in (req.urls or []) if isinstance(u, str) and u.strip()]
    if not urls:
        raise FetchValidationError("urls must be a non-empty list")
    urls = urls[: config.FETCH_BULK_MAX_URLS]

    async def one(u: str) -> FetchResult:
        single = FetchRequest(
            url=u, mode=req.mode, format=req.format,
            max_chars=req.max_chars, css_selector=req.css_selector,
        )
        try:
            return await run_fetch(single)
        except FetchValidationError as exc:
            return FetchResult(url=u, content_format=(req.format or config.FETCH_DEFAULT_FORMAT),
                               error=f"validation: {exc}")
        except Exception as exc:
            return FetchResult(url=u, content_format=(req.format or config.FETCH_DEFAULT_FORMAT),
                               error=f"fetch_failed: {exc}")

    results = await asyncio.gather(*(one(u) for u in urls))
    return BulkFetchResponse(results=list(results))

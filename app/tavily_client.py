"""Tavily client — the paid-but-free-tier FALLBACK search backend.

Used only when SearXNG (the free, scraper-based primary) returns zero results
or errors. SearXNG scrapes public engines that block self-hosted IPs, so its
availability is best-effort; Tavily gives a reliability floor.

Tavily's native response is already our §3 wire shape:
    {"query", "answer", "results": [{"title","url","content","score"}], ...}
so mapping is a filter + cap, not a translation.

Free tier: 1,000 credits/month (basic = 1 credit, advanced = 2).
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

import httpx

from . import config


class TavilyError(RuntimeError):
    """Tavily unreachable, unauthorized, or returned an unusable response."""


class TavilyRateLimited(TavilyError):
    def __init__(self, retry_after_s: int = 1) -> None:
        super().__init__("rate_limited")
        self.retry_after_s = retry_after_s


def is_enabled() -> bool:
    """Fallback is live only when a key is present AND the toggle is on."""
    return bool(config.TAVILY_API_KEY) and config.SEARCH_FALLBACK_ENABLED


def _is_http_url(url: object) -> bool:
    if not isinstance(url, str) or not url:
        return False
    try:
        return urlparse(url).scheme in ("http", "https")
    except ValueError:
        return False


async def raw_search(
    query: str,
    max_results: int,
    search_depth: str = "basic",
    include_answer: bool = False,
) -> dict:
    """POST {TAVILY_BASE_URL}/search. Returns Tavily's parsed JSON."""
    if not is_enabled():
        raise TavilyError("tavily fallback not configured")

    payload = {
        "query": query,
        "max_results": max_results,
        "search_depth": "advanced" if search_depth == "advanced" else "basic",
        "include_answer": bool(include_answer),
    }
    try:
        async with httpx.AsyncClient(timeout=config.TAVILY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{config.TAVILY_BASE_URL}/search",
                json=payload,
                headers={"Authorization": f"Bearer {config.TAVILY_API_KEY}"},
            )
    except httpx.HTTPError as exc:
        raise TavilyError(f"tavily unreachable: {exc}") from exc

    if resp.status_code == 429:
        retry = resp.headers.get("Retry-After")
        try:
            retry_s = int(retry) if retry else 1
        except ValueError:
            retry_s = 1
        raise TavilyRateLimited(retry_s)
    if resp.status_code in (401, 403):
        raise TavilyError("tavily unauthorized — check TAVILY_API_KEY")
    if resp.status_code >= 400:
        raise TavilyError(f"tavily {resp.status_code}")

    try:
        return resp.json()
    except ValueError as exc:
        raise TavilyError("tavily returned non-JSON") from exc


def map_results(data: dict, max_results: int) -> list[dict]:
    """Tavily results[] → contract result dicts (http-only, deduped, capped)."""
    out: list[dict] = []
    seen: set[str] = set()
    for r in data.get("results", []) or []:
        url = r.get("url")
        if not _is_http_url(url) or url in seen:
            continue
        seen.add(url)
        score = r.get("score")
        out.append({
            "title": (r.get("title") or "").strip(),
            "url": url,
            "content": (r.get("content") or "").strip(),
            "score": float(score) if isinstance(score, (int, float)) else None,
        })
        if len(out) >= max_results:
            break
    return out


def extract_answer(data: dict) -> Optional[str]:
    answer = data.get("answer")
    if isinstance(answer, str) and answer.strip():
        return answer.strip()
    return None

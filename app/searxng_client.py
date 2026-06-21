"""Thin SearXNG JSON client + mapping into the §3 response shape.

SearXNG `GET /search?format=json` returns:
  { "query": ..., "results": [{title, url, content, score?, ...}],
    "answers": [...], "infoboxes": [...], ... }

We map results[] 1:1 onto the contract, drop non-http(s) URLs, dedupe by URL,
and derive an optional `answer` from answers/infoboxes.
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from . import config


class UpstreamError(RuntimeError):
    """SearXNG was unreachable or returned a non-2xx the shim cannot map."""


class RateLimited(RuntimeError):
    def __init__(self, retry_after_s: int = 1) -> None:
        super().__init__("rate_limited")
        self.retry_after_s = retry_after_s


_ALLOWED_SCHEMES = ("http", "https")


def _is_http_url(url: Any) -> bool:
    if not isinstance(url, str) or not url:
        return False
    try:
        return urlparse(url).scheme in _ALLOWED_SCHEMES
    except ValueError:
        return False


async def raw_search(query: str, language: Optional[str] = None) -> dict:
    """Call SearXNG and return its parsed JSON. Raises Upstream/RateLimited."""
    params: dict[str, str] = {"q": query, "format": "json"}
    if language and language.lower() != "all":
        params["language"] = language
    if config.SEARXNG_ENGINES:
        params["engines"] = config.SEARXNG_ENGINES

    try:
        async with httpx.AsyncClient(timeout=config.SEARXNG_TIMEOUT_S) as client:
            resp = await client.get(
                f"{config.SEARXNG_URL}/search",
                params=params,
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:  # connect/timeout/etc.
        raise UpstreamError(str(exc)) from exc

    if resp.status_code == 429:
        retry = resp.headers.get("Retry-After")
        try:
            retry_s = int(retry) if retry else 1
        except ValueError:
            retry_s = 1
        raise RateLimited(retry_s)
    if resp.status_code >= 400:
        raise UpstreamError(f"searxng {resp.status_code}")

    try:
        return resp.json()
    except ValueError as exc:
        # SearXNG returns HTML (not JSON) when the json format is not enabled
        # in settings.yml — surface a clear hint.
        raise UpstreamError(
            "searxng did not return JSON — enable `formats: [html, json]` in "
            "searxng settings.yml"
        ) from exc


def map_results(data: dict, max_results: int) -> list[SearchResultItemDict]:
    """Map SearXNG results[] → contract result dicts (http-only, deduped)."""
    out: list[SearchResultItemDict] = []
    seen: set[str] = set()
    for r in data.get("results", []) or []:
        url = r.get("url")
        if not _is_http_url(url) or url in seen:
            continue
        seen.add(url)
        score = r.get("score")
        item: SearchResultItemDict = {
            "title": (r.get("title") or "").strip(),
            "url": url,
            "content": (r.get("content") or "").strip(),
            "score": float(score) if isinstance(score, (int, float)) else None,
        }
        out.append(item)
        if len(out) >= max_results:
            break
    return out


def extract_answer(data: dict) -> Optional[str]:
    """Best-effort synthesized answer from SearXNG answers/infoboxes."""
    answers = data.get("answers") or []
    parts: list[str] = []
    for a in answers:
        if isinstance(a, str):
            parts.append(a)
        elif isinstance(a, dict) and a.get("answer"):
            parts.append(str(a["answer"]))
    if parts:
        return " ".join(parts).strip() or None

    for box in data.get("infoboxes") or []:
        if isinstance(box, dict) and box.get("content"):
            return str(box["content"]).strip() or None
    return None


# Lightweight type alias for readability (plain dicts on the wire).
SearchResultItemDict = dict

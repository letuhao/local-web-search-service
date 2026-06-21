"""Optional full-page content enrichment for search_depth="advanced".

SearXNG returns short search-snippet `content`. When `advanced` depth is
requested we fetch the top-N result pages and replace the snippet with
readable extracted text (trafilatura). This is best-effort: any failure leaves
the original snippet untouched. If trafilatura is not installed the whole step
is a no-op.
"""
from __future__ import annotations

import asyncio
from typing import Optional

import anyio
import httpx

from . import config

try:  # trafilatura is an optional dependency
    import trafilatura  # type: ignore

    _HAVE_TRAFILATURA = True
except Exception:  # pragma: no cover - import guard
    _HAVE_TRAFILATURA = False


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; local-web-search-service/1.0; +extract)"
    )
}


def to_format(html: str, url: str, fmt: str) -> str:
    """Convert raw HTML to the requested fetch format (best-effort).

    Used by the in-process http fallback when no Scrapling sidecar is set.
    `html` => raw page; `markdown`/`text` => trafilatura main-content extract.
    """
    if fmt == "html":
        return html or ""
    if not _HAVE_TRAFILATURA:
        return ""
    out_fmt = "markdown" if fmt == "markdown" else "txt"
    text = trafilatura.extract(
        html, url=url, output_format=out_fmt,
        include_comments=False, include_tables=True,
    )
    return (text or "").strip()


def _extract_sync(html: str, url: str) -> Optional[str]:
    text = trafilatura.extract(
        html, url=url, include_comments=False, include_tables=False
    )
    if not text:
        return None
    text = text.strip()
    if len(text) > config.EXTRACT_MAX_CHARS:
        text = text[: config.EXTRACT_MAX_CHARS].rstrip() + " …"
    return text or None


async def _enrich_one(client: httpx.AsyncClient, item: dict) -> None:
    try:
        resp = await client.get(item["url"], headers=_HEADERS, follow_redirects=True)
        if resp.status_code >= 400 or not resp.text:
            return
        text = await anyio.to_thread.run_sync(_extract_sync, resp.text, item["url"])
        if text:
            item["content"] = text
    except Exception:
        # Best-effort: keep the original snippet on any failure.
        return


async def enrich(results: list[dict]) -> None:
    """Mutate the top-N results in place with extracted full-page text."""
    if not _HAVE_TRAFILATURA or not config.ENABLE_EXTRACT or not results:
        return
    targets = results[: config.EXTRACT_TOP_N]
    async with httpx.AsyncClient(timeout=config.EXTRACT_TIMEOUT_S) as client:
        await asyncio.gather(*(_enrich_one(client, it) for it in targets))

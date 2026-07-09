"""The single search orchestrator shared by the HTTP API and the MCP tool.

Two-tier backend:
  1. SearXNG (PRIMARY)  — free, keyless, self-hosted. Scrapes public engines,
     which block self-hosted IPs, so it is best-effort: it can return zero
     results or error out.
  2. Tavily  (FALLBACK) — used ONLY when SearXNG yields nothing or errors, so a
     research turn never comes back empty. Free tier: 1,000 credits/month.

Fallback decision table (`SEARCH_FALLBACK_ENABLED` + a TAVILY_API_KEY present):

  SearXNG          fallback off        fallback on
  ---------------  ------------------  ----------------------------------------
  results          return them         return them (Tavily never called: $0)
  0 results, ok    return [] (200)     try Tavily; if it fails -> [] (200)
  error            raise (502/429)     try Tavily; if it fails -> raise SearXNG's

Note "0 results" is a VALID 200 per §3 ("nothing found"), so when Tavily also
finds nothing we still return 200 with an empty list — not an error.

Pipeline: clamp → SearXNG → map → [fallback] → (advanced) enrich → answer.
"""
from __future__ import annotations

import logging

from . import config, extract, searxng_client, tavily_client
from .models import SearchRequest, SearchResponse, SearchResultItem

log = logging.getLogger(__name__)


def _clamp_max_results(requested: int | None) -> int:
    if not requested or requested < 1:
        return config.DEFAULT_MAX_RESULTS
    return min(requested, config.MAX_RESULTS_CAP)


async def run_search(req: SearchRequest) -> SearchResponse:
    """Run one web search and return the §3 response.

    Raises searxng_client.UpstreamError / RateLimited when the primary fails
    AND the fallback is unavailable or also fails; callers map those to the
    contract's 5xx / 429.
    """
    query = (req.query or "").strip()
    max_results = _clamp_max_results(req.max_results)
    depth = (req.search_depth or "basic").lower()

    results: list[dict] = []
    answer = None
    provider = "searxng"
    primary_error: Exception | None = None

    # --- 1. Primary: SearXNG -------------------------------------------------
    try:
        data = await searxng_client.raw_search(query, language=req.language)
        results = searxng_client.map_results(data, max_results)
        if req.include_answer:
            answer = searxng_client.extract_answer(data)
    except (searxng_client.UpstreamError, searxng_client.RateLimited) as exc:
        primary_error = exc
        log.warning("searxng failed (%s); considering fallback", exc)

    # --- 2. Fallback: Tavily (only when the primary gave us nothing) ---------
    if not results and tavily_client.is_enabled():
        try:
            tdata = await tavily_client.raw_search(
                query, max_results, search_depth=depth,
                include_answer=bool(req.include_answer),
            )
            results = tavily_client.map_results(tdata, max_results)
            if req.include_answer:
                answer = tavily_client.extract_answer(tdata)
            provider = "tavily"
            primary_error = None  # fallback covered for the primary
            log.info("tavily fallback served %d results", len(results))
        except tavily_client.TavilyError as exc:
            log.warning("tavily fallback also failed: %s", exc)
            # Keep primary_error; if the primary merely found nothing (no
            # error), an empty 200 is the correct, contract-valid answer.

    # Primary errored and nothing rescued it -> surface the primary's error.
    if primary_error is not None:
        raise primary_error

    # --- 3. Advanced enrichment ---------------------------------------------
    # Only for SearXNG (snippet-only). Tavily's own `advanced` depth already
    # returns fuller content, so re-fetching those pages would be wasted work.
    if depth == "advanced" and provider == "searxng":
        await extract.enrich(results)

    return SearchResponse(
        query=query,
        answer=answer,
        results=[SearchResultItem(**r) for r in results],
        provider=provider,
    )

"""The single search orchestrator shared by the HTTP API and the MCP tool.

Pipeline: clamp → SearXNG search → map to §3 shape → (advanced) enrich → answer.
"""
from __future__ import annotations

from . import config, extract, searxng_client
from .models import SearchRequest, SearchResponse, SearchResultItem


def _clamp_max_results(requested: int | None) -> int:
    if not requested or requested < 1:
        return config.DEFAULT_MAX_RESULTS
    return min(requested, config.MAX_RESULTS_CAP)


async def run_search(req: SearchRequest) -> SearchResponse:
    """Run one web search and return the §3 response.

    Raises searxng_client.UpstreamError / RateLimited on backend trouble;
    callers map those to the contract's 5xx / 429.
    """
    query = (req.query or "").strip()
    max_results = _clamp_max_results(req.max_results)

    data = await searxng_client.raw_search(query, language=req.language)
    results = searxng_client.map_results(data, max_results)

    if (req.search_depth or "basic").lower() == "advanced":
        await extract.enrich(results)

    answer = None
    if req.include_answer:
        answer = searxng_client.extract_answer(data)

    return SearchResponse(
        query=query,
        answer=answer,
        results=[SearchResultItem(**r) for r in results],
    )

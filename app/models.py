"""Wire models for the Tavily-compatible contract (§3 / §8).

Request fields are *tolerated* (extra fields ignored, unknown `api_key`
accepted and dropped); response keys are *exact* — `answer`,
`results[].{title,url,content,score}` — because LoreWeave's adapter parses
them by name.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class SearchRequest(BaseModel):
    # Tolerate extra fields a Tavily client might send.
    model_config = ConfigDict(extra="ignore")

    query: str
    max_results: Optional[int] = None
    search_depth: Optional[str] = "basic"
    include_answer: Optional[bool] = False
    # Present only for Tavily wire-compat; this backend is NOT Tavily, so it is
    # ignored (never used for auth — auth is the Bearer header, §2).
    api_key: Optional[str] = None
    # Optional language hint passed through to SearXNG (e.g. "en", "zh", "all").
    language: Optional[str] = None


class SearchResultItem(BaseModel):
    title: str = ""
    url: str
    content: str = ""
    score: Optional[float] = None


class SearchResponse(BaseModel):
    query: str
    answer: Optional[str] = None
    results: List[SearchResultItem] = Field(default_factory=list)


# --- Fetch capability (contract §10) ----------------------------------------

# How hard to try. http=fast HTTP; browser=JS rendering; stealth=anti-bot
# (Cloudflare); auto=http then escalate to stealth on block/thin content.
FetchMode = str  # "auto" | "http" | "browser" | "stealth"
# Output shape of `content`.
FetchFormat = str  # "markdown" | "text" | "html"


class FetchRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str
    mode: Optional[FetchMode] = None
    format: Optional[FetchFormat] = None
    max_chars: Optional[int] = None
    # Optional CSS selector — extract only the matching subtree(s).
    css_selector: Optional[str] = None
    api_key: Optional[str] = None  # ignored (wire-compat, never used for auth)


class FetchResult(BaseModel):
    url: str               # the requested URL (echo)
    final_url: str = ""    # URL after redirects (may differ)
    status: Optional[int] = None
    title: str = ""
    content: str = ""
    content_format: str = "markdown"
    length: int = 0
    engine: str = ""       # which path produced it: http | browser | stealth
    error: Optional[str] = None  # set on per-URL failure (bulk); else null


class FetchResponse(FetchResult):
    """Single-URL fetch — same shape as one bulk item."""


class BulkFetchRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    urls: List[str]
    mode: Optional[FetchMode] = None
    format: Optional[FetchFormat] = None
    max_chars: Optional[int] = None
    css_selector: Optional[str] = None
    api_key: Optional[str] = None


class BulkFetchResponse(BaseModel):
    results: List[FetchResult] = Field(default_factory=list)

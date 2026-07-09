"""Runtime configuration, all from environment variables.

Nothing here is secret-at-rest: the bearer secret is supplied by the operator
(or left empty for a keyless local deployment, per the contract §2).
"""
from __future__ import annotations

import os


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# --- SearXNG backend ---------------------------------------------------------
# URL of the SearXNG instance this shim wraps. On the compose network this is
# the service name; locally it is http://localhost:8080.
SEARXNG_URL: str = os.getenv("SEARXNG_URL", "http://localhost:8080").rstrip("/")
SEARXNG_TIMEOUT_S: float = float(os.getenv("SEARXNG_TIMEOUT_S", "15"))
# Optional comma-separated SearXNG engine list (e.g. "google,bing,duckduckgo").
# Empty => SearXNG's configured defaults.
SEARXNG_ENGINES: str = os.getenv("SEARXNG_ENGINES", "").strip()

# --- Auth (contract §2) ------------------------------------------------------
# Bearer shared secret. Empty => keyless: the service ignores Authorization.
WEB_SEARCH_SECRET: str = os.getenv("WEB_SEARCH_SECRET", "").strip()

# --- Result shaping (contract §3) -------------------------------------------
DEFAULT_MAX_RESULTS: int = 5
MAX_RESULTS_CAP: int = 20

# --- Search fallback: Tavily (contract §4 backend/profile selection) ---------
# SearXNG stays PRIMARY (free, keyless). Tavily is used ONLY when SearXNG
# returns zero results or errors — so a research turn never comes back empty.
# Empty TAVILY_API_KEY disables the fallback entirely (pure-SearXNG mode).
TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "").strip()
TAVILY_BASE_URL: str = os.getenv("TAVILY_BASE_URL", "https://api.tavily.com").rstrip("/")
TAVILY_TIMEOUT_S: float = float(os.getenv("TAVILY_TIMEOUT_S", "20"))
SEARCH_FALLBACK_ENABLED: bool = _flag("SEARCH_FALLBACK_ENABLED", True)

# --- Optional content enrichment (contract §5a) -----------------------------
# On search_depth="advanced", fetch + extract full page text for the top N
# results (richer `content` than SearXNG snippets). Requires trafilatura.
ENABLE_EXTRACT: bool = _flag("ENABLE_EXTRACT", True)
EXTRACT_TOP_N: int = int(os.getenv("EXTRACT_TOP_N", "3"))
EXTRACT_TIMEOUT_S: float = float(os.getenv("EXTRACT_TIMEOUT_S", "8"))
EXTRACT_MAX_CHARS: int = int(os.getenv("EXTRACT_MAX_CHARS", "4000"))

# --- Fetch capability (Scrapling sidecar, contract §10) ---------------------
# MCP URL of the Scrapling sidecar. Empty disables the Scrapling backend (the
# shim then serves `mode=http`/`auto` via an in-process httpx+trafilatura
# fallback, and `mode=browser`/`stealth` return an "unavailable" error).
SCRAPLING_MCP_URL: str = os.getenv("SCRAPLING_MCP_URL", "http://scrapling:8000/mcp").strip()
ENABLE_FETCH: bool = _flag("ENABLE_FETCH", True)
FETCH_DEFAULT_MODE: str = os.getenv("FETCH_DEFAULT_MODE", "auto").strip().lower()
FETCH_DEFAULT_FORMAT: str = os.getenv("FETCH_DEFAULT_FORMAT", "markdown").strip().lower()
FETCH_MAX_CHARS: int = int(os.getenv("FETCH_MAX_CHARS", "8000"))
FETCH_TIMEOUT_S: float = float(os.getenv("FETCH_TIMEOUT_S", "45"))
FETCH_BULK_MAX_URLS: int = int(os.getenv("FETCH_BULK_MAX_URLS", "10"))
# In `auto` mode, escalate from http → stealth when the http result has an
# error status or fewer than this many characters of content.
FETCH_AUTO_MIN_CHARS: int = int(os.getenv("FETCH_AUTO_MIN_CHARS", "200"))

# --- Server ------------------------------------------------------------------
HOST: str = os.getenv("HOST", "0.0.0.0")
# Default host-published port (random 15000–16000 range to avoid clashes).
# Under docker-compose the container still listens on 8090 (PORT is set there);
# this default applies to a direct local `python -m app.main` run.
PORT: int = int(os.getenv("PORT", "15487"))

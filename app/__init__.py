"""local-web-search-service — SearXNG-backed web search.

Exposes the same search over two surfaces:
  * HTTP  — Tavily-compatible `POST /search` (the LoreWeave provider contract).
  * MCP   — Streamable HTTP `web_search` tool at `/mcp` (for Cursor / agents).

Both surfaces call the single orchestrator in `app.service`.
"""

__version__ = "1.0.0"

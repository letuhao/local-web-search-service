"""ASGI entrypoint: one app serving both surfaces.

  HTTP  →  /search, /health, /ready          (LoreWeave provider contract)
  MCP   →  /mcp      (Streamable HTTP tool)   (Cursor / agents)

The MCP session manager must run inside the app lifespan, so we bridge it into
FastAPI's lifespan before mounting the Streamable HTTP app.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import __version__
from .api import router
from .mcp_server import mcp


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start/stop the MCP StreamableHTTP session manager alongside the app.
    async with mcp.session_manager.run():
        yield


app = FastAPI(
    title="local-web-search-service",
    version=__version__,
    summary="SearXNG-backed web search — Tavily-compatible HTTP API + MCP tool.",
    lifespan=lifespan,
)

app.include_router(router)
app.mount("/mcp", mcp.streamable_http_app())


if __name__ == "__main__":
    import uvicorn

    from . import config

    uvicorn.run(app, host=config.HOST, port=config.PORT)

"""MCP client to the Scrapling sidecar (the `pyd4vinci/scrapling` container).

The sidecar speaks MCP Streamable HTTP at `SCRAPLING_MCP_URL`. We call its
fetch tools server-to-server and normalize the structured result.

Scrapling tool result (structuredContent) looks like:
    {"status": 200, "content": ["<markdown>", ""], "url": "https://final/"}
`content` is an array (one entry per matched element when a css_selector hits
several; whole-page => the text in [0]).
"""
from __future__ import annotations

from typing import Any, Optional

from . import config


class ScraplingError(RuntimeError):
    """Sidecar unreachable, disabled, or returned an error result."""


class ScraplingUnavailable(ScraplingError):
    """No SCRAPLING_MCP_URL configured (browser/stealth modes can't run)."""


def is_configured() -> bool:
    return bool(config.SCRAPLING_MCP_URL)


def _join_content(content: Any) -> str:
    if isinstance(content, list):
        return "\n\n".join(s.strip() for s in content if isinstance(s, str) and s.strip())
    if isinstance(content, str):
        return content.strip()
    return ""


def normalize(structured: dict, requested_url: str) -> dict:
    """Scrapling structuredContent → fields used by FetchResult."""
    status = structured.get("status")
    return {
        "status": int(status) if isinstance(status, int) else None,
        "final_url": structured.get("url") or requested_url,
        "content": _join_content(structured.get("content")),
    }


async def call_tool(tool_name: str, arguments: dict) -> dict:
    """Invoke one Scrapling MCP tool; return its structuredContent dict.

    Raises ScraplingUnavailable when no sidecar is configured, ScraplingError
    on connection failure or a tool-level error.
    """
    if not is_configured():
        raise ScraplingUnavailable("SCRAPLING_MCP_URL is not set")

    # Imported lazily so the search-only path never pays the MCP client import.
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    try:
        async with streamablehttp_client(config.SCRAPLING_MCP_URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
    except ScraplingError:
        raise
    except Exception as exc:  # connection / protocol / timeout
        raise ScraplingError(f"scrapling sidecar call failed: {exc}") from exc

    if result.isError:
        msg = ""
        for c in result.content:
            if getattr(c, "type", None) == "text":
                msg = c.text
                break
        raise ScraplingError(f"scrapling tool error: {msg[:300]}")

    structured = result.structuredContent
    if not isinstance(structured, dict):
        # Some tools may only return text content — wrap it.
        text = ""
        for c in result.content:
            if getattr(c, "type", None) == "text":
                text = c.text
                break
        return {"status": None, "content": text, "url": ""}
    return structured

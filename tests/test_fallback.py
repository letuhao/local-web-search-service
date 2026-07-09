"""Tavily fallback tests — every branch of the decision table in service.py.

Both backends are mocked; no network, no credits spent.

    pytest -q tests/test_fallback.py
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.config as config
import app.searxng_client as searxng_client
import app.tavily_client as tavily_client
from app.main import app

client = TestClient(app)

_SEARXNG_HIT = {
    "results": [{"title": "SX", "url": "https://sx.example.com", "content": "sx", "score": 0.9}],
    "answers": ["searxng answer"],
}
_SEARXNG_EMPTY = {"results": [], "answers": []}
_TAVILY_HIT = {
    "query": "q",
    "answer": "tavily answer",
    "results": [
        {"title": "TV", "url": "https://tv.example.com", "content": "tv", "score": 0.8},
        {"title": "bad", "url": "javascript:alert(1)", "content": "x"},
    ],
}


def _searxng(monkeypatch, *, data=None, exc=None):
    async def fake(query, language=None):
        if exc:
            raise exc
        return data
    monkeypatch.setattr(searxng_client, "raw_search", fake)


def _tavily(monkeypatch, *, enabled=True, data=None, exc=None):
    monkeypatch.setattr(tavily_client, "is_enabled", lambda: enabled)

    async def fake(query, max_results, search_depth="basic", include_answer=False):
        if exc:
            raise exc
        return data
    monkeypatch.setattr(tavily_client, "raw_search", fake)


# --- Row 1: SearXNG has results -> Tavily must NOT be called (costs nothing) --

def test_searxng_hit_does_not_call_tavily(monkeypatch):
    _searxng(monkeypatch, data=_SEARXNG_HIT)
    called = {"n": 0}

    async def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("tavily must not be called when searxng has results")

    monkeypatch.setattr(tavily_client, "is_enabled", lambda: True)
    monkeypatch.setattr(tavily_client, "raw_search", boom)

    r = client.post("/search", json={"query": "q", "include_answer": True})
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "searxng"
    assert body["results"][0]["url"] == "https://sx.example.com"
    assert called["n"] == 0


# --- Row 2: SearXNG 0 results (no error) ------------------------------------

def test_zero_results_falls_back_to_tavily(monkeypatch):
    _searxng(monkeypatch, data=_SEARXNG_EMPTY)
    _tavily(monkeypatch, data=_TAVILY_HIT)

    r = client.post("/search", json={"query": "q", "include_answer": True})
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "tavily"
    assert body["answer"] == "tavily answer"
    # non-http(s) url dropped by the tavily mapper too
    assert [x["url"] for x in body["results"]] == ["https://tv.example.com"]


def test_zero_results_fallback_disabled_returns_empty_200(monkeypatch):
    _searxng(monkeypatch, data=_SEARXNG_EMPTY)
    _tavily(monkeypatch, enabled=False)

    r = client.post("/search", json={"query": "q"})
    assert r.status_code == 200
    assert r.json()["results"] == []
    assert r.json()["provider"] == "searxng"


def test_zero_results_and_tavily_fails_returns_empty_200(monkeypatch):
    # "nothing found" is a valid 200 per §3 — not an error.
    _searxng(monkeypatch, data=_SEARXNG_EMPTY)
    _tavily(monkeypatch, exc=tavily_client.TavilyError("down"))

    r = client.post("/search", json={"query": "q"})
    assert r.status_code == 200
    assert r.json()["results"] == []


# --- Row 3: SearXNG errors ---------------------------------------------------

def test_searxng_error_rescued_by_tavily(monkeypatch):
    _searxng(monkeypatch, exc=searxng_client.UpstreamError("searxng down"))
    _tavily(monkeypatch, data=_TAVILY_HIT)

    r = client.post("/search", json={"query": "q"})
    assert r.status_code == 200
    assert r.json()["provider"] == "tavily"


def test_searxng_error_and_tavily_fails_surfaces_primary_error(monkeypatch):
    _searxng(monkeypatch, exc=searxng_client.UpstreamError("searxng down"))
    _tavily(monkeypatch, exc=tavily_client.TavilyError("tavily down"))

    r = client.post("/search", json={"query": "q"})
    assert r.status_code == 502
    assert r.json() == {"error": "upstream"}


def test_searxng_ratelimit_and_no_fallback_surfaces_429(monkeypatch):
    _searxng(monkeypatch, exc=searxng_client.RateLimited(7))
    _tavily(monkeypatch, enabled=False)

    r = client.post("/search", json={"query": "q"})
    assert r.status_code == 429
    assert r.json()["retry_after_s"] == 7


# --- is_enabled gating -------------------------------------------------------

def test_is_enabled_requires_key_and_toggle(monkeypatch):
    monkeypatch.setattr(config, "TAVILY_API_KEY", "")
    monkeypatch.setattr(config, "SEARCH_FALLBACK_ENABLED", True)
    assert tavily_client.is_enabled() is False

    monkeypatch.setattr(config, "TAVILY_API_KEY", "tvly-x")
    monkeypatch.setattr(config, "SEARCH_FALLBACK_ENABLED", False)
    assert tavily_client.is_enabled() is False

    monkeypatch.setattr(config, "SEARCH_FALLBACK_ENABLED", True)
    assert tavily_client.is_enabled() is True


def test_health_reports_fallback(monkeypatch):
    monkeypatch.setattr(tavily_client, "is_enabled", lambda: True)
    assert client.get("/health").json()["backends"]["search_fallback"] == "tavily"

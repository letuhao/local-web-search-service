"""Contract tests (§3/§8) — run without a live SearXNG by patching raw_search.

    pip install -r requirements.txt pytest
    pytest -q
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

import app.searxng_client as searxng_client
from app.main import app

client = TestClient(app)

_FAKE_SEARXNG = {
    "query": "nezha",
    "results": [
        {"title": "Nezha — Wikipedia", "url": "https://en.wikipedia.org/wiki/Nezha",
         "content": "Nezha is a protection deity ...", "score": 0.95},
        {"title": "FSYY", "url": "https://example.org/fsyy",
         "content": "Nezha appears ...", "score": 0.81},
        {"title": "bad scheme", "url": "javascript:alert(1)", "content": "x"},
        {"title": "dupe", "url": "https://en.wikipedia.org/wiki/Nezha", "content": "dupe"},
    ],
    "answers": ["Nezha is a protection deity in Chinese mythology."],
}


@pytest.fixture(autouse=True)
def _patch_searxng(monkeypatch):
    async def fake_raw_search(query, language=None):
        return _FAKE_SEARXNG
    monkeypatch.setattr(searxng_client, "raw_search", fake_raw_search)
    # service.py imported the module, so patching the attribute is enough.


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ready():
    assert client.get("/ready").status_code == 200


def test_search_shape_and_url_safety():
    r = client.post("/search", json={"query": "nezha", "max_results": 5,
                                     "include_answer": True})
    assert r.status_code == 200
    body = r.json()
    # exact response keys
    assert set(body) >= {"query", "answer", "results"}
    urls = [it["url"] for it in body["results"]]
    # non-http(s) dropped, duplicate deduped
    assert "javascript:alert(1)" not in urls
    assert urls == ["https://en.wikipedia.org/wiki/Nezha", "https://example.org/fsyy"]
    first = body["results"][0]
    assert set(first) == {"title", "url", "content", "score"}
    assert body["answer"].startswith("Nezha is a protection deity")


def test_max_results_clamped():
    r = client.post("/search", json={"query": "x", "max_results": 999})
    assert r.status_code == 200
    assert len(r.json()["results"]) <= 20


def test_empty_query_is_validation_error():
    r = client.post("/search", json={"query": "   "})
    assert r.status_code == 400
    assert r.json() == {"error": "validation"}


def test_unknown_api_key_field_ignored():
    # Tavily wire-compat: an unknown api_key body field must not break anything.
    r = client.post("/search", json={"query": "x", "api_key": "whatever"})
    assert r.status_code == 200


def test_auth_required_when_secret_set(monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "WEB_SEARCH_SECRET", "s3cret")
    # 401 without bearer
    assert client.post("/search", json={"query": "x"}).status_code == 401
    # 200 with correct bearer
    r = client.post("/search", json={"query": "x"},
                    headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200


def test_upstream_error_maps_to_502(monkeypatch):
    async def boom(query, language=None):
        raise searxng_client.UpstreamError("searxng down")
    monkeypatch.setattr(searxng_client, "raw_search", boom)
    r = client.post("/search", json={"query": "x"})
    assert r.status_code == 502
    assert r.json() == {"error": "upstream"}


def test_rate_limited_maps_to_429(monkeypatch):
    async def limited(query, language=None):
        raise searxng_client.RateLimited(7)
    monkeypatch.setattr(searxng_client, "raw_search", limited)
    r = client.post("/search", json={"query": "x"})
    assert r.status_code == 429
    body = r.json()
    assert body["error"] == "rate_limited" and body["retry_after_s"] == 7

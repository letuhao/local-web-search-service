"""Fetch capability tests (§10) — Scrapling sidecar mocked.

    pytest -q tests/test_fetch.py
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.fetch_service as fetch_service
import app.scrapling_client as scrapling_client
from app.main import app

client = TestClient(app)


def _structured(status, text, url="https://example.com/"):
    return {"status": status, "content": [text, ""], "url": url}


@pytest.fixture
def sidecar(monkeypatch):
    """Pretend a Scrapling sidecar is configured; record tool calls."""
    calls = []

    async def fake_call_tool(tool_name, arguments):
        calls.append((tool_name, arguments))
        if tool_name == "get":
            return _structured(200, "# Example\n\n" + "body text " * 50)
        if tool_name == "stealthy_fetch":
            return _structured(200, "# Stealth\n\n" + "unblocked " * 80)
        return _structured(200, "other")

    monkeypatch.setattr(scrapling_client, "is_configured", lambda: True)
    monkeypatch.setattr(scrapling_client, "call_tool", fake_call_tool)
    return calls


def test_fetch_http_via_sidecar(sidecar):
    r = client.post("/fetch", json={"url": "https://example.com", "mode": "http"})
    assert r.status_code == 200
    body = r.json()
    assert body["engine"] == "http"
    assert body["status"] == 200
    assert body["title"] == "Example"
    assert body["content_format"] == "markdown"
    assert body["length"] > 0
    assert sidecar[0][0] == "get"


def test_fetch_auto_escalates_to_stealth(monkeypatch):
    # http result is blocked (403) -> auto must escalate to stealth.
    async def fake_call_tool(tool_name, arguments):
        if tool_name == "get":
            return _structured(403, "")
        if tool_name == "stealthy_fetch":
            return _structured(200, "recovered content " * 40)
        return _structured(200, "x")

    monkeypatch.setattr(scrapling_client, "is_configured", lambda: True)
    monkeypatch.setattr(scrapling_client, "call_tool", fake_call_tool)

    r = client.post("/fetch", json={"url": "https://example.com", "mode": "auto"})
    assert r.status_code == 200
    assert r.json()["engine"] == "stealth"


def test_fetch_max_chars_caps_content(sidecar):
    r = client.post("/fetch", json={"url": "https://example.com", "mode": "http", "max_chars": 20})
    assert r.status_code == 200
    assert r.json()["length"] <= 22  # 20 + " …"


def test_fetch_bad_url_is_validation_error(sidecar):
    r = client.post("/fetch", json={"url": "ftp://nope"})
    assert r.status_code == 400
    assert r.json()["error"] == "validation"


def test_browser_mode_without_sidecar_502(monkeypatch):
    monkeypatch.setattr(scrapling_client, "is_configured", lambda: False)
    r = client.post("/fetch", json={"url": "https://example.com", "mode": "browser"})
    assert r.status_code == 502
    assert r.json()["error"] == "fetch_failed"


def test_http_fallback_when_no_sidecar(monkeypatch):
    monkeypatch.setattr(scrapling_client, "is_configured", lambda: False)

    async def fake_fallback(url, fmt):
        return {"status": 200, "final_url": url, "content": "fallback md " * 30}

    monkeypatch.setattr(fetch_service, "_http_fallback", fake_fallback)
    r = client.post("/fetch", json={"url": "https://example.com", "mode": "http"})
    assert r.status_code == 200
    assert r.json()["engine"] == "http"
    assert r.json()["status"] == 200


def test_bulk_fetch_mixed(sidecar):
    r = client.post("/fetch/bulk", json={
        "urls": ["https://a.example.com", "not-a-url", "https://b.example.com"],
        "mode": "http",
    })
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 3
    errs = [x for x in results if x["error"]]
    oks = [x for x in results if not x["error"]]
    assert len(errs) == 1 and "validation" in errs[0]["error"]
    assert len(oks) == 2


def test_auth_required_for_fetch_when_secret_set(monkeypatch, sidecar):
    import app.config as config
    monkeypatch.setattr(config, "WEB_SEARCH_SECRET", "s3cret")
    assert client.post("/fetch", json={"url": "https://x.example.com"}).status_code == 401
    r = client.post("/fetch", json={"url": "https://x.example.com", "mode": "http"},
                    headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200


def test_health_advertises_fetch_capability():
    body = client.get("/health").json()
    assert "web_fetch" in body["capabilities"]
    assert "fetch" in body["backends"]

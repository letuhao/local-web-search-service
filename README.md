# local-web-search-service

Self-hosted **web search + page fetch** for LoreWeave's glossary deep-research
feature:
- **Search** — **[SearXNG](https://github.com/searxng/searxng)** primary (OSS
  metasearch — no API key, no per-query cost), with an optional
  **[Tavily](https://tavily.com)** fallback so a research turn never comes back
  empty. See [Search reliability](#search-reliability-two-tier-backend).
- **Fetch** — backed by a **[Scrapling](https://scrapling.readthedocs.io/)
  sidecar** (browser rendering + anti-bot), with an in-process HTTP fallback.

Capabilities are exposed over **two surfaces**, both calling the same
orchestrators:

| Capability | HTTP | MCP tool |
|---|---|---|
| Search | `POST /search` (Tavily-compatible) | `web_search` |
| Fetch one page | `POST /fetch` | `fetch_page` |
| Fetch many pages | `POST /fetch/bulk` | `fetch_pages` |

The HTTP `POST /search` is the LoreWeave contract; the MCP surface is a bonus
for direct agent (Cursor/Claude) use. LoreWeave never uses MCP — it calls
`POST /search`. (A `web_fetch` LoreWeave consumer is not built yet — fetch is
used directly via HTTP/MCP for now.)

> Contracts implemented: **§3/§8** (search) and **§10** (fetch) of
> `lore-weave-security/docs/04_integration/2026-06-21-web-search-service-integration.md`.

---

## Architecture

```
LoreWeave ──BYOK──▶ provider-registry ──POST /search──▶┐
                                                        ├─▶ web-search shim (this repo)
Cursor/agent ── MCP /mcp · HTTP /search /fetch ────────▶┘     │
                                                              ├─▶ SearXNG       (search; always)
                                                              ├─▶ trafilatura   (search advanced-extract)
                                                              └─▶ Scrapling MCP (fetch: browser/stealth)
                                                                    └ http fallback when sidecar absent
```

- `app/service.py` — search orchestrator: clamp → SearXNG → map → (advanced) enrich → answer.
- `app/searxng_client.py` — SearXNG JSON client + mapping to the §3 shape (http-only URLs, deduped).
- `app/fetch_service.py` — fetch orchestrator: mode dispatch (http/browser/stealth/auto) + auto-escalation.
- `app/scrapling_client.py` — MCP client to the Scrapling sidecar.
- `app/api.py` — `POST /search`, `POST /fetch`, `POST /fetch/bulk`, `GET /health`, `GET /ready`.
- `app/mcp_server.py` — FastMCP tools: `web_search`, `fetch_page`, `fetch_pages` (Streamable HTTP).
- `app/main.py` — combines everything into one ASGI app (HTTP routes + mounted `/mcp`).

---

## Run

### Docker (recommended — ships SearXNG too)

```powershell
copy .env.example .env      # optional: set WEB_SEARCH_SECRET, etc.
docker compose up -d --build
```

- Shim:    `http://localhost:15487`  (`/search`, `/health`, `/ready`, `/mcp`)
- SearXNG: internal to the compose network (publish port 8080 only for debugging).

### Local (Python) — SearXNG elsewhere

```powershell
pip install -r requirements.txt
$env:SEARXNG_URL = "http://localhost:8080"   # a SearXNG with JSON format enabled
uvicorn app.main:app --host 0.0.0.0 --port 15487
```

---

## Try it

```powershell
.\scripts\smoke.ps1                              # health + a sample /search
```

```bash
curl -X POST http://localhost:15487/search \
  -H "Content-Type: application/json" \
  -d '{"query":"Nezha 哪吒 deity","max_results":5,"include_answer":true}'
```

Response (exact keys LoreWeave parses):

```json
{
  "query": "Nezha 哪吒 deity",
  "answer": "Nezha is a protection deity ...",
  "results": [
    { "title": "Nezha — Wikipedia", "url": "https://en.wikipedia.org/wiki/Nezha",
      "content": "Nezha is a protection deity ...", "score": 0.95 }
  ]
}
```

### Fetch a specific page

```bash
# auto = fast HTTP first, escalate to stealth (Cloudflare/anti-bot) if blocked
curl -X POST http://localhost:15487/fetch \
  -H "Content-Type: application/json" \
  -d '{"url":"https://en.wikipedia.org/wiki/Nezha","mode":"auto","format":"markdown","max_chars":8000}'
```

```json
{
  "url": "https://en.wikipedia.org/wiki/Nezha",
  "final_url": "https://en.wikipedia.org/wiki/Nezha",
  "status": 200, "title": "Nezha", "content": "# Nezha\n\n...",
  "content_format": "markdown", "length": 5234, "engine": "http", "error": null
}
```

- `mode`: `http` (fast) · `browser` (JS render) · `stealth` (anti-bot) · `auto` (default).
- `format`: `markdown` (default) · `text` · `html`.
- Bulk: `POST /fetch/bulk` with `{"urls":[...]}` → `{"results":[...]}` (per-URL `error` isolation, capped at 10).

### MCP client config

```json
{
  "mcpServers": {
    "local-web-search": { "type": "http", "url": "http://localhost:15487/mcp" }
  }
}
```

Tools:
- `web_search(query, max_results=5, search_depth="basic", include_answer=false)`
- `fetch_page(url, mode="auto", format="markdown", max_chars=8000, css_selector=None)`
- `fetch_pages(urls, mode="auto", format="markdown", max_chars=8000, css_selector=None)`

---

## Configuration (env)

| Var | Default | Meaning |
|---|---|---|
| `WEB_SEARCH_SECRET` | _(empty)_ | Bearer required on `/search`. Empty ⇒ **keyless** (Authorization ignored). |
| `SEARXNG_URL` | `http://localhost:8080` | SearXNG base URL (compose: `http://searxng:8080`). |
| `SEARXNG_ENGINES` | _(empty)_ | Comma-separated engine subset; empty ⇒ SearXNG defaults. |
| `SEARXNG_TIMEOUT_S` | `15` | SearXNG request timeout. |
| `TAVILY_API_KEY` | _(empty)_ | Enables the Tavily fallback. Empty ⇒ pure-SearXNG mode. |
| `SEARCH_FALLBACK_ENABLED` | `true` | Master toggle for the fallback (needs a key too). |
| `TAVILY_TIMEOUT_S` | `20` | Tavily request timeout. |
| `ENABLE_EXTRACT` | `true` | Full-page extract on `search_depth="advanced"`. |
| `EXTRACT_TOP_N` | `3` | How many top results to extract. |
| `EXTRACT_MAX_CHARS` | `4000` | Cap on extracted `content` length. |
| `ENABLE_FETCH` | `true` | Enable the `/fetch` + `/fetch/bulk` API and `fetch_*` MCP tools. |
| `SCRAPLING_MCP_URL` | `http://scrapling:8000/mcp` | Scrapling sidecar MCP URL. **Empty ⇒ disable Scrapling** (http/auto via in-process fallback; browser/stealth → 502). |
| `FETCH_DEFAULT_MODE` | `auto` | Default fetch mode. |
| `FETCH_MAX_CHARS` | `8000` | Default cap on fetched `content`. |
| `FETCH_BULK_MAX_URLS` | `10` | Max URLs per `/fetch/bulk`. |
| `PORT` | `15487` | Host-published port (container-internal stays 8090 under compose). |

**SearXNG must have `formats: [html, json]`** (set in `searxng/settings.yml`) —
the shim calls `/search?format=json`. Without it you get an `upstream` error
with a clear hint.

---

## Register in LoreWeave (BYOK `web_search`)

1. **Provider credential** — `provider_kind = web_search`,
   `endpoint_base_url = http://<host>:15487`, `secret` = `WEB_SEARCH_SECRET`
   (empty for keyless).
2. **User model** — `provider_model_name = searxng-default`,
   `capability_flags = {"web_search": true}` (strict),
   `pricing = {"input_per_mtok":0,"output_per_mtok":0}`.
3. `is_active = true` (+ `is_favorite` to make it the preferred web_search model).

---

## Test

```powershell
pip install -r requirements.txt pytest
pytest -q          # contract tests, no live SearXNG needed (SearXNG is mocked)
```

---

## Search reliability: two-tier backend

**SearXNG is a metasearch _scraper_, not its own index.** It proxies Google/
Bing/Brave/etc., and those engines actively CAPTCHA and rate-limit self-hosted
IPs. It is self-hosted but **not self-sufficient** — that is inherent, not a bug
in this service.

Two things make it reliable here:

**1. A wide engine pool (`searxng/settings.yml`).** SearXNG's stock config
enables only **four** real web engines (`brave`, `duckduckgo`, `google`,
`startpage`) — and all four block self-hosted instances. When they die at once
you get **zero results**. We enable ~10 engines instead, including `mojeek` and
`mwmbl` (independent crawlers that don't block servers) plus `bing`, `yahoo`,
`qwant`, `yep`, `presearch`, `duckduckgo web`. Measured on the same 8-query
benchmark: **2/8 → 8/8**, and 10/10 under a rapid burst.

**2. A Tavily fallback (optional).** Even a wide pool is best-effort. If SearXNG
returns **zero results or errors**, the shim calls Tavily so the turn still
succeeds. The response's `provider` field says which backend served it.

```
SearXNG has results  →  return them          (Tavily never called ⇒ $0)
SearXNG 0 results    →  try Tavily; still nothing ⇒ empty 200 (valid "not found")
SearXNG errors       →  try Tavily; if it also fails ⇒ surface SearXNG's 502/429
```

Set `TAVILY_API_KEY` in `.env` to enable (free tier: **1,000 credits/month**,
basic search = 1 credit). Leave it empty for pure-SearXNG mode. Because the
fallback only fires when SearXNG comes up empty, normal operation costs nothing.

> Note: Brave's Search API **removed its free tier in Feb 2026** (now ~$5/1k
> queries), which is why Tavily is the fallback of choice here.

---

## Troubleshooting: ERROR lines in the `searxng` container logs

Seeing things like `SearxEngineCaptchaException`, `google ... IndexError`,
`Too many request (suspended_time=180)`, or `engine timeout`? **That's normal
and not a fault in this service.** They come from SearXNG's per-engine scrapers,
not the shim (the `web-search` container logs stay clean).

- Public engines (Google/DuckDuckGo/Brave/…) routinely block, CAPTCHA, or
  rate-limit a single server IP. SearXNG logs each failed engine at ERROR.
- SearXNG queries **many** engines and merges whatever succeeds, so a few
  engines failing still returns a full result set — that's the resilience.
- **If you get zero results, the cause is almost always too few enabled
  engines**, not "SearXNG is broken." The stock config enables only four real
  web engines and all four block self-hosted IPs. See
  [Search reliability](#search-reliability-two-tier-backend) — `searxng/settings.yml`
  widens the pool. Removing engines to quiet the logs makes this *worse*.
- Tor engines (`ahmia`, `torch`) are removed outright: they need a Tor proxy we
  don't run, so they only fail to load and never return anything.
- Bursty testing trips rate limits (engines auto-suspend ~180s then recover).
  At normal deep-research volume you'll rarely see them.

## Notes

- **Returned text is untrusted.** LoreWeave neutralizes it (INV-6) regardless;
  this service returns clean text but is never trusted.
- **Stateless** — no GPU, no model lifecycle (unlike rerank/STT/TTS).
- **Graceful** — backend down/slow ⇒ `502 {"error":"upstream"}` /
  `429 {"error":"rate_limited"}`; LoreWeave degrades the research turn to
  "search unavailable" and never blocks the chat.

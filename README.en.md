# Cuba USD Rate API

[🇪🇸 Español](README.md) · [🇬🇧 English](#)

Free JSON API for the informal (street) and official bank rates of **all 16 currencies elTOQUE publishes** (USD, EUR, MLC, CAD, MXN, ZELLE, CLA, GBP, CHF, DKK, JPY, NOK, SEK, AUD, CNY, PAB), scraped from [elTOQUE](https://eltoque.com/tasas-de-cambio-cuba) via [Firecrawl](https://www.firecrawl.dev) (needed because the site sits behind a Cloudflare challenge that a plain `curl`/`requests` can't pass).

**🔴 Live API:** **[cubausdrateapi.5-l.cc](https://cubausdrateapi.5-l.cc)**

Live demo landing page — Spanish by default (with an English toggle), light/dark theme (dark is true black), mobile-responsive, shows the current rate with a count-up animation and a table of all 16 currencies, and includes copy-pasteable code snippets.

It's also an **[MCP](https://modelcontextprotocol.io) server** (`/mcp/`), so agents like Claude can query rates as tools instead of raw HTTP — see [Connect via MCP](#connect-via-mcp) below.

---

## How it works

- A background scheduler scrapes elTOQUE's pages through Firecrawl (the 7 currency pages that carry an informal rate + the shared `mercado-formal` page with every bank table), **sequentially**, every `SCRAPE_INTERVAL_MINUTES` (480 = 8h by default), and caches the parsed result to `data/cache.json`. It's sequential rather than parallel because Firecrawl returns 429 (rate limited) if you fire several scrapes at once; each call retries automatically with backoff when that happens.
- API requests **never** trigger a live scrape — they just read the cache, so responses are instant and don't burn Firecrawl credits.
- Every response includes `requested_at`, computed at the exact moment the request hits the server — **not** the time the data was scraped (that's `data.scraped_at` / `generated_at`).
- Not every currency has both markets: MLC, ZELLE (Zelle balance) and CLA (classic card balance) only trade informally (no bank counter for them); GBP, CHF, DKK, JPY, NOK, SEK, AUD, CNY, PAB only have an official bank rate (some, like AUD, CNY and PAB, are only quoted by BANDEC) (elTOQUE doesn't track a street rate for those).
- If a page fails or elTOQUE serves its maintenance page, that part (informal or formal) keeps its last-known-good value instead of turning into `null`.

### Firecrawl credits math

Firecrawl's free tier gives **1000 credits/month**, and a plain `/v2/scrape` call costs **1 credit**. Each refresh cycle scrapes **8 pages** (7 currency pages + 1 formal-market page) to cover all 16 currencies, so it costs 8 credits per cycle.

| Interval | Scrapes/day | Credits/month | Fits in 1000? |
| --- | --- | --- | --- |
| 60 min | 8 × 24 = 192 | ~5,760 | No |
| 240 min (4h) | 8 × 6 = 48 | ~1,440 | No |
| 360 min (6h) | 8 × 4 = 32 | ~960 | Just barely (~40 credits of buffer) |
| **480 min / 8h (default)** | **8 × 3 = 24** | **~720** | **Yes, ~280 credits of buffer** |
| 720 min (12h) | 8 × 2 = 16 | ~480 | Yes, plenty of buffer |

The default (every 8h) leaves headroom for manual testing, restarts, and the immediate scrape that runs on every app startup.

---

## Quickstart (Docker Compose)

```bash
git clone https://github.com/dotcom350/El-Toke-USD-Scraper.git
cd El-Toke-USD-Scraper
cp .env.example .env
# edit .env and set FIRECRAWL_API_KEY=fc-...
docker compose up -d --build
```

Then open `http://localhost:8000/` for the landing page, or call the API directly:

```bash
curl http://localhost:8000/api/v2/rates
```

---

## Endpoints

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/v2/rates` | All 16 currencies, in elTOQUE's own order |
| `GET` | `/api/v2/rates/{code}` | A single currency by code (`USD`, `EUR`, `MLC`, `CAD`, `MXN`, `ZELLE`, `CLA`, `GBP`, `CHF`, `DKK`, `JPY`, `NOK`, `SEK`, `AUD`, `CNY`, `PAB`) |
| `GET` | `/api/v2/history/{code}` | Real history for one currency: one point per scrape (`informal_cup` + the `formal` bank table), oldest first. Optional `?days=N`. Stored in Postgres and `data/history/`; also works without Postgres. The landing page renders a line chart, including a single point |
| `GET` | `/api/v1/dolar` | USD only, in the original response shape (kept for backward compatibility) |
| `GET` | `/api/v1/health` | Service status and last successful scrape time |
| `ANY` | `/mcp/` | MCP server (Streamable HTTP) - see below |
| `GET` | `/docs` | Interactive Swagger / OpenAPI docs |
| `GET` | `/` | Bilingual landing page |

Days with no scrape of our own (before the API started running, or while it was down) can be filled with real daily values, e.g. elTOQUE's daily median. They live in `data/history/<CODE>.daily.json` and the API returns them with `"source": "daily"` (scraper points carry `"source": "scrape"` and always win for their day). To import a `[{"date": "2026-08-10", "price": 665.84}, ...]` file:

```bash
python -m app.daily_history import USD path/to/file.json
```

The landing chart can be dragged to pan, zoomed with the wheel, a pinch or the −/+ buttons, and double-click resets the view.

## Connect via MCP

The API exposes an [MCP](https://modelcontextprotocol.io) server at `/mcp/` with 3 tools: `list_currencies`, `get_all_rates` and `get_rate(code)`. Same as the REST API, the tools only read the cache - they never trigger a live scrape. No API key.

With [Claude Code](https://claude.com/claude-code):

```bash
claude mcp add --transport http cuba-rates http://localhost:8000/mcp/
```

Or in any MCP client's JSON config:

```json
{
  "mcpServers": {
    "cuba-rates": {
      "type": "http",
      "url": "http://localhost:8000/mcp/"
    }
  }
}
```

> Self-hosted on whatever domain/port you pick, so MCP's DNS-rebinding protection ships disabled by default (the data served is public, read-only, side-effect-free). If you deploy on a fixed domain and want that protection back, configure it in `app/mcp_server.py` (`TransportSecuritySettings`).

### Example response — `/api/v2/rates/EUR`

This is a real response, captured live from the running API:

```json
{
  "success": true,
  "requested_at": "2026-08-16T02:20:09.819429+00:00",
  "interval_minutes": 480,
  "data": {
    "code": "EUR",
    "name_es": "Euro",
    "name_en": "Euro",
    "scraped_at": "2026-08-16T02:13:01.463799+00:00",
    "source_url": "https://eltoque.com/tasas-de-cambio-cuba/euro",
    "source_updated_label": "15 de agosto de 2026 a las 08:03 a. m.",
    "informal": {
      "cup": 770.0,
      "conversions": { "USD": 1.16, "MLC": 1.63 },
      "change_vs_yesterday_cup": 0.0
    },
    "formal": {
      "BPA": { "compra": 694.37, "venta": 722.71 },
      "BANDEC": { "compra": 663.35, "venta": 690.43 },
      "BANMET Efectivo": { "compra": 692.44, "venta": 720.70 },
      "BANMET Transferencia": { "compra": 706.57, "venta": 717.17 },
      "CADECA Casas de cambio": { "compra": 669.53, "venta": 696.31 },
      "CADECA Hoteles y Aeropuertos": { "compra": 687.29, "venta": 751.05 }
    }
  }
}
```

`informal` is `null` for currencies with no informal market (GBP, CHF, DKK, JPY, NOK, SEK, AUD, CNY, PAB); `formal` is `null` for currencies with no bank counter (MLC, ZELLE, CLA).

`/api/v1/dolar` keeps the original response shape (`informal.usd_to_cup`, `usd_to_mlc`, `usd_to_eur`) so existing consumers don't break — it reads from the same shared cache internally rather than triggering a separate scrape.

---

## qvai — AI Telegram bot + admin panel

The project ships with **qvai**, a conversational Telegram bot that answers questions about the exchange rates (using the same live cache as the API) and chats about anything else, powered by [NVIDIA NIM](https://build.nvidia.com) (OpenAI SDK-compatible). Every message is logged to Postgres, and everything is managed from a web panel at `/admin` — no code changes or redeploys needed.

**From the admin panel you can:**
- View **every conversation** the bot has had (chat list + full thread per chat).
- Edit the **bot's behavior** (name, personality/system prompt, model, temperature, max tokens) live.
- Edit the **Telegram token** and **NVIDIA API key** — the bot restarts itself on save.
- Turn the bot on/off.

### Turning it on

1. Create a bot on Telegram via [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token it gives you.
2. Get a free API key at [build.nvidia.com](https://build.nvidia.com) (the default model is `nvidia/nemotron-3.5-lightning-30b-a3b`, changeable in the panel).
3. Bring the stack up with `docker compose up -d --build` (already includes Postgres, see Quickstart above).
4. Go to `http://localhost:8000/admin` (or your domain), log in with `ADMIN_PASSWORD`, open **Settings**, paste the Telegram token and NVIDIA API key, and save. The bot starts itself.

The default system prompt already tells it to reply in Spanish, use the live rates (via a `{RATES}` placeholder), and occasionally mention it's an **ID Academy** (impactodigital.vip) project — all of this is editable from the panel.

> The bot uses long polling (no webhook), so changing the token from the panel just restarts the polling loop — nothing to re-register with Telegram.

---

## Configuration

Environment variables (see `.env.example`):

| Variable | Default | Description |
| --- | --- | --- |
| `FIRECRAWL_API_KEY` | — (required) | Your Firecrawl API key. Get one free at firecrawl.dev. |
| `SCRAPE_INTERVAL_MINUTES` | `480` | Minutes between scrapes (8 pages covering all 16 currencies). See credits math above. |
| `PORT` | `8000` | Host port exposed by docker compose. |
| `ADMIN_PASSWORD` | — (required) | Password to log into `/admin`. |
| `SESSION_SECRET` | — (required) | Secret used to sign the admin panel's session cookie. Generate with `python -c "import secrets; print(secrets.token_hex(32))"`. |
| `POSTGRES_PASSWORD` | — (required) | Password for the bundled Postgres container (not published to the host — only reachable from `api` on the compose network). |

qvai's **Telegram token** and **NVIDIA API key** do *not* go in `.env` — they're loaded live from the `/admin` panel (see the section above), so they can be changed without a redeploy.

---

## Project structure

```
app/
  main.py          # FastAPI app, v1/v2 endpoints, scheduler, wiring
  currencies.py      # registry of the 16 currencies, in elTOQUE's order
  scraper.py          # Firecrawl call + markdown -> structured JSON parsing
  cache.py            # thread-safe JSON cache (memory + disk)
  mcp_server.py        # MCP server (same tools/data as the API, same cache)
  db.py                 # Postgres access: qvai settings + conversation log
  bot.py                 # qvai Telegram bot (long polling + NVIDIA NIM)
  admin.py                # admin panel (login, dashboard, conversations, settings)
  assets.py                # static asset cache-busting (content hash)
  templates/
    index.html       # bilingual landing page
    admin_*.html      # admin panel templates
  static/
    style.css
    admin.css          # admin panel styles
data/
  cache.json         # created at runtime, gitignored
Dockerfile
docker-compose.yml       # includes the Postgres service
requirements.txt
.env.example
```

## Running without Docker

```bash
python -m venv .venv
.venv/Scripts/activate   # Windows
pip install -r requirements.txt
cp .env.example .env     # then edit it
uvicorn app.main:app --reload
```

---

## Disclaimer

This project is **not affiliated with elTOQUE**. All exchange rate data belongs to and is published by [eltoque.com](https://eltoque.com/tasas-de-cambio-cuba); this API simply re-serves it in a machine-readable format for convenience. Provided for informational purposes only — verify important decisions against the original source.

## License

MIT — see [LICENSE](LICENSE).

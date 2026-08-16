# Cuba USD Rate API

[🇪🇸 Español](README.md) · [🇬🇧 English](#)

Free JSON API for the informal (street) and official bank rates of **all 9 currencies elTOQUE publishes** (USD, EUR, MLC, CAD, MXN, ZELLE, CLA, GBP, CHF), scraped from [elTOQUE](https://eltoque.com/tasas-de-cambio-cuba) via [Firecrawl](https://www.firecrawl.dev) (needed because the site sits behind a Cloudflare challenge that a plain `curl`/`requests` can't pass).

Live demo landing page: `http://localhost:8000/` (or wherever you deploy it) — bilingual, shows the current rate plus a table of all 9 currencies, and includes copy-pasteable code snippets.

---

## How it works

- A background scheduler scrapes elTOQUE's 9 currency pages through Firecrawl, **sequentially**, every `SCRAPE_INTERVAL_MINUTES` (480 = 8h by default), and caches the parsed result to `data/cache.json`. It's sequential rather than parallel because Firecrawl returns 429 (rate limited) if you fire several scrapes at once; each call retries automatically with backoff when that happens.
- API requests **never** trigger a live scrape — they just read the cache, so responses are instant and don't burn Firecrawl credits.
- Every response includes `requested_at`, computed at the exact moment the request hits the server — **not** the time the data was scraped (that's `data.scraped_at` / `generated_at`).
- Not every currency has both markets: MLC, ZELLE (Zelle balance) and CLA (classic card balance) only trade informally (no bank counter for them); GBP and CHF only have an official bank rate (elTOQUE doesn't track a street rate for those).

### Firecrawl credits math

Firecrawl's free tier gives **1000 credits/month**, and a plain `/v2/scrape` call costs **1 credit**. Each refresh cycle scrapes all **9 currencies**, so the cost is 9x a single-currency API.

| Interval | Scrapes/day | Credits/month | Fits in 1000? |
| --- | --- | --- | --- |
| 60 min | 9 × 24 = 216 | ~6,480 | No |
| 240 min (4h) | 9 × 6 = 54 | ~1,620 | No |
| 360 min (6h) | 9 × 4 = 36 | ~1,080 | Just barely over |
| **480 min / 8h (default)** | **9 × 3 = 27** | **~810** | **Yes, ~190 credits of buffer** |
| 720 min (12h) | 9 × 2 = 18 | ~540 | Yes, plenty of buffer |

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
| `GET` | `/api/v2/rates` | All 9 currencies, in elTOQUE's own order |
| `GET` | `/api/v2/rates/{code}` | A single currency by code (`USD`, `EUR`, `MLC`, `CAD`, `MXN`, `ZELLE`, `CLA`, `GBP`, `CHF`) |
| `GET` | `/api/v1/dolar` | USD only, in the original response shape (kept for backward compatibility) |
| `GET` | `/api/v1/health` | Service status and last successful scrape time |
| `GET` | `/docs` | Interactive Swagger / OpenAPI docs |
| `GET` | `/` | Bilingual landing page |

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

`informal` is `null` for currencies with no informal market (GBP, CHF); `formal` is `null` for currencies with no bank counter (MLC, ZELLE, CLA).

`/api/v1/dolar` keeps the original response shape (`informal.usd_to_cup`, `usd_to_mlc`, `usd_to_eur`) so existing consumers don't break — it reads from the same shared cache internally rather than triggering a separate scrape.

---

## Configuration

Environment variables (see `.env.example`):

| Variable | Default | Description |
| --- | --- | --- |
| `FIRECRAWL_API_KEY` | — (required) | Your Firecrawl API key. Get one free at firecrawl.dev. |
| `SCRAPE_INTERVAL_MINUTES` | `480` | Minutes between scrapes (of all 9 currencies). See credits math above. |
| `PORT` | `8000` | Host port exposed by docker compose. |

---

## Project structure

```
app/
  main.py          # FastAPI app, v1/v2 endpoints, scheduler wiring
  currencies.py      # registry of the 9 currencies, in elTOQUE's order
  scraper.py          # Firecrawl call + markdown -> structured JSON parsing
  cache.py            # thread-safe JSON cache (memory + disk)
  templates/
    index.html       # bilingual landing page
  static/
    style.css
data/
  cache.json         # created at runtime, gitignored
Dockerfile
docker-compose.yml
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

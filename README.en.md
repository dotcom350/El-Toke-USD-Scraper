# Cuba USD Rate API

[🇪🇸 Español](README.md) · [🇬🇧 English](#)

Free JSON API for Cuba's informal (street) and official bank USD exchange rates, scraped hourly from [elTOQUE](https://eltoque.com/tasas-de-cambio-cuba/dolar) via [Firecrawl](https://www.firecrawl.dev) (needed because the site sits behind a Cloudflare challenge that a plain `curl`/`requests` can't pass).

Live demo landing page: `http://localhost:8000/` (or wherever you deploy it) — bilingual, shows the current rate, and includes copy-pasteable code snippets.

---

## How it works

- A background scheduler scrapes elTOQUE through Firecrawl every `SCRAPE_INTERVAL_MINUTES` (default **60**) and caches the parsed result to `data/cache.json`.
- API requests **never** trigger a live scrape — they just read the cache, so responses are instant and don't burn Firecrawl credits.
- Every response includes `requested_at`, computed at the exact moment the request hits the server — **not** the time the data was scraped (that's `data.scraped_at`).

### Firecrawl credits math

Firecrawl's free tier gives **1000 credits/month**, and a plain `/v2/scrape` call costs **1 credit**.

| Interval | Scrapes/day | Credits/month | Fits in 1000? |
| --- | --- | --- | --- |
| 15 min | 96 | ~2,880 | No |
| 30 min | 48 | ~1,440 | No |
| **60 min (default)** | **24** | **~720-744** | **Yes, ~260 credits of buffer** |
| 120 min | 12 | ~360 | Yes, plenty of buffer |

The default (hourly) leaves headroom for manual testing, restarts, and the immediate scrape that runs on every app startup.

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
curl http://localhost:8000/api/v1/dolar
```

---

## Endpoints

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/v1/dolar` | Informal + official USD rates, as cached JSON |
| `GET` | `/api/v1/health` | Service status and last successful scrape time |
| `GET` | `/docs` | Interactive Swagger / OpenAPI docs |
| `GET` | `/` | Bilingual landing page |

### Example response

```json
{
  "success": true,
  "requested_at": "2026-08-15T14:32:07.123456+00:00",
  "interval_minutes": 60,
  "data": {
    "scraped_at": "2026-08-15T14:00:11.905000+00:00",
    "source_url": "https://eltoque.com/tasas-de-cambio-cuba/dolar",
    "source_updated_label": "15 de agosto de 2026 a las 08:03 a. m.",
    "informal": {
      "usd_to_cup": 665.0,
      "usd_to_mlc": 1.41,
      "usd_to_eur": 0.86,
      "change_vs_yesterday_cup": 0.0
    },
    "formal": {
      "BPA": { "compra": 602.70, "venta": 627.30 },
      "BANDEC": { "compra": 580.16, "venta": 603.84 },
      "BANMET Efectivo": { "compra": 602.70, "venta": 627.30 },
      "BANMET Transferencia": { "compra": 615.00, "venta": 624.23 },
      "CADECA Casas de cambio": { "compra": 585.00, "venta": 608.40 },
      "CADECA Hoteles y Aeropuertos": { "compra": 596.55, "venta": 651.90 }
    }
  }
}
```

---

## Configuration

Environment variables (see `.env.example`):

| Variable | Default | Description |
| --- | --- | --- |
| `FIRECRAWL_API_KEY` | — (required) | Your Firecrawl API key. Get one free at firecrawl.dev. |
| `SCRAPE_INTERVAL_MINUTES` | `60` | Minutes between scrapes. See credits math above. |
| `PORT` | `8000` | Host port exposed by docker compose. |

---

## Project structure

```
app/
  main.py          # FastAPI app, endpoints, scheduler wiring
  scraper.py        # Firecrawl call + markdown -> structured JSON parsing
  cache.py          # thread-safe JSON cache (memory + disk)
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

This project is **not affiliated with elTOQUE**. All exchange rate data belongs to and is published by [eltoque.com](https://eltoque.com/tasas-de-cambio-cuba/dolar); this API simply re-serves it in a machine-readable format for convenience. Provided for informational purposes only — verify important decisions against the original source.

## License

MIT — see [LICENSE](LICENSE).

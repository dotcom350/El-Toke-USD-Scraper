import hashlib
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.cache import RateCache
from app.currencies import CURRENCIES, CURRENCIES_BY_CODE
from app.mcp_server import build_mcp_asgi_app
from app.scraper import scrape_all

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cuba-usd-rate-api")

FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
INTERVAL_MINUTES = int(os.getenv("SCRAPE_INTERVAL_MINUTES", "480"))
CACHE_PATH = os.getenv("CACHE_PATH", "data/cache.json")

cache = RateCache(path=CACHE_PATH)
scheduler = AsyncIOScheduler()


def _get_currency_entry(code: str) -> dict | None:
    snapshot = cache.get()
    if not snapshot:
        return None
    return snapshot.get("currencies", {}).get(code)


def _get_all_currencies() -> list | None:
    snapshot = cache.get()
    if not snapshot:
        return None
    currencies = snapshot.get("currencies", {})
    return [currencies[c["code"]] for c in CURRENCIES if c["code"] in currencies]


mcp_asgi_app = build_mcp_asgi_app(fetch_all=_get_all_currencies, fetch_one=_get_currency_entry)


async def refresh_job() -> None:
    try:
        data = await scrape_all(FIRECRAWL_API_KEY)
        # Merge onto the existing cache instead of replacing it wholesale,
        # so a currency that fails this cycle (transient Firecrawl error)
        # keeps showing its last-known-good rate instead of disappearing
        # until the next successful scrape.
        existing = cache.get()
        if existing and existing.get("currencies"):
            merged = dict(existing["currencies"])
            merged.update(data["currencies"])
            data["currencies"] = merged
        cache.set(data)
        ok_count = len(data["currencies"])
        logger.info(
            "Scrape refreshed at %s (%d/%d currencies ok)",
            data["generated_at"],
            ok_count,
            len(CURRENCIES),
        )
        if data["errors"]:
            logger.warning("Currencies that failed to scrape: %s", list(data["errors"]))
    except Exception as exc:  # noqa: BLE001 - keep serving stale cache on any failure
        logger.exception("Scrape refresh failed")
        cache.set_error(str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    cache.load()
    if not FIRECRAWL_API_KEY:
        logger.warning("FIRECRAWL_API_KEY is not set - the scraper will not run.")
    else:
        await refresh_job()
        scheduler.add_job(refresh_job, "interval", minutes=INTERVAL_MINUTES, id="refresh")
        scheduler.start()
    # The MCP sub-app manages its own background task group via its own
    # lifespan; mounting it doesn't wire that up automatically, so it has to
    # be entered here alongside our own startup/shutdown.
    async with mcp_asgi_app.router.lifespan_context(mcp_asgi_app):
        yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="Cuba Currency Rate API",
    description="Free API for Cuba's informal and official exchange rates across every currency elTOQUE tracks.",
    version="2.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/mcp", mcp_asgi_app)
templates = Jinja2Templates(directory="app/templates")


def _static_asset_version(path: str) -> str:
    """Content hash for cache-busting static assets. Cloudflare (and
    browsers) cache /static/* aggressively; since the URL never otherwise
    changes between deploys, a stale cached copy can outlive a redeploy by
    hours. Appending ?v=<hash> makes each content change a new URL instead.
    """
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()[:10]
    except OSError:
        return "0"


STATIC_VERSION = _static_asset_version("app/static/style.css")


def _v1_dolar_shape(usd_entry: dict) -> dict:
    """Keeps /api/v1/dolar's original response shape stable for existing
    consumers even though scraping is now driven by the shared multi-currency
    job.
    """
    informal = usd_entry.get("informal") or {}
    conversions = informal.get("conversions") or {}
    return {
        "scraped_at": usd_entry["scraped_at"],
        "source_url": usd_entry["source_url"],
        "source_updated_label": usd_entry.get("source_updated_label"),
        "informal": {
            "usd_to_cup": informal.get("cup"),
            "usd_to_mlc": conversions.get("MLC"),
            "usd_to_eur": conversions.get("EUR"),
            "change_vs_yesterday_cup": informal.get("change_vs_yesterday_cup"),
        },
        "formal": usd_entry.get("formal") or {},
    }


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"static_version": STATIC_VERSION},
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/api/v1/dolar")
async def get_dolar():
    requested_at = datetime.now(timezone.utc).isoformat()
    usd_entry = _get_currency_entry("USD")
    if usd_entry is None:
        raise HTTPException(status_code=503, detail="No data yet, try again in a few seconds.")
    return {
        "success": True,
        "requested_at": requested_at,
        "interval_minutes": INTERVAL_MINUTES,
        "data": _v1_dolar_shape(usd_entry),
    }


@app.get("/api/v2/rates")
async def get_all_rates():
    requested_at = datetime.now(timezone.utc).isoformat()
    snapshot = cache.get()
    if not snapshot:
        raise HTTPException(status_code=503, detail="No data yet, try again in a few seconds.")
    currencies = snapshot.get("currencies", {})
    rates = [currencies[c["code"]] for c in CURRENCIES if c["code"] in currencies]
    return {
        "success": True,
        "requested_at": requested_at,
        "interval_minutes": INTERVAL_MINUTES,
        "generated_at": snapshot.get("generated_at"),
        "rates": rates,
    }


@app.get("/api/v2/rates/{code}")
async def get_rate(code: str):
    requested_at = datetime.now(timezone.utc).isoformat()
    code = code.upper()
    if code not in CURRENCIES_BY_CODE:
        raise HTTPException(status_code=404, detail=f"Unknown currency code: {code}")
    entry = _get_currency_entry(code)
    if entry is None:
        raise HTTPException(status_code=503, detail="No data yet, try again in a few seconds.")
    return {
        "success": True,
        "requested_at": requested_at,
        "interval_minutes": INTERVAL_MINUTES,
        "data": entry,
    }


@app.get("/api/v1/health")
async def health():
    snapshot = cache.get()
    return {
        "status": "ok" if FIRECRAWL_API_KEY else "misconfigured",
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "last_scrape_at": cache.last_scrape_at(),
        "currencies_ok": len(snapshot.get("currencies", {})) if snapshot else 0,
        "currencies_total": len(CURRENCIES),
        "interval_minutes": INTERVAL_MINUTES,
    }

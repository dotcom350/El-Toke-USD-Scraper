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
from app.scraper import SOURCE_URL, scrape_and_parse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cuba-usd-rate-api")

FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
INTERVAL_MINUTES = int(os.getenv("SCRAPE_INTERVAL_MINUTES", "60"))
CACHE_PATH = os.getenv("CACHE_PATH", "data/cache.json")

cache = RateCache(path=CACHE_PATH)
scheduler = AsyncIOScheduler()


async def refresh_job() -> None:
    try:
        data = await scrape_and_parse(FIRECRAWL_API_KEY)
        cache.set(data)
        logger.info("Scrape refreshed successfully at %s", data["scraped_at"])
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
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="Cuba USD Rate API",
    description="Free API for Cuba's informal and official USD exchange rates, scraped from elTOQUE.",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/v1/dolar")
async def get_dolar():
    requested_at = datetime.now(timezone.utc).isoformat()
    snapshot = cache.get()
    if snapshot is None:
        raise HTTPException(status_code=503, detail="No data yet, try again in a few seconds.")
    return {
        "success": True,
        "requested_at": requested_at,
        "interval_minutes": INTERVAL_MINUTES,
        "data": snapshot,
    }


@app.get("/api/v1/health")
async def health():
    return {
        "status": "ok" if FIRECRAWL_API_KEY else "misconfigured",
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "last_scrape_at": cache.last_scrape_at(),
        "interval_minutes": INTERVAL_MINUTES,
        "source_url": SOURCE_URL,
    }

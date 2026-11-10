import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import db
from app.admin import build_admin_router
from app.assets import content_hash
from app.bot import BotManager
from app.cache import RateCache
from app.currencies import CURRENCIES, CURRENCIES_BY_CODE
from app.daily_history import load_daily, merge_with_scrapes, seed_from_dir
from app.mcp_server import build_mcp_asgi_app
from app.scraper import scrape_all

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cuba-usd-rate-api")

FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
INTERVAL_MINUTES = int(os.getenv("SCRAPE_INTERVAL_MINUTES", "480"))
CACHE_PATH = os.getenv("CACHE_PATH", "data/cache.json")
HISTORY_DIR = os.getenv("HISTORY_DIR", "data/history")
SEED_DIR = os.getenv("SEED_DIR", str(Path(__file__).parent / "seed"))


async def save_history_to_json(rows: list) -> None:
    """Persist real scrape points, with the same uniqueness key as Postgres."""
    Path(HISTORY_DIR).mkdir(parents=True, exist_ok=True)
    for code, recorded_at, informal_cup, formal_json in rows:
        filepath = Path(HISTORY_DIR) / f"{code}.json"
        entry = {
            "recorded_at": recorded_at.isoformat(),
            "informal_cup": informal_cup,
            "formal": json.loads(formal_json) if formal_json else None,
        }
        try:
            if filepath.exists():
                with open(filepath, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            else:
                existing = []
            by_time = {datetime.fromisoformat(point["recorded_at"]): point for point in existing}
            by_time.setdefault(recorded_at, entry)
            pending = filepath.with_suffix(".json.tmp")
            with open(pending, "w", encoding="utf-8") as f:
                json.dump([by_time[time] for time in sorted(by_time)], f, ensure_ascii=False, indent=2)
            pending.replace(filepath)
        except Exception as e:
            logger.error(f"Could not save history for {code}: {e}")


async def load_history_from_json(code: str, since=None, limit: int = 10000) -> list[dict]:
    """Load history from JSON file for a specific currency."""
    filepath = Path(HISTORY_DIR) / f"{code}.json"
    if not filepath.exists():
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            all_points = json.load(f)
        
        points = []
        for entry in all_points:
            recorded_at = datetime.fromisoformat(entry["recorded_at"])
            if since and recorded_at < since:
                continue
            points.append({
                "recorded_at": entry["recorded_at"],
                "informal_cup": entry["informal_cup"],
                "formal": entry["formal"],
            })
        
        by_time = {datetime.fromisoformat(point["recorded_at"]): point for point in points}
        return [by_time[time] for time in sorted(by_time)[-limit:]]
    except Exception as e:
        logger.error(f"Could not load history for {code}: {e}")
        return []

SESSION_SECRET = os.getenv("SESSION_SECRET") or secrets.token_hex(32)
if not os.getenv("SESSION_SECRET"):
    logger.warning("SESSION_SECRET is not set - using a random one, admin logins won't survive a restart.")

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
bot_manager = BotManager(get_rates=_get_all_currencies)
admin_router = build_admin_router(bot_manager)


async def record_history_snapshot(snapshot: dict | None) -> None:
    """Append every cached entry to rate_history. Entries carried over
    unchanged from an earlier cycle keep their original scraped_at, so the
    (code, recorded_at) unique key makes re-recording them a no-op - only
    freshly scraped values add new points."""
    if not snapshot:
        return
    rows = []
    for code, entry in (snapshot.get("currencies") or {}).items():
        informal = (entry.get("informal") or {}).get("cup")
        formal = entry.get("formal")
        if (informal is None and not formal) or not entry.get("scraped_at"):
            continue
        rows.append((
            code,
            datetime.fromisoformat(entry["scraped_at"]),
            informal,
            json.dumps(formal) if formal else None,
        ))
    
    # Try database first
    if db.pool_ready():
        try:
            added = await db.record_history(rows)
            if added:
                logger.info("Recorded %d new history points in database", added)
        except Exception:  # noqa: BLE001
            logger.exception("Could not record rate history in database, trying JSON fallback")
    
    # Keep a disk copy even while Postgres is healthy, so a temporary database
    # outage can still serve the same history.
    try:
        await save_history_to_json(rows)
        logger.info("Persisted snapshot of %d history points to JSON files", len(rows))
    except Exception:  # noqa: BLE001
        logger.exception("Could not record rate history at all")


async def refresh_job() -> None:
    try:
        # Pass the existing cache in so any part (informal/formal) that fails
        # this cycle - transient Firecrawl error, elTOQUE maintenance page -
        # keeps its last-known-good value instead of being blanked out.
        existing = cache.get() or {}
        data = await scrape_all(FIRECRAWL_API_KEY, previous=existing.get("currencies"))
        cache.set(data)
        await record_history_snapshot(data)
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
    # Daily values shipped with the app (elTOQUE's last months) so the
    # chart isn't empty on a fresh install. Only fills missing days.
    seeded = seed_from_dir(HISTORY_DIR, SEED_DIR)
    if seeded:
        logger.info("Seeded daily history: %s", seeded)
    # DB first, so the startup scrape (and the cache already on disk) can be
    # recorded into the rate history.
    await db.init_pool()
    await record_history_snapshot(cache.get())
    if not FIRECRAWL_API_KEY:
        logger.warning("FIRECRAWL_API_KEY is not set - the scraper will not run.")
    else:
        await refresh_job()
        scheduler.add_job(refresh_job, "interval", minutes=INTERVAL_MINUTES, id="refresh")
        scheduler.start()

    if db.pool_ready():
        settings = await db.get_settings()
        if settings.get("enabled") and settings.get("telegram_token"):
            await bot_manager.start(settings["telegram_token"])

    # The MCP sub-app manages its own background task group via its own
    # lifespan; mounting it doesn't wire that up automatically, so it has to
    # be entered here alongside our own startup/shutdown.
    async with mcp_asgi_app.router.lifespan_context(mcp_asgi_app):
        yield
    if scheduler.running:
        scheduler.shutdown(wait=False)
    await bot_manager.stop()
    await db.close_pool()


app = FastAPI(
    title="Cuba Currency Rate API",
    description="Free API for Cuba's informal and official exchange rates across every currency elTOQUE tracks.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, session_cookie="qvai_admin", max_age=60 * 60 * 24 * 7)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/mcp", mcp_asgi_app)
app.include_router(admin_router)
templates = Jinja2Templates(directory="app/templates")

STATIC_VERSION = content_hash("app/static/style.css")


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


@app.get("/api/v2/history/{code}")
async def get_history(code: str, days: Optional[int] = Query(None, ge=1, le=3650)):
    """Every recorded data point for one currency, oldest first: the
    informal CUP rate and the full bank table as of each scrape
    (source="scrape"), plus one informal-only point per day that has no
    scrape of its own (source="daily"). `days` limits it to the last N
    days (default: everything)."""
    requested_at = datetime.now(timezone.utc).isoformat()
    code = code.upper()
    if code not in CURRENCIES_BY_CODE:
        raise HTTPException(status_code=404, detail=f"Unknown currency code: {code}")
    
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    
    # Merge disk history with Postgres: points collected during an outage
    # must remain visible after the database comes back.
    points = await load_history_from_json(code, since=since)
    if db.pool_ready():
        try:
            rows = await db.get_history(code, since=since)
            points.extend([
                {
                    "recorded_at": r["recorded_at"].isoformat(),
                    "informal_cup": r["informal_cup"],
                    "formal": json.loads(r["formal"]) if r["formal"] else None,
                }
                for r in rows
            ])
        except Exception:
            logger.exception("Database history load failed, trying JSON fallback")
    by_time = {datetime.fromisoformat(point["recorded_at"]): point for point in points}
    points = [by_time[time] for time in sorted(by_time)]
    # Days with no scrape of our own (before this API started, or while it
    # was down) are filled with elTOQUE's daily median for that day.
    points = merge_with_scrapes(points, load_daily(HISTORY_DIR, code), since=since)[-10000:]

    return {
        "success": True,
        "requested_at": requested_at,
        "interval_minutes": INTERVAL_MINUTES,
        "code": code,
        "points": points,
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

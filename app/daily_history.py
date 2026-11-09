"""Daily informal-rate medians (one value per currency per day), used to
backfill the history chart for days this API has no scrape of its own -
the months before it started running, or any day the scraper was down.

Stored as {"YYYY-MM-DD": cup} in <HISTORY_DIR>/<CODE>.daily.json, next to
the per-scrape history files. Real scrape points always win: a daily value
is only served for a UTC day that has no scraped informal rate.

Import a file of daily values by hand with:
    python -m app.daily_history import USD path/to/file.json
The file may be [{"date": "2026-08-10", "price": 665.84}, ...] or elTOQUE's
own chart shape [{"_id": "2026-08-10", "median": 665.84}, ...].
"""

import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger("cuba-usd-rate-api")

DAILY_SOURCE = "daily"
SCRAPE_SOURCE = "scrape"


def _path(history_dir: str, code: str) -> Path:
    return Path(history_dir) / f"{code.upper()}.daily.json"


def _valid_day(value) -> Optional[str]:
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def _valid_rate(value) -> Optional[float]:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    return rate if rate > 0 and rate == rate and rate != float("inf") else None


def normalize(rows: Iterable[dict]) -> dict[str, float]:
    """Turns a list of {date|_id, price|median} rows into {day: rate},
    dropping anything that isn't a real date with a positive number."""
    values: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = _valid_day(row.get("date", row.get("_id")))
        rate = _valid_rate(row.get("price", row.get("median")))
        if day and rate is not None:
            values[day] = round(rate, 4)
    return values


def load_daily(history_dir: str, code: str) -> dict[str, float]:
    path = _path(history_dir, code)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {day: rate for day, rate in raw.items() if _valid_day(day) == day and _valid_rate(rate) is not None}
    except Exception as exc:  # noqa: BLE001 - a bad file must not break the chart
        logger.error("Could not load daily history for %s: %s", code, exc)
        return {}


def save_daily(history_dir: str, code: str, values: dict[str, float]) -> int:
    """Merges `values` into the stored file. A day already stored is
    overwritten, since the current day's median keeps moving until the day
    closes. Returns how many days were added or changed."""
    if not values:
        return 0
    path = _path(history_dir, code)
    path.parent.mkdir(parents=True, exist_ok=True)
    stored = load_daily(history_dir, code)
    changed = sum(1 for day, rate in values.items() if stored.get(day) != rate)
    if not changed:
        return 0
    stored.update(values)
    pending = path.with_suffix(".json.tmp")
    with open(pending, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(stored.items())), f, ensure_ascii=False, indent=2)
    pending.replace(path)
    return changed


def merge_with_scrapes(scrape_points: list[dict], daily: dict[str, float], since: Optional[datetime] = None) -> list[dict]:
    """Scrape points (tagged source="scrape") plus one daily point, at
    noon UTC so it lands on the same calendar day across the Americas and
    Europe, for every day without a scraped informal rate. Oldest first."""
    points = [{**p, "source": p.get("source") or SCRAPE_SOURCE} for p in scrape_points]
    scraped_days = {
        datetime.fromisoformat(p["recorded_at"]).astimezone(timezone.utc).date().isoformat()
        for p in points
        if p.get("informal_cup") is not None
    }
    since_day = since.astimezone(timezone.utc).date().isoformat() if since else None
    for day, rate in daily.items():
        if day in scraped_days or (since_day and day < since_day):
            continue
        points.append({
            "recorded_at": f"{day}T12:00:00+00:00",
            "informal_cup": rate,
            "formal": None,
            "source": DAILY_SOURCE,
        })
    points.sort(key=lambda p: datetime.fromisoformat(p["recorded_at"]))
    return points


def _main(argv: list[str]) -> int:
    import os

    if len(argv) != 3 or argv[0] != "import":
        print(__doc__)
        return 2
    code, file_path = argv[1].upper(), argv[2]
    with open(file_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    rows = payload.get("data", payload) if isinstance(payload, dict) else payload
    values = normalize(rows)
    if not values:
        print(f"No valid daily values found in {file_path}")
        return 1
    history_dir = os.getenv("HISTORY_DIR", "data/history")
    changed = save_daily(history_dir, code, values)
    days = sorted(values)
    print(f"{code}: {len(values)} days ({days[0]} -> {days[-1]}), {changed} added/changed in {_path(history_dir, code)}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))

"""Daily informal-rate medians (one value per currency per day), used to
backfill the history chart for days this API has no scrape of its own -
the months before it started running, or any day the scraper was down.

Stored as {"YYYY-MM-DD": cup} in <HISTORY_DIR>/<CODE>.daily.json, next to
the per-scrape history files. Real scrape points always win: a daily value
is only served for a UTC day that has no scraped informal rate.

Import a file of daily values by hand with:
    python -m app.daily_history import USD path/to/file.json
    python -m app.daily_history import-csv path/to/file.csv
The JSON may be [{"date": "2026-08-10", "price": 665.84}, ...] or elTOQUE's
own chart shape [{"_id": "2026-08-10", "median": 665.84}, ...]. The CSV has
a "fecha" (or "date") column plus one column per currency code:
    fecha,USD,EUR,MLC
    2026-08-11,670,780,460.28

Every CSV in app/seed/ is loaded on startup with seed_from_dir(), filling
only days not stored yet, so the chart has elTOQUE's last months from the
first deploy and restarts never overwrite newer values.
"""

import csv
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


def save_daily(history_dir: str, code: str, values: dict[str, float], overwrite: bool = True) -> int:
    """Merges `values` into the stored file. By default a day already
    stored is overwritten, since the current day's median keeps moving
    until the day closes; overwrite=False only adds missing days. Returns
    how many days were added or changed."""
    if not values:
        return 0
    path = _path(history_dir, code)
    path.parent.mkdir(parents=True, exist_ok=True)
    stored = load_daily(history_dir, code)
    if not overwrite:
        values = {day: rate for day, rate in values.items() if day not in stored}
    changed = sum(1 for day, rate in values.items() if stored.get(day) != rate)
    if not changed:
        return 0
    stored.update(values)
    pending = path.with_suffix(".json.tmp")
    with open(pending, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(stored.items())), f, ensure_ascii=False, indent=2)
    pending.replace(path)
    return changed


def read_csv(path) -> dict[str, dict[str, float]]:
    """{code: {day: rate}} from a "fecha,USD,EUR,..." CSV. Empty or
    non-numeric cells are skipped."""
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        date_col = next((c for c in reader.fieldnames or [] if c.strip().lower() in ("fecha", "date")), None)
        if not date_col:
            raise ValueError(f"{path}: no 'fecha' or 'date' column")
        codes = [c for c in reader.fieldnames if c != date_col]
        rows = list(reader)
    return {
        code.strip().upper(): normalize({"date": row[date_col], "price": row[code]} for row in rows)
        for code in codes
    }


def seed_from_dir(history_dir: str, seed_dir: str) -> dict[str, int]:
    """Loads every CSV in seed_dir, adding only days not stored yet.
    Returns {code: days added}."""
    added: dict[str, int] = {}
    for path in sorted(Path(seed_dir).glob("*.csv")):
        try:
            for code, values in read_csv(path).items():
                count = save_daily(history_dir, code, values, overwrite=False)
                if count:
                    added[code] = added.get(code, 0) + count
        except Exception as exc:  # noqa: BLE001 - a bad seed file must not stop the API
            logger.error("Could not load seed file %s: %s", path, exc)
    return added


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

    history_dir = os.getenv("HISTORY_DIR", "data/history")
    if len(argv) == 2 and argv[0] == "import-csv":
        for code, values in read_csv(argv[1]).items():
            if values:
                changed = save_daily(history_dir, code, values)
                days = sorted(values)
                print(f"{code}: {len(values)} days ({days[0]} -> {days[-1]}), {changed} added/changed")
        return 0
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
    changed = save_daily(history_dir, code, values)
    days = sorted(values)
    print(f"{code}: {len(values)} days ({days[0]} -> {days[-1]}), {changed} added/changed in {_path(history_dir, code)}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))

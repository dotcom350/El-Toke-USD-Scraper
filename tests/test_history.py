import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import daily_history, main
from app.cache import RateCache


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.enterContext(patch.object(main, "HISTORY_DIR", self.temp.name))
        self.now = datetime.now(timezone.utc)

    def row(self, when, cup=665):
        return ("USD", when, cup, json.dumps({"BPA": {"compra": 600, "venta": 625}}))

    async def test_restarts_do_not_duplicate_points(self):
        rows = [self.row(self.now)]
        await main.save_history_to_json(rows)
        await main.save_history_to_json(rows)
        points = await main.load_history_from_json("USD")
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["informal_cup"], 665)
        self.assertEqual(points[0]["formal"]["BPA"]["venta"], 625)

    async def test_returns_newest_points_in_chronological_order(self):
        await main.save_history_to_json([
            self.row(self.now - timedelta(days=i), 665 - i) for i in [2, 0, 3, 1]
        ])
        points = await main.load_history_from_json("USD", limit=2)
        self.assertEqual([p["informal_cup"] for p in points], [664, 665])
        recent = await main.load_history_from_json("USD", since=self.now - timedelta(hours=1))
        self.assertEqual(len(recent), 1)

    async def test_disk_copy_is_kept_when_database_is_available(self):
        snapshot = {"currencies": {"USD": {
            "scraped_at": self.now.isoformat(), "informal": {"cup": 665}, "formal": None,
        }}}
        with patch.object(main.db, "pool_ready", return_value=True), patch.object(
            main.db, "record_history", new=AsyncMock(return_value=1)
        ) as record:
            await main.record_history_snapshot(snapshot)
            record.assert_awaited_once()
        self.assertEqual(len(await main.load_history_from_json("USD")), 1)

    async def test_endpoint_merges_outage_points_with_database_without_duplicates(self):
        await main.save_history_to_json([self.row(self.now - timedelta(days=1)), self.row(self.now)])
        db_rows = [{
            "recorded_at": self.now, "informal_cup": 666, "formal": None,
        }]
        with patch.object(main.db, "pool_ready", return_value=True), patch.object(
            main.db, "get_history", new=AsyncMock(return_value=db_rows)
        ):
            payload = await main.get_history("usd", days=None)
        self.assertEqual([p["informal_cup"] for p in payload["points"]], [665, 666])

    async def test_database_failure_preserves_disk_history(self):
        await main.save_history_to_json([self.row(self.now)])
        with patch.object(main.db, "pool_ready", return_value=True), patch.object(
            main.db, "get_history", new=AsyncMock(side_effect=RuntimeError("database offline"))
        ):
            payload = await main.get_history("USD", days=None)
        self.assertEqual(len(payload["points"]), 1)

    async def test_api_prices_work_without_database_and_without_history(self):
        cache = RateCache(str(Path(self.temp.name) / "cache.json"))
        cache.set({"currencies": {"USD": {
            "code": "USD", "scraped_at": self.now.isoformat(),
            "source_url": "https://example.test/usd", "informal": {
                "cup": 665, "conversions": {"MLC": 1.44, "EUR": 0.86},
            }, "formal": None,
        }}})
        with patch.object(main, "cache", cache), patch.object(main.db, "pool_ready", return_value=False):
            client = TestClient(main.app)  # No lifespan: never scrape or start the bot in tests.
            self.addCleanup(client.close)
            self.assertEqual(client.get("/api/v1/dolar").json()["data"]["informal"]["usd_to_cup"], 665)
            self.assertEqual(len(client.get("/api/v2/rates").json()["rates"]), 1)
            self.assertEqual(client.get("/api/v2/history/USD").json()["points"], [])
            self.assertEqual(client.get("/api/v2/history/UNKNOWN").status_code, 404)
            self.assertEqual(client.get("/api/v2/history/USD?days=0").status_code, 422)

    async def test_daily_values_fill_only_days_without_a_scrape(self):
        today = self.now.date()
        await main.save_history_to_json([self.row(self.now, 900)])
        daily_history.save_daily(self.temp.name, "USD", {
            (today - timedelta(days=d)).isoformat(): 800 + d for d in range(0, 4)
        })
        payload = await main.get_history("usd", days=None)
        points = payload["points"]
        # Today has a real scrape, so its daily median is not added on top.
        self.assertEqual([p["informal_cup"] for p in points], [803, 802, 801, 900])
        self.assertEqual([p["source"] for p in points], ["daily", "daily", "daily", "scrape"])
        self.assertTrue(points[0]["recorded_at"].endswith("T12:00:00+00:00"))
        recent = await main.get_history("USD", days=2)
        self.assertEqual([p["informal_cup"] for p in recent["points"]], [802, 801, 900])

    def test_daily_import_accepts_both_file_shapes_and_overwrites_a_day(self):
        values = daily_history.normalize([
            {"date": "2026-08-10", "price": 665.84},
            {"_id": "2026-08-11", "median": "670"},
            {"date": "not-a-date", "price": 1},
            {"date": "2026-08-12", "price": None},
            {"date": "2026-08-13", "price": -5},
        ])
        self.assertEqual(values, {"2026-08-10": 665.84, "2026-08-11": 670.0})
        self.assertEqual(daily_history.save_daily(self.temp.name, "usd", values), 2)
        self.assertEqual(daily_history.save_daily(self.temp.name, "USD", values), 0)
        self.assertEqual(daily_history.save_daily(self.temp.name, "USD", {"2026-08-11": 671.0}), 1)
        self.assertEqual(daily_history.load_daily(self.temp.name, "USD")["2026-08-11"], 671.0)
        # The per-scrape history file is a different file and stays untouched.
        self.assertEqual(await_points(self.temp.name), [])


    def test_seed_csv_fills_missing_days_only_and_survives_restarts(self):
        seed = Path(self.temp.name) / "seed"
        seed.mkdir()
        (seed / "rates.csv").write_text(
            "fecha,USD,EUR,MLC\n2026-08-11,670,780,460.28\n2026-08-12,670,,460\n", encoding="utf-8"
        )
        history = str(Path(self.temp.name) / "history")
        self.assertEqual(daily_history.seed_from_dir(history, str(seed)), {"USD": 2, "EUR": 1, "MLC": 2})
        daily_history.save_daily(history, "USD", {"2026-08-12": 675.0})  # a newer value
        self.assertEqual(daily_history.seed_from_dir(history, str(seed)), {})
        self.assertEqual(daily_history.load_daily(history, "USD"), {"2026-08-11": 670.0, "2026-08-12": 675.0})
        self.assertEqual(daily_history.load_daily(history, "MLC")["2026-08-11"], 460.28)

    def test_shipped_seed_covers_three_months_of_usd_eur_mlc(self):
        values = daily_history.read_csv(Path(main.SEED_DIR) / "eltoque_tasas_3meses.csv")
        for code in ("USD", "EUR", "MLC"):
            self.assertGreaterEqual(len(values[code]), 90, code)


def await_points(history_dir):
    import asyncio
    with patch.object(main, "HISTORY_DIR", history_dir):
        return asyncio.run(main.load_history_from_json("USD"))

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import main
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

"""Run with: python -m unittest discover -s tests -v

Install requirements-dev.txt and run `python -m playwright install chromium` first.
The browser uses the real FastAPI routes with deterministic, isolated test data.
No scrapes, bot activity, credentials or external network requests are needed.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from app import main
from app.cache import RateCache


class LandingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.enterContext(patch.object(main, "HISTORY_DIR", self.temp.name))
        self.enterContext(patch.object(main.db, "pool_ready", return_value=False))
        self.now = datetime.now(timezone.utc)
        bank = {"BPA": {"compra": 600, "venta": 625}}
        rates = {}
        for code, name, informal, formal in [
            ("USD", "Dólar", {"cup": 665, "conversions": {"MLC": 1.44, "EUR": 0.86}}, bank),
            ("GBP", "Libra", None, bank),
            ("MLC", "MLC", {"cup": 470}, None),
        ]:
            rates[code] = {
                "code": code, "name_es": name, "name_en": code,
                "scraped_at": self.now.isoformat(), "source_url": "https://example.test/",
                "informal": informal, "formal": formal,
            }
            self.write_history(code, [informal["cup"] if informal else None], formal)
        cache = RateCache(str(Path(self.temp.name) / "cache.json"))
        cache.set({"generated_at": self.now.isoformat(), "currencies": rates})
        self.enterContext(patch.object(main, "cache", cache))
        self.client = TestClient(main.app)  # Deliberately avoid production startup.
        self.addCleanup(self.client.close)
        self.context = self.browser.new_context(viewport={"width": 1100, "height": 900})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        # Canvas silently ignores NaN coordinates. Turn that into a regression failure.
        self.page.add_init_script("""
            for (const name of ['moveTo', 'lineTo', 'arc', 'translate', 'fillText']) {
                const original = CanvasRenderingContext2D.prototype[name];
                CanvasRenderingContext2D.prototype[name] = function(...args) {
                    if (args.some(a => typeof a === 'number' && !Number.isFinite(a))) {
                        throw new Error('Invalid canvas coordinate in ' + name);
                    }
                    return original.apply(this, args);
                };
            }
        """)
        self.page.route("**/*", self.route)

    def route(self, route):
        url = urlsplit(route.request.url)
        if url.hostname != "rates.test":
            route.abort()
            return
        response = self.client.get(url.path + ("?" + url.query if url.query else ""))
        route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

    def write_history(self, code, values, formal=None):
        points = [{
            "recorded_at": (self.now - timedelta(hours=(len(values) - i - 1) * 8)).isoformat(),
            "informal_cup": value, "formal": formal,
        } for i, value in enumerate(values)]
        (Path(self.temp.name) / f"{code}.json").write_text(json.dumps(points), encoding="utf-8")

    def load(self):
        self.page.goto("http://rates.test/")
        self.page.wait_for_function("hist.loaded && latestRates.length === 3")
        self.page.wait_for_function("document.getElementById('price-cup').textContent === '665'")

    def assert_chart_drawn(self):
        self.page.wait_for_function("""() => {
            const c = document.getElementById('chart-canvas');
            if (!c || !c.width) return false;
            const pixels = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            // The green line/point must actually appear, beyond the dark grid/background.
            for (let i = 0; i < pixels.length; i += 4) {
                if (pixels[i + 1] > 100 && pixels[i + 1] > pixels[i] * 1.5 && pixels[i + 1] > pixels[i + 2] * 1.1) return true;
            }
            return false;
        }""")
        self.assertEqual(self.errors, [])

    def test_initial_load_shows_prices_table_response_and_first_chart_point(self):
        self.load()
        self.assertEqual(self.page.locator("#rates-body tr").count(), 3)
        self.assertIn("665 CUP", self.page.locator("#rates-body").inner_text())
        self.assertTrue(json.loads(self.page.locator("#example-response").inner_text())["success"])
        self.assertEqual(self.page.locator("#hist-price").inner_text(), "665 CUP")
        self.assert_chart_drawn()

    def test_flat_history_renders_without_invalid_coordinates(self):
        self.write_history("USD", [665, 665, 665])
        self.load()
        self.assert_chart_drawn()
        self.assertIn("3 puntos", self.page.locator("#hist-meta").inner_text())

    def test_language_theme_market_currency_and_mobile_resize(self):
        self.load()
        self.page.locator('.lang-toggle [data-lang="en"]').click()
        self.assertEqual(self.page.locator("#th-currency").text_content(), "Currency")
        self.page.locator("#theme-toggle").click()
        self.page.locator('#hist-market [data-value="formal"]').click()
        self.assertEqual(self.page.locator("#hist-price").inner_text(), "625 CUP")
        self.assertTrue(self.page.locator("#hist-bank").is_visible())
        self.page.locator("#hist-currency").select_option("GBP")
        self.page.wait_for_function("hist.code === 'GBP' && hist.market === 'formal'")
        self.assertTrue(self.page.locator('#hist-market [data-value="informal"]').is_disabled())
        self.page.locator("#hist-currency").select_option("MLC")
        self.page.wait_for_function("hist.code === 'MLC' && document.getElementById('hist-price').textContent === '470 CUP'")
        self.assertTrue(self.page.locator('#hist-market [data-value="formal"]').is_disabled())
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.assert_chart_drawn()
        screenshot = os.getenv("LANDING_QA_SCREENSHOT")
        if screenshot:
            self.page.locator("#historico").screenshot(path=screenshot)

    def test_negative_changes_keep_the_minus_sign(self):
        self.write_history("USD", [670, 665])
        self.load()
        self.assertTrue(self.page.locator("#hist-change").inner_text().startswith("−5"))
        self.assert_chart_drawn()

    def test_empty_range_clears_chart_and_recovers(self):
        self.write_history("USD", [665])
        path = Path(self.temp.name) / "USD.json"
        points = json.loads(path.read_text())
        points[0]["recorded_at"] = (self.now - timedelta(days=40)).isoformat()
        path.write_text(json.dumps(points))
        self.load()
        self.assert_chart_drawn()
        self.page.locator('#hist-range [data-value="7"]').click()
        self.assertTrue(self.page.locator("#hist-empty").is_visible())
        self.assertEqual(self.page.locator("#chart-canvas").count(), 0)
        self.page.locator('#hist-range [data-value="all"]').click()
        self.assert_chart_drawn()

    def test_failed_history_request_does_not_hide_prices(self):
        self.page.route("**/api/v2/history/*", lambda route: route.fulfill(status=503, body="{}"))
        self.load()
        self.assertTrue(self.page.locator("#hist-empty").is_visible())
        self.assertIn("665 CUP", self.page.locator("#rates-body").inner_text())
        self.assertEqual(self.errors, [])

    def write_daily(self, code, days):
        today = self.now.date()
        values = {(today - timedelta(days=d)).isoformat(): 600 + (days - d) for d in range(1, days + 1)}
        (Path(self.temp.name) / f"{code}.daily.json").write_text(json.dumps(values), encoding="utf-8")

    def view_span(self):
        return self.page.evaluate("hist.view.end - hist.view.start")

    def test_daily_backfill_is_drawn_and_chart_can_be_panned_and_zoomed(self):
        self.write_daily("USD", 90)
        self.load()
        self.assert_chart_drawn()
        self.assertIn("91 puntos", self.page.locator("#hist-meta").inner_text())
        full = self.view_span()

        canvas = self.page.locator("#chart-canvas")
        canvas.scroll_into_view_if_needed()
        box = canvas.bounding_box()
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        self.page.mouse.move(cx, cy)
        self.page.mouse.wheel(0, -400)
        self.page.wait_for_function(f"hist.view.end - hist.view.start < {full * 0.8}")
        self.assertNotIn("91 puntos", self.page.locator("#hist-meta").inner_text())

        start = self.page.evaluate("hist.view.start")
        self.page.mouse.move(cx, cy)
        self.page.mouse.down()
        self.page.mouse.move(cx + 200, cy, steps=5)
        self.page.mouse.up()
        self.assertLess(self.page.evaluate("hist.view.start"), start)

        self.page.locator('#hist-zoom [data-zoom="in"]').click()
        self.page.locator('#hist-zoom [data-zoom="reset"]').click()
        self.assertAlmostEqual(self.view_span(), full, delta=1000)
        self.page.locator('#hist-range [data-value="30"]').click()
        self.assert_chart_drawn()
        self.page.mouse.move(cx, cy)  # crosshair + tooltip
        self.page.wait_for_timeout(50)
        self.assertEqual(self.errors, [])

    def test_language_switch_keeps_the_panned_view(self):
        self.write_daily("USD", 30)
        self.load()
        self.page.locator('#hist-zoom [data-zoom="in"]').click()
        view = self.page.evaluate("[hist.view.start, hist.view.end]")
        self.page.locator('.lang-toggle [data-lang="en"]').click()
        self.assertEqual(self.page.evaluate("[hist.view.start, hist.view.end]"), view)
        self.assert_chart_drawn()

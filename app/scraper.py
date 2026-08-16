"""Fetches elTOQUE's currency rate pages through Firecrawl (bypasses the
site's Cloudflare challenge) and parses the returned markdown into
structured data.

Not every currency page has the same sections:
- USD, EUR, CAD, MXN have both an informal (street) and a formal (bank)
  market section.
- MLC, ZELLE (saldo-zelle), CLA (tarjeta-clasica) only have an informal
  section - they aren't traded at bank counters.
- GBP, CHF only have a formal section - elTOQUE doesn't track a street
  rate for them.
"""

import asyncio
import re
from datetime import datetime, timezone

import httpx

from app.currencies import CURRENCIES, SOURCE_BASE

FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"

HEADLINE_RE = re.compile(
    r"valor referencial de \*\*([\-\d.,]+)\*\*.*?diferencia de \*\*(-?[\d.,]+)\*\*",
    re.DOTALL,
)
# The compact "|  | 665.00 CUP | 1.41 MLC | 0.86 EUR |" summary row. Which
# three currencies appear (and in which order) varies per page, so this
# captures each (value, unit) pair generically instead of hardcoding units.
SUMMARY_TABLE_RE = re.compile(
    r"\|\s*\|\s*([\d.]+)\s*([A-Za-z]+)\s*\|\s*([\d.]+)\s*([A-Za-z]+)\s*\|\s*([\d.]+)\s*([A-Za-z]+)\s*\|"
)
UPDATED_LABEL_RE = re.compile(
    r"\d{1,2} de [a-záéíóúñ]+ de \d{4} a las [\d:]+\s?[ap]\.\s?m\.",
    re.IGNORECASE,
)

# (display label, regex to find the "compra | venta" numbers for that bank/channel)
FORMAL_BANKS = [
    ("BPA", r"BPA\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|"),
    ("BANDEC", r"BANDEC\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|"),
    ("BANMET Efectivo", r"BANMET\s*Efectivo\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|"),
    ("BANMET Transferencia", r"BANMET\s*Transferencia\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|"),
    ("CADECA Casas de cambio", r"CADECA\s*Casas de cambio\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|"),
    ("CADECA Hoteles y Aeropuertos", r"CADECA\s*Hoteles y Aeropuertos\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|"),
]


def _clean(markdown: str) -> str:
    # Firecrawl's HTML->markdown conversion leaves stray backspace control
    # chars and escaped asterisks around the source table cells.
    return markdown.replace("\x08", "").replace("\\*", "*")


def parse_markdown(markdown: str, currency: dict) -> dict:
    text = _clean(markdown)

    headline_m = HEADLINE_RE.search(text)
    headline_cup = float(headline_m.group(1).replace(",", ".")) if headline_m else None
    change_cup = float(headline_m.group(2).replace(",", ".")) if headline_m else None

    table_m = SUMMARY_TABLE_RE.search(text)
    conversions: dict = {}
    if table_m:
        for i in range(0, 6, 2):
            value = float(table_m.group(i + 1))
            unit = table_m.group(i + 2).upper()
            conversions[unit] = value

    cup_rate = conversions.pop("CUP", None)
    if cup_rate is None:
        cup_rate = headline_cup

    informal = None
    if cup_rate is not None or conversions:
        informal = {
            "cup": cup_rate,
            "conversions": conversions,
            "change_vs_yesterday_cup": change_cup,
        }

    formal: dict = {}
    for label, pattern in FORMAL_BANKS:
        fm = re.search(pattern, text)
        if fm:
            formal[label] = {"compra": float(fm.group(1)), "venta": float(fm.group(2))}

    label_m = UPDATED_LABEL_RE.search(text)

    return {
        "code": currency["code"],
        "name_es": currency["name_es"],
        "name_en": currency["name_en"],
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "source_url": f"{SOURCE_BASE}/{currency['slug']}",
        "source_updated_label": label_m.group(0) if label_m else None,
        "informal": informal,
        "formal": formal or None,
    }


async def _scrape_one(client: httpx.AsyncClient, api_key: str, currency: dict, max_retries: int = 4) -> dict:
    for attempt in range(max_retries):
        resp = await client.post(
            FIRECRAWL_SCRAPE_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"url": f"{SOURCE_BASE}/{currency['slug']}", "formats": ["markdown"]},
        )
        if resp.status_code == 429 and attempt < max_retries - 1:
            retry_after = resp.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else min(2 ** attempt * 2, 20)
            await asyncio.sleep(delay)
            continue
        resp.raise_for_status()
        payload = resp.json()

        if not payload.get("success"):
            raise RuntimeError(f"Firecrawl returned an error for {currency['code']}: {payload}")

        return parse_markdown(payload["data"]["markdown"], currency)

    raise RuntimeError(f"Firecrawl kept rate-limiting {currency['code']} after {max_retries} attempts")


async def scrape_all(api_key: str) -> dict:
    """Scrape every currency in CURRENCIES, one at a time. Firecrawl's rate
    limit rejects concurrent scrapes (429), so this deliberately runs
    sequentially rather than in parallel. Per-currency failures are
    collected in "errors" rather than aborting the whole batch, so one bad
    page doesn't take down currencies that scraped fine.
    """
    results: dict = {}
    errors: dict = {}

    async with httpx.AsyncClient(timeout=30) as client:
        for currency in CURRENCIES:
            try:
                results[currency["code"]] = await _scrape_one(client, api_key, currency)
            except Exception as exc:  # noqa: BLE001 - isolate this currency's failure
                errors[currency["code"]] = str(exc)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "currencies": results,
        "errors": errors or None,
    }

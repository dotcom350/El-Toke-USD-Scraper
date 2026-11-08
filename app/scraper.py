"""Fetches elTOQUE's currency rate pages through Firecrawl (bypasses the
site's Cloudflare challenge) and parses the returned markdown into
structured data.

Since elTOQUE's late-2026 redesign the data lives on two kinds of page:
- Each informal-market currency (USD, EUR, MLC, CAD, MXN, ZELLE, CLA) has
  its own page with the street rate plus a 3-currency conversion summary.
  These pages no longer include the bank (formal) table.
- One shared "mercado-formal" page holds the bank compra/venta tables for
  every officially-traded currency (USD, EUR, CAD, MXN, GBP, CHF, ...).
  GBP and CHF have no page of their own anymore.

So a full refresh is 7 informal pages + 1 formal page = 8 Firecrawl credits.
"""

import asyncio
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.currencies import CURRENCIES, FORMAL_SLUG, SOURCE_BASE, source_url

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

# A bank row in a per-currency formal table, e.g. "| BANMETEfectivo | 752.64 | 783.36 |".
# The redesign dropped the space between the institution and the channel
# ("CADECACasas de cambio"), so both spellings are accepted and the label is
# re-normalized to the original "CADECA Casas de cambio" form to keep the
# API's response keys stable.
FORMAL_ROW_RE = re.compile(
    r"^\|\s*(BPA|BANDEC|BANMET|CADECA)\s*([^|]*?)\s*\|\s*([\d.]+|NaN)\s*\|\s*([\d.]+|NaN)\s*\|\s*$",
    re.MULTILINE,
)
FORMAL_CODE_RE = re.compile(r"Los valores equivalen a 1 ([A-Z]{3,5})")

# Ask Firecrawl for a live fetch instead of its own cache (which by default
# can serve a copy up to 2 days old). Without this, a transient elTOQUE
# maintenance page got cached by Firecrawl and kept being served back to us
# long after the site was back up.
FIRECRAWL_MAX_AGE_MS = 0


def _clean(markdown: str) -> str:
    # Firecrawl's HTML->markdown conversion leaves stray backspace control
    # chars and escaped asterisks around the source table cells.
    return markdown.replace("\x08", "").replace("\\*", "*")


def _snippet(text: str, length: int = 80) -> str:
    return " ".join(text.split())[:length]


def parse_informal_page(markdown: str) -> tuple[dict, Optional[str]]:
    """Returns (informal, source_updated_label). Raises if the page doesn't
    contain a street rate - e.g. elTOQUE served a maintenance page, or the
    layout changed again - so the caller keeps the last-known-good value
    instead of overwriting it with nulls."""
    text = _clean(markdown)

    headline_m = HEADLINE_RE.search(text)
    headline_cup = float(headline_m.group(1).replace(",", ".")) if headline_m else None
    change_cup = float(headline_m.group(2).replace(",", ".")) if headline_m else None

    table_m = SUMMARY_TABLE_RE.search(text)
    conversions: dict = {}
    if table_m:
        for i in range(0, 6, 2):
            conversions[table_m.group(i + 2).upper()] = float(table_m.group(i + 1))

    cup_rate = conversions.pop("CUP", None)
    if cup_rate is None:
        cup_rate = headline_cup
    if cup_rate is None:
        raise RuntimeError(f"no informal rate found on page: {_snippet(text)!r}")

    label_m = UPDATED_LABEL_RE.search(text)
    informal = {
        "cup": cup_rate,
        "conversions": conversions,
        "change_vs_yesterday_cup": change_cup,
    }
    return informal, (label_m.group(0) if label_m else None)


def parse_formal_page(markdown: str) -> dict:
    """Parses the shared mercado-formal page into
    {code: {"formal": {bank: {compra, venta}}, "label": str | None}}.
    Banks quoting "NaN" (no rate for that currency) are left out."""
    text = _clean(markdown)
    result: dict = {}
    for section in re.split(r"^## ", text, flags=re.MULTILINE):
        code_m = FORMAL_CODE_RE.search(section)
        if not code_m:
            continue
        banks: dict = {}
        for m in FORMAL_ROW_RE.finditer(section):
            institution, channel, compra, venta = m.groups()
            if compra == "NaN" or venta == "NaN":
                continue
            label = f"{institution} {channel}".strip()
            banks[label] = {"compra": float(compra), "venta": float(venta)}
        if banks:
            label_m = UPDATED_LABEL_RE.search(section)
            result[code_m.group(1)] = {"formal": banks, "label": label_m.group(0) if label_m else None}
    if not result:
        raise RuntimeError(f"no bank rate tables found on page: {_snippet(text)!r}")
    return result


async def _fetch_markdown(client: httpx.AsyncClient, api_key: str, url: str, max_retries: int = 4) -> str:
    for attempt in range(max_retries):
        resp = await client.post(
            FIRECRAWL_SCRAPE_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"url": url, "formats": ["markdown"], "maxAge": FIRECRAWL_MAX_AGE_MS},
        )
        if resp.status_code == 429 and attempt < max_retries - 1:
            retry_after = resp.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else min(2 ** attempt * 2, 20)
            await asyncio.sleep(delay)
            continue
        resp.raise_for_status()
        payload = resp.json()

        if not payload.get("success"):
            raise RuntimeError(f"Firecrawl returned an error for {url}: {payload}")
        status = (payload["data"].get("metadata") or {}).get("statusCode")
        if status and status >= 400:
            raise RuntimeError(f"{url} returned HTTP {status}")
        return payload["data"]["markdown"]

    raise RuntimeError(f"Firecrawl kept rate-limiting {url} after {max_retries} attempts")


async def scrape_all(api_key: str, previous: Optional[dict] = None) -> dict:
    """Scrape every currency in CURRENCIES, one page at a time (Firecrawl's
    rate limit rejects concurrent scrapes with 429).

    `previous` is the last cached {code: entry} map. Any part (informal or
    formal) that fails this cycle falls back to its previous value, so a
    transient failure or a maintenance page never blanks out a rate that
    was fine before. Failures are reported in "errors".
    """
    previous = previous or {}
    errors: dict = {}
    now = datetime.now(timezone.utc).isoformat()

    async with httpx.AsyncClient(timeout=60) as client:
        formal_by_code: Optional[dict] = None
        try:
            md = await _fetch_markdown(client, api_key, f"{SOURCE_BASE}/{FORMAL_SLUG}")
            formal_by_code = parse_formal_page(md)
        except Exception as exc:  # noqa: BLE001 - fall back to previous formal data
            errors["formal"] = str(exc)

        results: dict = {}
        for currency in CURRENCIES:
            code = currency["code"]
            prev = previous.get(code) or {}
            fresh = False
            label = None

            informal = None
            if currency["slug"]:
                try:
                    md = await _fetch_markdown(client, api_key, source_url(currency))
                    informal, label = parse_informal_page(md)
                    fresh = True
                except Exception as exc:  # noqa: BLE001 - isolate this currency's failure
                    errors[code] = str(exc)
                    informal = prev.get("informal")

            formal = None
            if currency["formal"]:
                section = (formal_by_code or {}).get(code)
                if section:
                    formal = section["formal"]
                    label = label or section["label"]
                    fresh = True
                else:
                    if formal_by_code is not None:
                        errors[f"{code}.formal"] = "currency missing from the mercado-formal page"
                    formal = prev.get("formal")

            if not fresh:
                # Nothing new this cycle - keep the old entry untouched (with
                # its original scraped_at) rather than restamping stale data.
                if prev:
                    results[code] = prev
                continue

            results[code] = {
                "code": code,
                "name_es": currency["name_es"],
                "name_en": currency["name_en"],
                "scraped_at": now,
                "source_url": source_url(currency),
                "source_updated_label": label or prev.get("source_updated_label"),
                "informal": informal,
                "formal": formal,
            }

    return {
        "generated_at": now,
        "currencies": results,
        "errors": errors or None,
    }

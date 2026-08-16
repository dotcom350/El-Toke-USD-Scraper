"""Fetches the elTOQUE USD rate page through Firecrawl (bypasses the site's
Cloudflare challenge) and parses the returned markdown into structured data.
"""

import re
from datetime import datetime, timezone

import httpx

FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"
SOURCE_URL = "https://eltoque.com/tasas-de-cambio-cuba/dolar"

INFORMAL_TABLE_RE = re.compile(
    r"\|\s*\|\s*([\d.]+)\s*CUP\s*\|\s*([\d.]+)\s*MLC\s*\|\s*([\d.]+)\s*EUR\s*\|"
)
CHANGE_RE = re.compile(
    r"valor referencial de \*\*([\d.,]+)\*\*.*?diferencia de \*\*(-?[\d.,]+)\*\*",
    re.DOTALL,
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


def parse_markdown(markdown: str) -> dict:
    text = _clean(markdown)

    informal: dict = {}
    m = INFORMAL_TABLE_RE.search(text)
    if m:
        informal["usd_to_cup"] = float(m.group(1))
        informal["usd_to_mlc"] = float(m.group(2))
        informal["usd_to_eur"] = float(m.group(3))

    change_m = CHANGE_RE.search(text)
    if change_m:
        informal["change_vs_yesterday_cup"] = float(change_m.group(2).replace(",", "."))

    formal: dict = {}
    for label, pattern in FORMAL_BANKS:
        fm = re.search(pattern, text)
        if fm:
            formal[label] = {"compra": float(fm.group(1)), "venta": float(fm.group(2))}

    label_m = UPDATED_LABEL_RE.search(text)

    return {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "source_url": SOURCE_URL,
        "source_updated_label": label_m.group(0) if label_m else None,
        "informal": informal,
        "formal": formal,
    }


async def scrape_and_parse(api_key: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            FIRECRAWL_SCRAPE_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"url": SOURCE_URL, "formats": ["markdown"]},
        )
        resp.raise_for_status()
        payload = resp.json()

    if not payload.get("success"):
        raise RuntimeError(f"Firecrawl returned an error: {payload}")

    markdown = payload["data"]["markdown"]
    return parse_markdown(markdown)

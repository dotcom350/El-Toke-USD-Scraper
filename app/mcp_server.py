"""Exposes the cached currency rates as an MCP (Model Context Protocol)
server, mounted at /mcp on the main FastAPI app, so MCP clients (Claude
Desktop, Claude Code, etc.) can query rates as tools instead of raw HTTP.
"""

from typing import Callable, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from app.currencies import CURRENCIES, CURRENCIES_BY_CODE

INSTRUCTIONS = (
    "Provides Cuba's currency exchange rates, scraped from elTOQUE. Covers 9 "
    "currencies: USD, EUR, MLC, CAD, MXN, ZELLE (Zelle balance), CLA (classic "
    "card balance), GBP and CHF. Each currency has an 'informal' (street) rate, "
    "an 'formal' (official bank) rate, or both - some currencies only trade "
    "informally (MLC, ZELLE, CLA), others only have an official bank rate (GBP, CHF)."
)


def build_mcp_asgi_app(
    fetch_all: Callable[[], Optional[list]],
    fetch_one: Callable[[str], Optional[dict]],
):
    """fetch_all/fetch_one read from the same in-memory cache the REST API
    uses - calling an MCP tool never triggers a live scrape.
    """
    server = MCPServer(
        name="cuba-currency-rates",
        title="Cuba Currency Rate API",
        instructions=INSTRUCTIONS,
        version="2.0.0",
    )

    @server.tool()
    def list_currencies() -> list[dict]:
        """List every currency code this server has exchange rates for, in
        elTOQUE's own display order, with its display name in English and
        Spanish."""
        return [
            {"code": c["code"], "name_en": c["name_en"], "name_es": c["name_es"]}
            for c in CURRENCIES
        ]

    @server.tool()
    def get_all_rates() -> dict:
        """Get Cuba's current informal (street) and official bank exchange
        rates for all 9 tracked currencies, in elTOQUE's own order. Cached -
        does not trigger a live scrape."""
        rates = fetch_all()
        if rates is None:
            return {"error": "No data yet - the server hasn't completed its first scrape."}
        return {"rates": rates}

    @server.tool()
    def get_rate(code: str) -> dict:
        """Get the current exchange rate for a single currency by its code
        (USD, EUR, MLC, CAD, MXN, ZELLE, CLA, GBP, CHF). Cached - does not
        trigger a live scrape."""
        code = code.strip().upper()
        if code not in CURRENCIES_BY_CODE:
            return {
                "error": f"Unknown currency code: {code}.",
                "valid_codes": list(CURRENCIES_BY_CODE),
            }
        entry = fetch_one(code)
        if entry is None:
            return {"error": f"No data yet for {code} - try again shortly."}
        return entry

    return server.streamable_http_app(
        streamable_http_path="/",
        # Self-hosted on an arbitrary user-chosen domain/port, so we can't
        # pin an allowed Host header in advance. The data served is public,
        # read-only currency rates - no auth, no side effects - so relaxing
        # DNS-rebinding protection here doesn't expose anything sensitive.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

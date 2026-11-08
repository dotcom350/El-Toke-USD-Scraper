"""Registry of every currency elTOQUE publishes a rate for, in the exact
order elTOQUE itself lists them in.

`slug` is the currency's own page on elTOQUE (which carries its informal /
street rate). Currencies without a street rate (GBP, CHF) have no page of
their own anymore - their only data is the bank table on the shared
"mercado-formal" page, so `slug` is None for them.
`formal` marks which currencies appear on that bank-rates page.
"""

SOURCE_BASE = "https://eltoque.com/tasas-de-cambio-cuba"
FORMAL_SLUG = "mercado-formal"

CURRENCIES = [
    {"code": "USD", "slug": "dolar", "formal": True, "name_es": "Dólar estadounidense", "name_en": "US Dollar"},
    {"code": "EUR", "slug": "euro", "formal": True, "name_es": "Euro", "name_en": "Euro"},
    {"code": "MLC", "slug": "mlc", "formal": False, "name_es": "MLC", "name_en": "MLC"},
    {"code": "CAD", "slug": "dolar-canadiense", "formal": True, "name_es": "Dólar canadiense", "name_en": "Canadian Dollar"},
    {"code": "MXN", "slug": "peso-mexicano", "formal": True, "name_es": "Peso mexicano", "name_en": "Mexican Peso"},
    {"code": "ZELLE", "slug": "saldo-zelle", "formal": False, "name_es": "Saldo Zelle", "name_en": "Zelle Balance"},
    {"code": "CLA", "slug": "tarjeta-clasica", "formal": False, "name_es": "Saldo tarjeta clásica", "name_en": "Classic Card Balance"},
    {"code": "GBP", "slug": None, "formal": True, "name_es": "Libra esterlina", "name_en": "British Pound"},
    {"code": "CHF", "slug": None, "formal": True, "name_es": "Franco suizo", "name_en": "Swiss Franc"},
]

CURRENCIES_BY_CODE = {c["code"]: c for c in CURRENCIES}


def source_url(currency: dict) -> str:
    return f"{SOURCE_BASE}/{currency['slug'] or FORMAL_SLUG}"

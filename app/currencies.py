"""Registry of every currency elTOQUE publishes a rate for, in the exact
order elTOQUE itself lists them in (its cross-page "otras monedas" nav).
"""

SOURCE_BASE = "https://eltoque.com/tasas-de-cambio-cuba"

CURRENCIES = [
    {"code": "USD", "slug": "dolar", "name_es": "Dólar estadounidense", "name_en": "US Dollar"},
    {"code": "EUR", "slug": "euro", "name_es": "Euro", "name_en": "Euro"},
    {"code": "MLC", "slug": "mlc", "name_es": "MLC", "name_en": "MLC"},
    {"code": "CAD", "slug": "dolar-canadiense", "name_es": "Dólar canadiense", "name_en": "Canadian Dollar"},
    {"code": "MXN", "slug": "peso-mexicano", "name_es": "Peso mexicano", "name_en": "Mexican Peso"},
    {"code": "ZELLE", "slug": "saldo-zelle", "name_es": "Saldo Zelle", "name_en": "Zelle Balance"},
    {"code": "CLA", "slug": "tarjeta-clasica", "name_es": "Saldo tarjeta clásica", "name_en": "Classic Card Balance"},
    {"code": "GBP", "slug": "libra-esterlina", "name_es": "Libra esterlina", "name_en": "British Pound"},
    {"code": "CHF", "slug": "franco-suizo", "name_es": "Franco suizo", "name_en": "Swiss Franc"},
]

CURRENCIES_BY_CODE = {c["code"]: c for c in CURRENCIES}

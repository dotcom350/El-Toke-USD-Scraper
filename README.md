# Cuba USD Rate API

[🇪🇸 Español](#) · [🇬🇧 English](README.en.md)

API JSON gratuita para la tasa informal (calle) y las tasas oficiales de bancos de **las 9 monedas que publica elTOQUE** (USD, EUR, MLC, CAD, MXN, ZELLE, CLA, GBP, CHF), scrapeadas desde [elTOQUE](https://eltoque.com/tasas-de-cambio-cuba) usando [Firecrawl](https://www.firecrawl.dev) (necesario porque el sitio está detrás de un challenge de Cloudflare que un `curl`/`requests` normal no puede pasar).

Landing page en vivo: `http://localhost:8000/` (o donde la despliegues) — español por defecto (con toggle a inglés), modo claro/oscuro (oscuro es negro puro), responsive para móviles, muestra la tasa actual con animación de conteo y una tabla con las 9 monedas, e incluye ejemplos de código listos para copiar.

También es un **servidor [MCP](https://modelcontextprotocol.io)** (`/mcp/`), así que agentes como Claude pueden consultar las tasas como *tools* en vez de HTTP crudo — ver la sección [Conectar vía MCP](#conectar-vía-mcp) abajo.

---

## Cómo funciona

- Un scheduler en segundo plano scrapea, **secuencialmente**, las 9 páginas de moneda de elTOQUE vía Firecrawl cada `SCRAPE_INTERVAL_MINUTES` (480 = 8h por defecto) y cachea el resultado parseado en `data/cache.json`. Es secuencial (no en paralelo) porque Firecrawl devuelve 429 (rate limit) si le mandas varios scrapes a la vez; cada llamada reintenta automáticamente con backoff si eso pasa.
- Las solicitudes a la API **nunca** disparan un scrape en vivo — solo leen el caché, así que las respuestas son instantáneas y no gastan créditos de Firecrawl.
- Cada respuesta incluye `requested_at`, calculado en el instante exacto en que la solicitud llega al servidor — **no** la hora en que se scrapeó la data (eso es `data.scraped_at` / `generated_at`).
- No todas las monedas tienen ambos mercados: MLC, ZELLE (saldo Zelle) y CLA (saldo tarjeta clásica) solo se cambian de forma informal (no hay mostrador de banco); GBP y CHF solo tienen tasa oficial de banco (elTOQUE no seguirle la pista al mercado informal).

### Matemática de créditos de Firecrawl

El tier gratuito de Firecrawl da **1000 créditos/mes**, y una llamada normal a `/v2/scrape` cuesta **1 crédito**. Cada ciclo de refresco scrapea las **9 monedas**, así que el costo es 9x el de una API de una sola moneda.

| Intervalo | Scrapes/día | Créditos/mes | ¿Entra en 1000? |
| --- | --- | --- | --- |
| 60 min | 9 × 24 = 216 | ~6,480 | No |
| 240 min (4h) | 9 × 6 = 54 | ~1,620 | No |
| 360 min (6h) | 9 × 4 = 36 | ~1,080 | Casi (se pasa un poco) |
| **480 min / 8h (por defecto)** | **9 × 3 = 27** | **~810** | **Sí, ~190 créditos de margen** |
| 720 min (12h) | 9 × 2 = 18 | ~540 | Sí, mucho margen |

El valor por defecto (cada 8h) deja margen para pruebas manuales, reinicios, y el scrape inmediato que corre cada vez que arranca la app.

---

## Inicio rápido (Docker Compose)

```bash
git clone https://github.com/dotcom350/El-Toke-USD-Scraper.git
cd El-Toke-USD-Scraper
cp .env.example .env
# edita .env y pon FIRECRAWL_API_KEY=fc-...
docker compose up -d --build
```

Luego abre `http://localhost:8000/` para la landing page, o llama a la API directamente:

```bash
curl http://localhost:8000/api/v2/rates
```

---

## Endpoints

| Método | Ruta | Descripción |
| --- | --- | --- |
| `GET` | `/api/v2/rates` | Las 9 monedas, en el orden propio de elTOQUE |
| `GET` | `/api/v2/rates/{code}` | Una sola moneda por código (`USD`, `EUR`, `MLC`, `CAD`, `MXN`, `ZELLE`, `CLA`, `GBP`, `CHF`) |
| `GET` | `/api/v1/dolar` | Solo USD, con el formato original (mantenido por compatibilidad) |
| `GET` | `/api/v1/health` | Estado del servicio y hora del último scrape exitoso |
| `ANY` | `/mcp/` | Servidor MCP (Streamable HTTP) — ver abajo |
| `GET` | `/docs` | Documentación interactiva Swagger / OpenAPI |
| `GET` | `/` | Landing page bilingüe |

## Conectar vía MCP

La API expone un servidor [MCP](https://modelcontextprotocol.io) en `/mcp/` con 3 tools: `list_currencies`, `get_all_rates` y `get_rate(code)`. Igual que la API REST, las tools solo leen el caché — no disparan un scrape en vivo. Sin API key.

Con [Claude Code](https://claude.com/claude-code):

```bash
claude mcp add --transport http cuba-rates http://localhost:8000/mcp/
```

O en la config JSON de cualquier cliente MCP:

```json
{
  "mcpServers": {
    "cuba-rates": {
      "type": "http",
      "url": "http://localhost:8000/mcp/"
    }
  }
}
```

> El servidor se auto-aloja en un dominio/puerto que tú eliges, así que la protección DNS-rebinding de MCP viene desactivada por defecto (el dato que sirve es público y de solo lectura, sin efectos secundarios). Si lo despliegas en un dominio fijo y quieres esa protección, configúrala en `app/mcp_server.py` (`TransportSecuritySettings`).

### Respuesta de ejemplo — `/api/v2/rates/EUR`

Esta es una respuesta real, capturada en vivo de la API corriendo:

```json
{
  "success": true,
  "requested_at": "2026-08-16T02:20:09.819429+00:00",
  "interval_minutes": 480,
  "data": {
    "code": "EUR",
    "name_es": "Euro",
    "name_en": "Euro",
    "scraped_at": "2026-08-16T02:13:01.463799+00:00",
    "source_url": "https://eltoque.com/tasas-de-cambio-cuba/euro",
    "source_updated_label": "15 de agosto de 2026 a las 08:03 a. m.",
    "informal": {
      "cup": 770.0,
      "conversions": { "USD": 1.16, "MLC": 1.63 },
      "change_vs_yesterday_cup": 0.0
    },
    "formal": {
      "BPA": { "compra": 694.37, "venta": 722.71 },
      "BANDEC": { "compra": 663.35, "venta": 690.43 },
      "BANMET Efectivo": { "compra": 692.44, "venta": 720.70 },
      "BANMET Transferencia": { "compra": 706.57, "venta": 717.17 },
      "CADECA Casas de cambio": { "compra": 669.53, "venta": 696.31 },
      "CADECA Hoteles y Aeropuertos": { "compra": 687.29, "venta": 751.05 }
    }
  }
}
```

`informal` es `null` para monedas sin mercado informal (GBP, CHF); `formal` es `null` para monedas sin mostrador de banco (MLC, ZELLE, CLA).

`/api/v1/dolar` mantiene el formato original (`informal.usd_to_cup`, `usd_to_mlc`, `usd_to_eur`) para no romper a quien ya lo esté usando — internamente lee del mismo caché compartido, no dispara un scrape aparte.

---

## Configuración

Variables de entorno (ver `.env.example`):

| Variable | Por defecto | Descripción |
| --- | --- | --- |
| `FIRECRAWL_API_KEY` | — (requerida) | Tu API key de Firecrawl. Consigue una gratis en firecrawl.dev. |
| `SCRAPE_INTERVAL_MINUTES` | `480` | Minutos entre scrapes (de las 9 monedas). Ver la matemática de créditos arriba. |
| `PORT` | `8000` | Puerto del host expuesto por docker compose. |

---

## Estructura del proyecto

```
app/
  main.py          # app FastAPI, endpoints v1/v2, scheduler
  currencies.py      # registro de las 9 monedas, en el orden de elTOQUE
  scraper.py         # llamada a Firecrawl + parseo de markdown a JSON estructurado
  cache.py           # caché JSON thread-safe (memoria + disco)
  mcp_server.py       # servidor MCP (mismas tools que la API, mismo caché)
  templates/
    index.html       # landing page bilingüe
  static/
    style.css
data/
  cache.json         # se crea en tiempo de ejecución, ignorado por git
Dockerfile
docker-compose.yml
requirements.txt
.env.example
```

## Correr sin Docker

```bash
python -m venv .venv
.venv/Scripts/activate   # Windows
pip install -r requirements.txt
cp .env.example .env     # y edítalo
uvicorn app.main:app --reload
```

---

## Aviso legal

Este proyecto **no está afiliado a elTOQUE**. Toda la data de tasas de cambio pertenece a y es publicada por [eltoque.com](https://eltoque.com/tasas-de-cambio-cuba), esta API simplemente la re-sirve en formato JSON por conveniencia. Provisto solo con fines informativos — verifica decisiones importantes contra la fuente original.

## Licencia

MIT — ver [LICENSE](LICENSE).

# Cuba USD Rate API

[🇪🇸 Español](#) · [🇬🇧 English](README.en.md)

API JSON gratuita para la tasa informal (calle) y las tasas oficiales de bancos del dólar en Cuba, scrapeada cada hora desde [elTOQUE](https://eltoque.com/tasas-de-cambio-cuba/dolar) usando [Firecrawl](https://www.firecrawl.dev) (necesario porque el sitio está detrás de un challenge de Cloudflare que un `curl`/`requests` normal no puede pasar).

Landing page en vivo: `http://localhost:8000/` (o donde la despliegues) — bilingüe, muestra la tasa actual, e incluye ejemplos de código listos para copiar.

---

## Cómo funciona

- Un scheduler en segundo plano scrapea elTOQUE vía Firecrawl cada `SCRAPE_INTERVAL_MINUTES` (60 por defecto) y cachea el resultado parseado en `data/cache.json`.
- Las solicitudes a la API **nunca** disparan un scrape en vivo — solo leen el caché, así que las respuestas son instantáneas y no gastan créditos de Firecrawl.
- Cada respuesta incluye `requested_at`, calculado en el instante exacto en que la solicitud llega al servidor — **no** la hora en que se scrapeó la data (eso es `data.scraped_at`).

### Matemática de créditos de Firecrawl

El tier gratuito de Firecrawl da **1000 créditos/mes**, y una llamada normal a `/v2/scrape` cuesta **1 crédito**.

| Intervalo | Scrapes/día | Créditos/mes | ¿Entra en 1000? |
| --- | --- | --- | --- |
| 15 min | 96 | ~2,880 | No |
| 30 min | 48 | ~1,440 | No |
| **60 min (por defecto)** | **24** | **~720-744** | **Sí, ~260 créditos de margen** |
| 120 min | 12 | ~360 | Sí, mucho margen |

El valor por defecto (cada hora) deja margen para pruebas manuales, reinicios, y el scrape inmediato que corre cada vez que arranca la app.

---

## Inicio rápido (Docker Compose)

```bash
git clone https://github.com/tu-usuario/cuba-usd-rate-api.git
cd cuba-usd-rate-api
cp .env.example .env
# edita .env y pon FIRECRAWL_API_KEY=fc-...
docker compose up -d --build
```

Luego abre `http://localhost:8000/` para la landing page, o llama a la API directamente:

```bash
curl http://localhost:8000/api/v1/dolar
```

---

## Endpoints

| Método | Ruta | Descripción |
| --- | --- | --- |
| `GET` | `/api/v1/dolar` | Tasas de USD informal + oficiales, como JSON cacheado |
| `GET` | `/api/v1/health` | Estado del servicio y hora del último scrape exitoso |
| `GET` | `/docs` | Documentación interactiva Swagger / OpenAPI |
| `GET` | `/` | Landing page bilingüe |

### Respuesta de ejemplo

```json
{
  "success": true,
  "requested_at": "2026-08-15T14:32:07.123456+00:00",
  "interval_minutes": 60,
  "data": {
    "scraped_at": "2026-08-15T14:00:11.905000+00:00",
    "source_url": "https://eltoque.com/tasas-de-cambio-cuba/dolar",
    "source_updated_label": "15 de agosto de 2026 a las 08:03 a. m.",
    "informal": {
      "usd_to_cup": 665.0,
      "usd_to_mlc": 1.41,
      "usd_to_eur": 0.86,
      "change_vs_yesterday_cup": 0.0
    },
    "formal": {
      "BPA": { "compra": 602.70, "venta": 627.30 },
      "BANDEC": { "compra": 580.16, "venta": 603.84 },
      "BANMET Efectivo": { "compra": 602.70, "venta": 627.30 },
      "BANMET Transferencia": { "compra": 615.00, "venta": 624.23 },
      "CADECA Casas de cambio": { "compra": 585.00, "venta": 608.40 },
      "CADECA Hoteles y Aeropuertos": { "compra": 596.55, "venta": 651.90 }
    }
  }
}
```

---

## Configuración

Variables de entorno (ver `.env.example`):

| Variable | Por defecto | Descripción |
| --- | --- | --- |
| `FIRECRAWL_API_KEY` | — (requerida) | Tu API key de Firecrawl. Consigue una gratis en firecrawl.dev. |
| `SCRAPE_INTERVAL_MINUTES` | `60` | Minutos entre scrapes. Ver la matemática de créditos arriba. |
| `PORT` | `8000` | Puerto del host expuesto por docker compose. |

---

## Estructura del proyecto

```
app/
  main.py          # app FastAPI, endpoints, scheduler
  scraper.py        # llamada a Firecrawl + parseo de markdown a JSON estructurado
  cache.py          # caché JSON thread-safe (memoria + disco)
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

Este proyecto **no está afiliado a elTOQUE**. Toda la data de tasas de cambio pertenece a y es publicada por [eltoque.com](https://eltoque.com/tasas-de-cambio-cuba/dolar); esta API simplemente la re-sirve en formato JSON por conveniencia. Provisto solo con fines informativos — verifica decisiones importantes contra la fuente original.

## Licencia

MIT — ver [LICENSE](LICENSE).

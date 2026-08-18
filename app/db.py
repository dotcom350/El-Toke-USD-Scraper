"""Postgres access for the qvai Telegram bot: bot settings (token, API key,
personality/system prompt, model params) and the full conversation log.

The connection pool is optional at import time - if DATABASE_URL isn't set
or Postgres isn't reachable, pool_ready() stays False and the admin panel /
bot simply report themselves as unavailable instead of crashing the whole
app (the currency API itself never depends on this module).
"""

import logging
import os
from typing import Optional

import asyncpg

logger = logging.getLogger("qvai-db")

DATABASE_URL = os.getenv("DATABASE_URL", "")

DEFAULT_SYSTEM_PROMPT = (
    "Eres qvai, un asistente de IA amigable y conversacional dentro de un bot de Telegram. "
    "Ayudas a la gente a saber las tasas de cambio de monedas en Cuba (dólar, euro, MLC, "
    "y otras) de forma clara y cercana, y también puedes conversar sobre cualquier otro tema "
    "con calidez y naturalidad, como lo haría un amigo.\n\n"
    "Estas son las tasas de cambio en tiempo real ahora mismo (úsalas para responder "
    "preguntas sobre precios, no inventes números):\n{RATES}\n\n"
    "De vez en cuando, solo cuando venga a cuento de forma natural en la conversación (no en "
    "cada mensaje, no lo fuerces), menciona que este bot es un proyecto de ID Academy "
    "(impactodigital.vip).\n\n"
    "Responde siempre en español, de forma breve y clara."
)

DEFAULT_WELCOME_MESSAGE = (
    "¡Hola! Soy *qvai* 🤖, tu asistente de IA en Telegram.\n\n"
    "Pregúntame por el precio del dólar, euro, MLC y las demás monedas en Cuba, o simplemente "
    "conversa conmigo de lo que quieras.\n\n"
    "Escribe /help para ver todos los comandos disponibles."
)

DEFAULT_HELP_MESSAGE = (
    "📚 *Comandos Disponibles*\n\n"
    "/start Iniciar Bot 🚀\n"
    "Este comando reinicia el bot y da la bienvenida al usuario. Úsalo para comenzar de nuevo "
    "o para recibir una nueva bienvenida.\n\n"
    "/stop Parar Bot 🛑\n"
    "Este comando detiene cualquier función o comando en ejecución y regresa al modo normal. "
    "Útil si deseas interrumpir cualquier proceso en curso.\n\n"
    "/help Comando de Ayuda 📖\n"
    "Este comando muestra una lista de todos los comandos disponibles y sus descripciones, "
    "ayudando al usuario a entender cómo utilizar QVAi de manera efectiva.\n\n"
    "/dev Modo Programador 🛠️\n"
    "En este modo, QVAi actúa como asistente y experto en desarrollo web. Puede ayudar con "
    "cualquier lenguaje de programación. El usuario debe decir qué quiere hacer, y QVAi lo "
    "guiará paso a paso en el proceso.\n\n"
    "/traductor Modo Translator 1.0 🌐\n"
    "Cuando está activado, QVAi se convierte en un asistente amigable y experto en "
    "traducción. El usuario puede indicar a qué idioma desea traducir el texto, y QVAi se "
    "encargará de traducirlo.\n\n"
    "/preciodolar Tasa de cambio del dólar en Cuba 💵\n"
    "Con este comando, QVAi proporciona la tasa de cambio actualizada del dólar en Cuba. El "
    "usuario recibirá la información más reciente sobre la moneda local.\n\n"
    "QVAi es un asistente amigable y experto creado por @DAVIDRT20, disponible para ayudar "
    "con muchas cosas."
)

DEFAULT_STOP_MESSAGE = "✅ Saliste del modo actual. Volví al modo normal - pregúntame lo que quieras."

DEFAULT_DEV_ACTIVATION_MESSAGE = (
    "🛠️ *Modo Programador activado*\n\n"
    "Ahora soy tu asistente experto en desarrollo web y programación. Cuéntame qué quieres "
    "construir o resolver y te guío paso a paso, en el lenguaje o framework que necesites.\n\n"
    "Escribe /stop cuando quieras salir de este modo."
)

DEFAULT_DEV_SYSTEM_PROMPT = (
    "Eres qvai en Modo Programador: un asistente y experto en desarrollo web y en cualquier "
    "lenguaje de programación (Python, JavaScript, HTML/CSS, SQL, etc). El usuario te va a "
    "decir qué quiere construir o el problema que tiene, y tú lo guías paso a paso, con "
    "ejemplos de código claros y explicaciones concisas. Responde en español."
)

DEFAULT_TRANSLATOR_ACTIVATION_MESSAGE = (
    "🌐 *Modo Translator 1.0 activado*\n\n"
    "Dime a qué idioma quieres traducir, y después mándame el texto (o mándame el texto y el "
    "idioma de una vez).\n\n"
    "Escribe /stop cuando quieras salir de este modo."
)

DEFAULT_TRANSLATOR_SYSTEM_PROMPT = (
    "Eres qvai en Modo Translator 1.0: un asistente experto y amigable en traducción. El "
    "usuario te indicará a qué idioma quiere traducir un texto (o quedará claro por "
    "contexto) y tú traduces con precisión y naturalidad, devolviendo solo la traducción a "
    "menos que te pidan una explicación."
)

DEFAULT_PRICE_DOLAR_TEMPLATE = (
    "💵 *Tasa de cambio del dólar en Cuba*\n\n"
    "Informal: *{cup} CUP*\n"
    "1 USD también equivale a: {mlc} MLC · {eur} EUR\n"
    "Bancos (CADECA): {compra} / {venta} CUP\n\n"
    "Actualizado: {updated}"
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bot_settings (
    id INTEGER PRIMARY KEY DEFAULT 1,
    bot_name TEXT NOT NULL DEFAULT 'qvai',
    telegram_token TEXT,
    nvidia_api_key TEXT,
    model TEXT NOT NULL DEFAULT 'nvidia/nemotron-3.5-lightning-30b-a3b',
    system_prompt TEXT NOT NULL DEFAULT '',
    temperature DOUBLE PRECISION NOT NULL DEFAULT 0.7,
    max_tokens INTEGER NOT NULL DEFAULT 1024,
    history_limit INTEGER NOT NULL DEFAULT 10,
    enabled BOOLEAN NOT NULL DEFAULT true,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (id = 1)
);

CREATE TABLE IF NOT EXISTS conversations (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    telegram_user_id BIGINT,
    username TEXT,
    first_name TEXT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_conversations_chat_id ON conversations (chat_id, created_at);

-- Per-chat active mode (normal / dev / traductor), so a plain text message
-- knows which system prompt to use without the user repeating the command.
CREATE TABLE IF NOT EXISTS chat_state (
    chat_id BIGINT PRIMARY KEY,
    mode TEXT NOT NULL DEFAULT 'normal',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# Added after the initial release - kept as ALTER TABLE ... ADD COLUMN IF NOT
# EXISTS (rather than folding into SCHEMA_SQL's CREATE TABLE) so existing
# installs pick them up without a manual migration step. Defaults are filled
# in via a parameterized UPDATE afterwards rather than embedded in the DDL,
# so none of this free-form text needs SQL-escaping.
MESSAGE_COLUMNS = {
    "welcome_message": DEFAULT_WELCOME_MESSAGE,
    "help_message": DEFAULT_HELP_MESSAGE,
    "stop_message": DEFAULT_STOP_MESSAGE,
    "dev_activation_message": DEFAULT_DEV_ACTIVATION_MESSAGE,
    "dev_system_prompt": DEFAULT_DEV_SYSTEM_PROMPT,
    "translator_activation_message": DEFAULT_TRANSLATOR_ACTIVATION_MESSAGE,
    "translator_system_prompt": DEFAULT_TRANSLATOR_SYSTEM_PROMPT,
    "price_dolar_template": DEFAULT_PRICE_DOLAR_TEMPLATE,
}

_pool: Optional[asyncpg.Pool] = None


def pool_ready() -> bool:
    return _pool is not None


async def init_pool() -> None:
    global _pool
    if not DATABASE_URL:
        logger.warning("DATABASE_URL not set - admin panel and qvai bot will be unavailable.")
        return
    try:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        async with _pool.acquire() as conn:
            await conn.execute(SCHEMA_SQL)
            # REAL (float32) can't represent 0.7 exactly, so it round-trips
            # back as 0.699999988079071. Migrate older installs that still
            # have the REAL column to DOUBLE PRECISION.
            await conn.execute("ALTER TABLE bot_settings ALTER COLUMN temperature TYPE DOUBLE PRECISION")
            await conn.execute("ALTER TABLE bot_settings ADD COLUMN IF NOT EXISTS history_limit INTEGER NOT NULL DEFAULT 10")
            await conn.execute(
                "INSERT INTO bot_settings (id, system_prompt) VALUES (1, $1) ON CONFLICT (id) DO NOTHING",
                DEFAULT_SYSTEM_PROMPT,
            )
            for column, default_value in MESSAGE_COLUMNS.items():
                await conn.execute(f"ALTER TABLE bot_settings ADD COLUMN IF NOT EXISTS {column} TEXT")
                await conn.execute(
                    f"UPDATE bot_settings SET {column} = $1 WHERE id = 1 AND {column} IS NULL",
                    default_value,
                )
        logger.info("Postgres pool ready")
    except Exception:
        logger.exception("Could not connect to Postgres - admin panel and qvai bot will be unavailable.")
        _pool = None


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def get_settings() -> dict:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM bot_settings WHERE id = 1")
        return dict(row) if row else {}


async def update_settings(**fields) -> dict:
    if not fields:
        return await get_settings()
    set_clause = ", ".join(f"{key} = ${i + 1}" for i, key in enumerate(fields))
    values = list(fields.values())
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE bot_settings SET {set_clause}, updated_at = now() WHERE id = 1 RETURNING *",
            *values,
        )
        return dict(row)


async def log_message(
    chat_id: int,
    user_id: Optional[int],
    username: Optional[str],
    first_name: Optional[str],
    role: str,
    content: str,
) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO conversations (chat_id, telegram_user_id, username, first_name, role, content)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            chat_id,
            user_id,
            username,
            first_name,
            role,
            content,
        )


async def get_chat_mode(chat_id: int) -> str:
    async with _pool.acquire() as conn:
        mode = await conn.fetchval("SELECT mode FROM chat_state WHERE chat_id = $1", chat_id)
        return mode or "normal"


async def set_chat_mode(chat_id: int, mode: str) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO chat_state (chat_id, mode, updated_at) VALUES ($1, $2, now())
            ON CONFLICT (chat_id) DO UPDATE SET mode = $2, updated_at = now()
            """,
            chat_id,
            mode,
        )


async def get_recent_messages(chat_id: int, limit: int = 16) -> list[dict]:
    """Most recent `limit` messages for a chat, oldest first (ready to feed
    straight into a chat-completion `messages` list)."""
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT role, content FROM conversations WHERE chat_id = $1 ORDER BY created_at DESC LIMIT $2",
            chat_id,
            limit,
        )
        return [dict(r) for r in reversed(rows)]


async def list_chats(limit: int = 200) -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                chat_id,
                COALESCE(NULLIF(MAX(username), ''), '') AS username,
                COALESCE(NULLIF(MAX(first_name), ''), '') AS first_name,
                COUNT(*) AS message_count,
                MAX(created_at) AS last_message_at,
                (ARRAY_AGG(content ORDER BY created_at DESC))[1] AS last_message
            FROM conversations
            GROUP BY chat_id
            ORDER BY last_message_at DESC
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]


async def get_conversation(chat_id: int) -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT role, content, created_at, username, first_name
            FROM conversations
            WHERE chat_id = $1
            ORDER BY created_at ASC
            """,
            chat_id,
        )
        return [dict(r) for r in rows]


async def stats() -> dict:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS total_messages, COUNT(DISTINCT chat_id) AS total_chats FROM conversations"
        )
        return dict(row)

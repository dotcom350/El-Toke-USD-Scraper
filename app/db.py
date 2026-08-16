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
"""

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
            await conn.execute(
                "INSERT INTO bot_settings (id, system_prompt) VALUES (1, $1) ON CONFLICT (id) DO NOTHING",
                DEFAULT_SYSTEM_PROMPT,
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

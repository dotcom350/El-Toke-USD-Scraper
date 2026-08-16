"""The qvai Telegram bot: a conversational AI assistant that can answer
questions about Cuba's currency rates (using the same live cache as the
REST API) and chat about anything else, powered by NVIDIA's
OpenAI-compatible chat completions API. Every message and reply is logged
to Postgres so the admin panel can show full conversation history.

Runs via long polling rather than a webhook, since the admin panel lets the
token be changed at runtime - restarting a polling loop is a simple
stop/start, while a webhook would need to be re-registered with Telegram
on every token change.
"""

import asyncio
import logging
from typing import Callable, Optional

from openai import AsyncOpenAI
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app import db

logger = logging.getLogger("qvai-bot")

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"


def format_rates_summary(rates: Optional[list]) -> str:
    if not rates:
        return "(todavía no hay datos de tasas cargados)"
    lines = []
    for r in rates:
        bits = [r["code"]]
        informal = r.get("informal")
        if informal:
            bits.append(f"{informal['cup']} CUP (informal)")
        formal = r.get("formal")
        if formal:
            cadeca = formal.get("CADECA Casas de cambio")
            if cadeca:
                bits.append(f"banco {cadeca['compra']}/{cadeca['venta']} CUP")
        lines.append(" - ".join(bits))
    return "\n".join(lines)


class BotManager:
    """Owns the python-telegram-bot Application instance and can be
    stopped/restarted at runtime when the admin changes the token."""

    def __init__(self, get_rates: Callable[[], Optional[list]]):
        self._get_rates = get_rates
        self.app: Optional[Application] = None
        self.running = False

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        settings = await db.get_settings()
        name = settings.get("bot_name") or "qvai"
        await update.message.reply_text(
            f"¡Hola! Soy {name}. Pregúntame por el precio del dólar, euro, MLC y las demás "
            "monedas, o simplemente conversa conmigo de lo que quieras."
        )

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return

        settings = await db.get_settings()
        if not settings.get("enabled", True) or not settings.get("nvidia_api_key"):
            await update.message.reply_text(
                "Todavía no estoy del todo configurado - avísale al administrador."
            )
            return

        chat = update.effective_chat
        user = update.effective_user
        text = update.message.text
        chat_id = chat.id if chat else None
        user_id = user.id if user else None
        username = user.username if user else None
        first_name = user.first_name if user else None

        await db.log_message(chat_id, user_id, username, first_name, "user", text)

        rates_text = format_rates_summary(self._get_rates())
        system_prompt = (settings.get("system_prompt") or db.DEFAULT_SYSTEM_PROMPT).replace(
            "{RATES}", rates_text
        )
        history = await db.get_recent_messages(chat_id, limit=16)

        messages = [{"role": "system", "content": system_prompt}]
        messages += [{"role": m["role"], "content": m["content"]} for m in history]
        messages.append({"role": "user", "content": text})

        client = AsyncOpenAI(base_url=NVIDIA_BASE_URL, api_key=settings["nvidia_api_key"])
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            completion = await client.chat.completions.create(
                model=settings.get("model") or DEFAULT_MODEL,
                messages=messages,
                temperature=float(settings.get("temperature") or 0.7),
                max_tokens=int(settings.get("max_tokens") or 1024),
            )
            reply = completion.choices[0].message.content or "..."
        except Exception:
            logger.exception("NVIDIA completion failed")
            reply = "Se me trabó el cerebro un segundo - intenta de nuevo en un momento."

        await db.log_message(chat_id, user_id, username, first_name, "assistant", reply)
        await update.message.reply_text(reply)

    async def start(self, token: str) -> None:
        if self.running:
            await self.stop()
        if not token:
            return
        try:
            self.app = Application.builder().token(token).build()
            self.app.add_handler(CommandHandler("start", self.handle_start))
            self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

            async def _connect():
                await self.app.initialize()
                await self.app.start()
                await self.app.updater.start_polling(drop_pending_updates=True)

            # An invalid/malformed-but-plausible token can otherwise hang here
            # indefinitely (python-telegram-bot retries network errors rather
            # than failing fast) - bound it so a bad token just fails cleanly.
            await asyncio.wait_for(_connect(), timeout=20)
            self.running = True
            logger.info("qvai bot started polling")
        except Exception:
            logger.exception("Failed to start qvai bot - check the Telegram token")
            self.app = None
            self.running = False

    async def stop(self) -> None:
        if not self.app:
            return
        try:
            if self.app.updater and self.app.updater.running:
                await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
        except Exception:
            logger.exception("Error stopping qvai bot")
        finally:
            self.app = None
            self.running = False
            logger.info("qvai bot stopped")

    async def restart(self, token: Optional[str]) -> None:
        await self.stop()
        if token:
            await self.start(token)

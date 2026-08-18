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
from datetime import datetime
from typing import Callable, Optional

from openai import AsyncOpenAI
from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app import db

logger = logging.getLogger("qvai-bot")

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"

# mode -> (settings key holding that mode's system prompt, fallback constant)
MODE_PROMPTS = {
    "dev": ("dev_system_prompt", db.DEFAULT_DEV_SYSTEM_PROMPT),
    "traductor": ("translator_system_prompt", db.DEFAULT_TRANSLATOR_SYSTEM_PROMPT),
}

BOT_COMMANDS = [
    BotCommand("start", "Iniciar Bot 🚀"),
    BotCommand("stop", "Parar Bot 🛑"),
    BotCommand("help", "Comando de Ayuda 📖"),
    BotCommand("dev", "Modo Programador 🛠️"),
    BotCommand("traductor", "Modo Translator 1.0 🌐"),
    BotCommand("preciodolar", "Tasa de cambio del dólar 💵"),
]


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


def _find_currency(rates: Optional[list], code: str) -> Optional[dict]:
    for r in rates or []:
        if r.get("code") == code:
            return r
    return None


async def _reply(update: Update, text: str) -> None:
    """Sends with Telegram Markdown, falling back to plain text if the
    admin-edited content has unbalanced markdown that Telegram refuses to
    parse (e.g. a stray `*` or `_`) - a broken reply is worse than a
    plain-text one.
    """
    try:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        await update.message.reply_text(text)


class BotManager:
    """Owns the python-telegram-bot Application instance and can be
    stopped/restarted at runtime when the admin changes the token."""

    def __init__(self, get_rates: Callable[[], Optional[list]]):
        self._get_rates = get_rates
        self.app: Optional[Application] = None
        self.running = False

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        if chat:
            await db.set_chat_mode(chat.id, "normal")
        settings = await db.get_settings()
        await _reply(update, settings.get("welcome_message") or db.DEFAULT_WELCOME_MESSAGE)

    async def handle_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        if chat:
            await db.set_chat_mode(chat.id, "normal")
        settings = await db.get_settings()
        await _reply(update, settings.get("stop_message") or db.DEFAULT_STOP_MESSAGE)

    async def handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        settings = await db.get_settings()
        await _reply(update, settings.get("help_message") or db.DEFAULT_HELP_MESSAGE)

    async def handle_dev(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        if chat:
            await db.set_chat_mode(chat.id, "dev")
        settings = await db.get_settings()
        await _reply(update, settings.get("dev_activation_message") or db.DEFAULT_DEV_ACTIVATION_MESSAGE)

    async def handle_traductor(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        if chat:
            await db.set_chat_mode(chat.id, "traductor")
        settings = await db.get_settings()
        await _reply(
            update, settings.get("translator_activation_message") or db.DEFAULT_TRANSLATOR_ACTIVATION_MESSAGE
        )

    async def handle_preciodolar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        settings = await db.get_settings()
        usd = _find_currency(self._get_rates(), "USD")
        if not usd or not usd.get("informal"):
            await _reply(update, "Todavía no tengo la tasa cargada - intenta en un momento.")
            return
        informal = usd["informal"]
        conversions = informal.get("conversions") or {}
        cadeca = (usd.get("formal") or {}).get("CADECA Casas de cambio") or {}
        scraped_at = usd.get("scraped_at")
        updated = scraped_at
        if scraped_at:
            try:
                updated = datetime.fromisoformat(scraped_at).astimezone().strftime("%d/%m/%Y %H:%M")
            except ValueError:
                pass
        template = settings.get("price_dolar_template") or db.DEFAULT_PRICE_DOLAR_TEMPLATE
        text = template.format(
            cup=informal.get("cup", "—"),
            mlc=conversions.get("MLC", "—"),
            eur=conversions.get("EUR", "—"),
            compra=cadeca.get("compra", "—"),
            venta=cadeca.get("venta", "—"),
            updated=updated or "—",
        )
        await _reply(update, text)

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

        mode = await db.get_chat_mode(chat_id) if chat_id else "normal"
        mode_key, mode_fallback = MODE_PROMPTS.get(mode, (None, None))
        if mode_key:
            system_prompt = settings.get(mode_key) or mode_fallback
        else:
            rates_text = format_rates_summary(self._get_rates())
            system_prompt = (settings.get("system_prompt") or db.DEFAULT_SYSTEM_PROMPT).replace(
                "{RATES}", rates_text
            )
        history_limit = int(settings.get("history_limit") or 10)
        history = await db.get_recent_messages(chat_id, limit=history_limit)

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
        await _reply(update, reply)

    async def start(self, token: str) -> None:
        if self.running:
            await self.stop()
        if not token:
            return
        try:
            self.app = Application.builder().token(token).build()
            self.app.add_handler(CommandHandler("start", self.handle_start))
            self.app.add_handler(CommandHandler("stop", self.handle_stop))
            self.app.add_handler(CommandHandler("help", self.handle_help))
            self.app.add_handler(CommandHandler("dev", self.handle_dev))
            self.app.add_handler(CommandHandler("traductor", self.handle_traductor))
            self.app.add_handler(CommandHandler("preciodolar", self.handle_preciodolar))
            self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

            async def _connect():
                await self.app.initialize()
                await self.app.start()
                await self.app.bot.set_my_commands(BOT_COMMANDS)
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

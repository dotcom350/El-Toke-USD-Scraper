"""Login-protected admin panel: view qvai's Telegram conversation history,
and edit its behavior (system prompt, model params) and credentials
(Telegram token, NVIDIA API key) at runtime.
"""

import asyncio
import os
import secrets

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import db
from app.assets import content_hash
from app.bot import BotManager

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

templates = Jinja2Templates(directory="app/templates")

STYLE_VERSION = content_hash("app/static/style.css")
ADMIN_STYLE_VERSION = content_hash("app/static/admin.css")


def _render(request: Request, name: str, context: dict, status_code: int = 200) -> HTMLResponse:
    ctx = {"static_version": STYLE_VERSION, "admin_static_version": ADMIN_STYLE_VERSION, **context}
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def _is_authed(request: Request) -> bool:
    return bool(request.session.get("admin_authed"))


def _require_auth(request: Request):
    """Returns a redirect response if not logged in, else None."""
    if not _is_authed(request):
        return RedirectResponse("/admin/login", status_code=303)
    return None


def _no_db(request: Request):
    return _render(
        request,
        "admin_error.html",
        {"message": "La base de datos no está disponible. Revisa DATABASE_URL y que Postgres esté corriendo."},
        status_code=503,
    )


def build_admin_router(bot_manager: BotManager) -> APIRouter:
    router = APIRouter(prefix="/admin")

    @router.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request):
        if _is_authed(request):
            return RedirectResponse("/admin", status_code=303)
        return _render(request, "admin_login.html", {"error": None})

    @router.post("/login")
    async def login_submit(request: Request, password: str = Form(...)):
        if ADMIN_PASSWORD and secrets.compare_digest(password, ADMIN_PASSWORD):
            request.session["admin_authed"] = True
            return RedirectResponse("/admin", status_code=303)
        return _render(request, "admin_login.html", {"error": "Contraseña incorrecta."}, status_code=401)

    @router.post("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/admin/login", status_code=303)

    @router.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        redirect = _require_auth(request)
        if redirect:
            return redirect
        if not db.pool_ready():
            return _no_db(request)
        settings = await db.get_settings()
        counts = await db.stats()
        recent_chats = await db.list_chats(limit=8)
        return _render(
            request,
            "admin_dashboard.html",
            {
                "active_nav": "dashboard",
                "settings": settings,
                "stats": counts,
                "recent_chats": recent_chats,
                "bot_running": bot_manager.running,
            },
        )

    @router.get("/conversations", response_class=HTMLResponse)
    async def conversations_list(request: Request):
        redirect = _require_auth(request)
        if redirect:
            return redirect
        if not db.pool_ready():
            return _no_db(request)
        chats = await db.list_chats(limit=300)
        return _render(request, "admin_conversations.html", {"active_nav": "conversations", "chats": chats})

    @router.get("/conversations/{chat_id}", response_class=HTMLResponse)
    async def conversation_detail(request: Request, chat_id: int):
        redirect = _require_auth(request)
        if redirect:
            return redirect
        if not db.pool_ready():
            return _no_db(request)
        messages = await db.get_conversation(chat_id)
        return _render(
            request,
            "admin_conversation_detail.html",
            {"active_nav": "conversations", "chat_id": chat_id, "messages": messages},
        )

    @router.get("/settings", response_class=HTMLResponse)
    async def settings_form(request: Request):
        redirect = _require_auth(request)
        if redirect:
            return redirect
        if not db.pool_ready():
            return _no_db(request)
        settings = await db.get_settings()
        return _render(
            request,
            "admin_settings.html",
            {"active_nav": "settings", "settings": settings, "saved": False, "bot_running": bot_manager.running},
        )

    @router.post("/settings", response_class=HTMLResponse)
    async def settings_save(
        request: Request,
        bot_name: str = Form(...),
        telegram_token: str = Form(""),
        nvidia_api_key: str = Form(""),
        model: str = Form(...),
        system_prompt: str = Form(...),
        temperature: float = Form(0.7),
        max_tokens: int = Form(1024),
        enabled: str = Form(None),
    ):
        redirect = _require_auth(request)
        if redirect:
            return redirect
        if not db.pool_ready():
            return _no_db(request)

        updates = {
            "bot_name": bot_name.strip() or "qvai",
            "model": model.strip() or "nvidia/nemotron-3.5-lightning-30b-a3b",
            "system_prompt": system_prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "enabled": enabled is not None,
        }
        # Only overwrite a secret if the admin actually typed a new value -
        # the form leaves these blank on load so we never echo secrets back.
        if telegram_token.strip():
            updates["telegram_token"] = telegram_token.strip()
        if nvidia_api_key.strip():
            updates["nvidia_api_key"] = nvidia_api_key.strip()

        new_settings = await db.update_settings(**updates)

        # Fire-and-forget: starting the bot means talking to Telegram's API,
        # which can hang for a while on a bad/invalid token. Don't make the
        # admin wait on that (or time out the save) just to persist settings -
        # the dashboard's status pill reflects the outcome a moment later.
        if new_settings.get("enabled") and new_settings.get("telegram_token"):
            asyncio.create_task(bot_manager.restart(new_settings["telegram_token"]))
        else:
            asyncio.create_task(bot_manager.stop())

        return _render(
            request,
            "admin_settings.html",
            {"active_nav": "settings", "settings": new_settings, "saved": True, "bot_running": bot_manager.running},
        )

    return router

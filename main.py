import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    Message, Update, InlineKeyboardMarkup, InlineKeyboardButton,
    WebAppInfo, MenuButtonWebApp, BusinessConnection,
    BusinessMessagesDeleted,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramAPIError

from database import Database
from security import validate_init_data

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("tg-control")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip().rstrip("/")
PORT = int(os.getenv("PORT", "8080"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DB_PATH = os.getenv("DB_PATH", str(DATA_DIR / "tg_control.sqlite3"))
LOG_MESSAGES_DEFAULT = os.getenv("LOG_MESSAGES", "true").lower() in {"1", "true", "yes", "on"}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")
if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID is required")

DATA_DIR.mkdir(parents=True, exist_ok=True)
db = Database(DB_PATH)
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
BOT_USER_ID = None


def is_owner(user_id: int | None) -> bool:
    return user_id == ADMIN_ID


def main_keyboard():
    if WEBAPP_URL:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Открыть TG Control", web_app=WebAppInfo(url=WEBAPP_URL))
        ]])
    return None


async def owner_notice(text: str):
    try:
        await bot.send_message(ADMIN_ID, text)
    except TelegramAPIError:
        log.exception("Could not notify owner")


@dp.message(Command("start"))
async def cmd_start(message: Message):
    if not message.from_user or not is_owner(message.from_user.id):
        await message.answer("TG Control работает только для владельца, указанного при настройке.")
        return
    await message.answer(
        "<b>TG Control</b>\n"
        "Панель управления подключёнными Telegram Business-чатами.\n\n"
        "Подключи этого бота в Telegram Business → Чат-боты, "
        "выбери нужные чаты и выдай необходимые разрешения.\n\n"
        "Команды: /stats, /chats, /logging_on, /logging_off, /help",
        reply_markup=main_keyboard(),
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    if not message.from_user or not is_owner(message.from_user.id):
        return
    await message.answer(
        "<b>Команды TG Control</b>\n"
        "/start — главная панель\n"
        "/stats — статистика журнала\n"
        "/chats — подключённые чаты\n"
        "/logging_on — включить журналирование\n"
        "/logging_off — выключить журналирование\n\n"
        "Управление участниками групп (бан/мут) через Business-подключение не поддерживается."
    )


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if not message.from_user or not is_owner(message.from_user.id):
        return
    stats = await db.stats()
    await message.answer(
        "<b>Статистика TG Control</b>\n"
        f"Подключений: <b>{stats['connections']}</b>\n"
        f"Чатов: <b>{stats['chats']}</b>\n"
        f"Сообщений в журнале: <b>{stats['messages']}</b>\n"
        f"Удалений зафиксировано: <b>{stats['deletions']}</b>"
    )


@dp.message(Command("chats"))
async def cmd_chats(message: Message):
    if not message.from_user or not is_owner(message.from_user.id):
        return
    chats = await db.list_chats()
    if not chats:
        await message.answer("Пока нет чатов. Подключи бота через настройки Telegram Business.")
        return
    lines = ["<b>Подключённые чаты</b>"]
    for c in chats[:30]:
        title = c["title"] or str(c["chat_id"])
        lines.append(f"• {title} — сообщений: {c['message_count']}")
    await message.answer("\n".join(lines))


@dp.message(Command("logging_on"))
async def cmd_logging_on(message: Message):
    if message.from_user and is_owner(message.from_user.id):
        await db.set_setting("logging_enabled", "1")
        await message.answer("Журналирование включено. Новые сообщения будут сохраняться с уведомлением собеседника.")


@dp.message(Command("logging_off"))
async def cmd_logging_off(message: Message):
    if message.from_user and is_owner(message.from_user.id):
        await db.set_setting("logging_enabled", "0")
        await message.answer("Журналирование выключено. Новые сообщения больше не сохраняются.")


@dp.business_connection()
async def on_business_connection(connection: BusinessConnection):
    await db.save_connection(
        connection.id,
        connection.user.id,
        connection.user_chat_id,
        connection.is_enabled,
        json.dumps(connection.rights.model_dump() if connection.rights else {}),
    )
    state = "подключён" if connection.is_enabled else "отключён"
    await owner_notice(f"Business-подключение {state}. ID: <code>{connection.id}</code>")


def message_text(message: Message) -> str:
    return (message.text or message.caption or "").strip()


def message_media(message: Message):
    candidates = (
        ("photo", message.photo[-1] if message.photo else None),
        ("video", message.video),
        ("document", message.document),
        ("audio", message.audio),
        ("voice", message.voice),
        ("animation", message.animation),
        ("video_note", message.video_note),
        ("sticker", message.sticker),
    )
    for kind, item in candidates:
        if item:
            return item.file_id, item.file_unique_id
    return None, None


async def forward_photo_to_bot_chat(message: Message):
    """Скачивает/пересылает исчезающее фото в чат с ботом (ADMIN_ID)."""
    if not message.photo:
        return

    sender = message.from_user
    sender_name = sender.full_name if sender else "Неизвестно"
    chat_title = message.chat.full_name or message.chat.title or str(message.chat.id)

    try:
        # Используем file_id напрямую для отправки в чат с ботом
        await bot.send_photo(
            chat_id=ADMIN_ID,
            photo=message.photo[-1].file_id,
            caption=f"📸 Исчезающее фото\n"
                    f"От: {sender_name}\n"
                    f"Чат: {chat_title}"
        )
        log.info("Forwarded business photo to admin chat %s", ADMIN_ID)
    except TelegramAPIError:
        log.exception("Could not forward business photo to admin chat")


@dp.business_message()
async def on_business_message(message: Message):
    if not message.business_connection_id:
        return
    if BOT_USER_ID and message.from_user and message.from_user.id == BOT_USER_ID:
        return

    # 1. Сначала обрабатываем фото — пересылаем в чат с ботом (независимо от журналирования)
    if message.photo:
        await forward_photo_to_bot_chat(message)

    # 2. Логика журналирования
    logging_enabled = await db.get_setting("logging_enabled", "1" if LOG_MESSAGES_DEFAULT else "0")
    if logging_enabled != "1":
        return

    chat = message.chat
    sender = message.from_user
    first_notice = await db.mark_chat_announced(message.business_connection_id, chat.id, chat.full_name or chat.title or str(chat.id))
    if first_notice:
        try:
            await bot.send_message(
                chat_id=chat.id,
                business_connection_id=message.business_connection_id,
                text="пр"
            )
        except TelegramAPIError:
            await db.reset_chat_announcement(message.business_connection_id, chat.id)
            log.exception("Could not send logging notice to business chat")
            return
        return

    media_file_id, media_file_unique_id = message_media(message)
    await db.save_message(
        connection_id=message.business_connection_id,
        chat_id=chat.id,
        chat_title=chat.full_name or chat.title or str(chat.id),
        message_id=message.message_id,
        sender_id=sender.id if sender else None,
        sender_name=sender.full_name if sender else "Неизвестно",
        text=message_text(message),
        content_type=message.content_type.value,
        message_date=message.date.isoformat(),
        edited=False,
        media_file_id=media_file_id,
        media_file_unique_id=media_file_unique_id,
        media_group_id=message.media_group_id,
    )


@dp.edited_business_message()
async def on_edited_business_message(message: Message):
    if not message.business_connection_id:
        return
    await db.update_message(
        message.business_connection_id,
        message.chat.id,
        message.message_id,
        message_text(message),
        edited=True,
    )


@dp.deleted_business_messages()
async def on_deleted_business_messages(deleted: BusinessMessagesDeleted):
    await db.record_deletions(
        deleted.business_connection_id,
        deleted.chat.id,
        deleted.message_ids,
    )
    await owner_notice(
        f"Получено уведомление об удалении {len(deleted.message_ids)} сообщения(й) "
        f"в чате <code>{deleted.chat.id}</code>. "
        "Если сообщения были получены и сохранены ранее, их записи остаются в журнале."
    )


async def auth_owner(request: web.Request):
    raw = request.headers.get("X-Telegram-Init-Data", "")
    user = validate_init_data(raw, BOT_TOKEN)
    if not user or int(user.get("id", 0)) != ADMIN_ID:
        raise web.HTTPUnauthorized(text="Telegram WebApp authorization failed")
    return user


async def api_stats(request):
    await auth_owner(request)
    return web.json_response(await db.stats())


async def api_chats(request):
    await auth_owner(request)
    return web.json_response(await db.list_chats())


async def api_messages(request):
    await auth_owner(request)
    try:
        limit = min(max(int(request.query.get("limit", "50")), 1), 200)
    except ValueError:
        raise web.HTTPBadRequest(text="Invalid limit")
    query = request.query.get("q", "")[:100]
    chat_id = request.query.get("chat_id")
    if chat_id not in (None, ""):
        try:
            chat_id = int(chat_id)
        except ValueError:
            raise web.HTTPBadRequest(text="Invalid chat_id")
    else:
        chat_id = None
    deleted_arg = request.query.get("deleted")
    if deleted_arg not in (None, "", "0", "1"):
        raise web.HTTPBadRequest(text="Invalid deleted filter")
    deleted = None if deleted_arg in (None, "") else deleted_arg == "1"
    return web.json_response(await db.list_messages(limit, query, chat_id, deleted))


async def api_people(request):
    await auth_owner(request)
    return web.json_response(await db.list_people())


async def api_media(request):
    await auth_owner(request)
    return web.json_response(await db.list_media())


async def api_connections(request):
    await auth_owner(request)
    return web.json_response(await db.get_connection_status())


async def api_clear_journal(request):
    await auth_owner(request)
    try:
        payload = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="Invalid JSON")
    if payload.get("confirm") != "CLEAR_TG_CONTROL_JOURNAL":
        raise web.HTTPBadRequest(text="Confirmation required")
    await db.delete_all_messages()
    return web.json_response({"ok": True})


async def api_settings_get(request):
    await auth_owner(request)
    return web.json_response({
        "logging_enabled": (await db.get_setting("logging_enabled", "1" if LOG_MESSAGES_DEFAULT else "0")) == "1"
    })


async def api_settings_post(request):
    await auth_owner(request)
    try:
        payload = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="Invalid JSON")
    enabled = bool(payload.get("logging_enabled", False))
    await db.set_setting("logging_enabled", "1" if enabled else "0")
    return web.json_response({"ok": True, "logging_enabled": enabled})


async def index(request):
    return web.FileResponse(Path(__file__).parent / "web" / "index.html")


async def webapp_js(request):
    return web.FileResponse(Path(__file__).parent / "web" / "app.js")


async def webapp_css(request):
    return web.FileResponse(Path(__file__).parent / "web" / "style.css")


async def start_web_server():
    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/app.js", webapp_js)
    app.router.add_get("/style.css", webapp_css)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_get("/api/chats", api_chats)
    app.router.add_get("/api/messages", api_messages)
    app.router.add_get("/api/people", api_people)
    app.router.add_get("/api/media", api_media)
    app.router.add_get("/api/connections", api_connections)
    app.router.add_post("/api/journal/clear", api_clear_journal)
    app.router.add_get("/api/settings", api_settings_get)
    app.router.add_post("/api/settings", api_settings_post)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Mini App server listening on 0.0.0.0:%s", PORT)
    return runner


async def main():
    global BOT_USER_ID
    await db.init()
    me = await bot.get_me()
    BOT_USER_ID = me.id
    if WEBAPP_URL:
        try:
            await bot.set_chat_menu_button(
                chat_id=ADMIN_ID,
                menu_button=MenuButtonWebApp(text="TG Control", web_app=WebAppInfo(url=WEBAPP_URL)),
            )
        except TelegramAPIError:
            log.exception("Could not set Mini App menu button")

    runner = await start_web_server()
    try:
        await dp.start_polling(
            bot,
            allowed_updates=[
                "message", "business_connection", "business_message",
                "edited_business_message", "deleted_business_messages",
            ],
        )
    finally:
        await runner.cleanup()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
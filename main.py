import asyncio
import logging
import os
import random
import re
import json
import requests
import jdatetime
import pytz
import html
import string
import tempfile
from datetime import datetime
from threading import Thread
from typing import List, Dict

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode, ButtonStyle
from aiogram.filters import Command
from aiogram.types import Message, KeyboardButton, InlineKeyboardButton, FSInputFile
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

# ============ CONFIG ============
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PORT = int(os.getenv("PORT", "8080"))
DB_URL = os.getenv("DB_URL")
DB_TOKEN = os.getenv("DB_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")

if DB_URL and DB_URL.startswith("libsql://"):
    DB_URL = DB_URL.replace("libsql://", "https://")

# ============ BOT SETUP ============
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties())
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ============ FSM ============
class SupportState(StatesGroup):
    waiting_for_message = State()

# ============ DATABASE ============
def db_query(sql: str, params: List = None):
    try:
        url = f"{DB_URL}"
        headers = {
            "Authorization": f"Bearer {DB_TOKEN}",
            "Content-Type": "application/json"
        }
        data = {
            "statements": [
                {"q": sql, "params": params or []}
            ]
        }
        response = requests.post(url, headers=headers, json=data, timeout=10)
        result = response.json()
        if isinstance(result, list) and len(result) > 0:
            first = result[0]
            if "results" in first:
                return first["results"]
            elif "error" in first:
                logger.error(f"❌ DB error: {first['error']}")
        return None
    except Exception as e:
        logger.error(f"❌ DB error: {e}")
        return None

def init_database():
    try:
        db_query("""
            CREATE TABLE IF NOT EXISTS configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER,
                text TEXT,
                date TEXT,
                type TEXT,
                file_id TEXT,
                file_name TEXT
            )
        """)
        logger.info("✅ Database initialized")
    except Exception as e:
        logger.error(f"❌ Database init error: {e}")

def save_to_db(item: Dict):
    try:
        db_query("""
            INSERT INTO configs (message_id, text, date, type, file_id, file_name)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [
            item["id"], item["text"],
            item["date"].strftime('%Y-%m-%d %H:%M:%S'),
            item["type"],
            item.get("file_id", ""),
            item.get("file_name", "")
        ])
        logger.info(f"💾 Saved: {item['type']}")
    except Exception as e:
        logger.error(f"❌ DB save error: {e}")

def get_from_db(filter_type: str = "all") -> List[Dict]:
    try:
        if filter_type == "all":
            sql = "SELECT * FROM configs ORDER BY id"
            params = []
        else:
            sql = "SELECT * FROM configs WHERE type = ? ORDER BY id"
            params = [filter_type]
        result = db_query(sql, params)
        items = []
        if result and "rows" in result:
            for row in result["rows"]:
                items.append({
                    "db_id": row[0],
                    "id": row[1],
                    "text": row[2],
                    "date": datetime.strptime(row[3], '%Y-%m-%d %H:%M:%S'),
                    "type": row[4],
                    "file_id": row[5] if row[5] else None,
                    "file_name": row[6] if row[6] else None
                })
        return items
    except Exception as e:
        logger.error(f"❌ DB fetch error: {e}")
        return []

def delete_from_db(db_id: int = None, filter_type: str = None):
    try:
        if filter_type == "all":
            db_query("DELETE FROM configs")
        elif db_id:
            db_query("DELETE FROM configs WHERE id = ?", [db_id])
        elif filter_type:
            db_query("DELETE FROM configs WHERE type = ?", [filter_type])
    except Exception as e:
        logger.error(f"❌ DB delete error: {e}")

# ============ JALALI HELPER ============
def to_jalali(dt: datetime) -> str:
    iran_tz = pytz.timezone('Asia/Tehran')
    dt_iran = dt.astimezone(iran_tz)
    jd = jdatetime.datetime.fromgregorian(datetime=dt_iran)
    return jd.strftime('%Y/%m/%d %H:%M')

def random_name(length=6):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length)) + ".npvt"

# ============ HEALTH CHECK ============
async def health_check(request):
    return web.Response(text="OK")

# ============ DETECTION ============
def detect_type(text: str) -> str:
    text_lower = text.lower()
    for protocol in ['vmess://', 'vless://', 'trojan://', 'hysteria2://', 'hysteria://', 'tuic://', 'ss://', 'ssr://', 'shadowrocket://']:
        if protocol in text_lower:
            return "v2ray"
    return "proxy"

def is_npvt_file(file_name: str = None) -> bool:
    return file_name and file_name.lower().endswith('.npvt')

# ============ CHANNEL POST HANDLER ============
@dp.channel_post()
async def handle_channel_post(message: Message):
    if message.chat.id != CHANNEL_ID:
        return
    
    if message.document:
        file_name = message.document.file_name or ""
        if is_npvt_file(file_name):
            save_to_db({
                "id": message.message_id,
                "text": message.caption or "🟣 نپستر کانفیگ",
                "date": message.date,
                "type": "nepster",
                "file_id": message.document.file_id,
                "file_name": random_name()
            })
            return
    
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    
    if not text and not entities:
        return
    
    def is_proxy_url(url: str) -> bool:
        return 't.me/proxy' in url or 'tg://proxy' in url
    
    def is_v2ray_url(url: str) -> bool:
        return any(url.startswith(p) for p in [
            'vmess://', 'vless://', 'trojan://', 'hysteria2://', 'hysteria://',
            'tuic://', 'ss://', 'ssr://', 'shadowrocket://'
        ])
    
    proxy_links = []
    v2ray_links = []
    
    for entity in entities:
        if entity.type == 'text_link':
            url = entity.url
            if is_proxy_url(url):
                proxy_links.append(url)
            elif is_v2ray_url(url):
                v2ray_links.append(url)
        elif entity.type == 'url':
            url = text[entity.offset:entity.offset + entity.length]
            if is_proxy_url(url):
                proxy_links.append(url)
            elif is_v2ray_url(url):
                v2ray_links.append(url)
    
    if not proxy_links:
        proxy_links = re.findall(r'(?:https?://t\.me/proxy|tg://proxy)\S+', text)
    
    if not v2ray_links:
        v2ray_links = re.findall(
            r'(?:vmess|vless|trojan|hysteria2?|tuic|ss|ssr|shadowrocket)://\S+',
            text
        )
    
    if proxy_links:
        for link in proxy_links:
            save_to_db({
                "id": message.message_id,
                "text": link,
                "date": message.date,
                "type": "proxy",
                "file_id": None,
                "file_name": ""
            })
        return
    
    if v2ray_links:
        for link in v2ray_links:
            save_to_db({
                "id": message.message_id,
                "text": link,
                "date": message.date,
                "type": "v2ray",
                "file_id": None,
                "file_name": ""
            })
        return

# ============ KEYBOARDS ============
def get_main_menu():
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="V2Ray", style=ButtonStyle.SUCCESS),
        KeyboardButton(text="Proxy", style=ButtonStyle.SUCCESS)
    )
    builder.row(
        KeyboardButton(text="NPT (NapsternetV)", style=ButtonStyle.SUCCESS)
    )
    builder.row(
        KeyboardButton(text="Support", style=ButtonStyle.PRIMARY)
    )
    return builder.as_markup(resize_keyboard=True)

# ============ HANDLERS ============
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user = message.from_user
    user_link = "[" + user.full_name + "](tg://user?id=" + str(user.id) + ")"
    await message.answer(
        "سلام " + user_link + " 👋 خوش آمدید!",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_menu()
    )

@dp.message(F.text == "V2Ray")
async def get_v2ray(message: Message):
    items = get_from_db("v2ray")
    if not items:
        await message.answer("❌ V2Ray یافت نشد.", reply_markup=get_main_menu())
        return
    item = random.choice(items)
    await send_v2ray(message, item)

@dp.message(F.text == "Proxy")
async def get_proxy(message: Message):
    items = get_from_db("proxy")
    if not items:
        await message.answer("❌ پروکسی یافت نشد.", reply_markup=get_main_menu())
        return
    count = min(3, len(items))
    selected = random.sample(items, count)
    await message.answer("🔵 **" + str(count) + " پروکسی رندوم:**", parse_mode=ParseMode.MARKDOWN)
    for item in selected:
        await send_proxy(message, item)
    await message.answer("✅", reply_markup=get_main_menu())

@dp.message(F.text == "NPT (NapsternetV)")
async def get_nepster(message: Message):
    items = get_from_db("nepster")
    if not items:
        await message.answer("❌ نپستر یافت نشد.", reply_markup=get_main_menu())
        return
    item = random.choice(items)
    await send_nepster(message, item)

# ============ MANAGE PANEL ============
@dp.message(Command("manage"))
async def cmd_manage(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    v2ray_count = len(get_from_db("v2ray"))
    proxy_count = len(get_from_db("proxy"))
    nepster_count = len(get_from_db("nepster"))
    total = v2ray_count + proxy_count + nepster_count
    
    txt = "🛠 **پنل مدیریت**\n\n🟢 V2Ray: " + str(v2ray_count) + " عدد\n🔵 پروکسی: " + str(proxy_count) + " عدد\n🟣 نپستر: " + str(nepster_count) + " عدد\n📊 کل: " + str(total)
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🗑 حذف همه V2Ray", callback_data="del_v2ray"))
    kb.row(InlineKeyboardButton(text="🗑 حذف همه پروکسی", callback_data="del_proxy"))
    kb.row(InlineKeyboardButton(text="🗑 حذف همه نپستر", callback_data="del_nepster"))
    kb.row(InlineKeyboardButton(text="💣 حذف همه چیز", callback_data="del_all"))
    kb.row(InlineKeyboardButton(text="❌ خروج", callback_data="manage_exit"))
    
    await message.answer(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=kb.as_markup())

@dp.callback_query(F.data == "manage_exit")
async def manage_exit(callback: types.CallbackQuery):
    await callback.message.delete()
    await callback.answer()

@dp.callback_query(F.data == "del_v2ray")
async def del_v2ray(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("دسترسی غیرمجاز", show_alert=True)
        return
    count = len(get_from_db("v2ray"))
    delete_from_db(filter_type="v2ray")
    await callback.message.edit_text("✅ " + str(count) + " V2Ray حذف شد!", parse_mode=ParseMode.MARKDOWN)
    await callback.answer()

@dp.callback_query(F.data == "del_proxy")
async def del_proxy(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("دسترسی غیرمجاز", show_alert=True)
        return
    count = len(get_from_db("proxy"))
    delete_from_db(filter_type="proxy")
    await callback.message.edit_text("✅ " + str(count) + " پروکسی حذف شد!", parse_mode=ParseMode.MARKDOWN)
    await callback.answer()

@dp.callback_query(F.data == "del_nepster")
async def del_nepster(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("دسترسی غیرمجاز", show_alert=True)
        return
    count = len(get_from_db("nepster"))
    delete_from_db(filter_type="nepster")
    await callback.message.edit_text("✅ " + str(count) + " نپستر حذف شد!", parse_mode=ParseMode.MARKDOWN)
    await callback.answer()

@dp.callback_query(F.data == "del_all")
async def del_all(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("دسترسی غیرمجاز", show_alert=True)
        return
    total = len(get_from_db("all"))
    delete_from_db(filter_type="all")
    await callback.message.edit_text("✅ همه " + str(total) + " مورد حذف شد!", parse_mode=ParseMode.MARKDOWN)
    await callback.answer()

# ============ SUPPORT ============
@dp.message(F.text == "Support")
async def support_start(message: Message, state: FSMContext):
    await message.answer(
        "📨 **پشتیبانی**\n\nپیام خود را بنویسید.\n🚫 لغو: /cancel",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(SupportState.waiting_for_message)

@dp.message(SupportState.waiting_for_message)
async def support_receive_message(message: Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("❌ لغو شد.", reply_markup=get_main_menu())
        return
    if not ADMIN_ID:
        await state.clear()
        return
    user = message.from_user
    info = (
        "📩 **پیام پشتیبانی**\n\n"
        "👤 " + user.full_name + "\n"
        "🆔 @" + (user.username or 'ندارد') + "\n"
        "🔢 `" + str(user.id) + "`\n"
        "🕐 " + to_jalali(message.date) + "\n\n"
        "📝 " + message.text
    )
    try:
        await bot.send_message(ADMIN_ID, info, parse_mode=ParseMode.MARKDOWN)
        await message.answer("✅ ارسال شد.", reply_markup=get_main_menu())
    except Exception as e:
        await message.answer("❌ خطا.", reply_markup=get_main_menu())
    await state.clear()

# ============ SEND FUNCTIONS ============
async def send_v2ray(message: Message, item: Dict):
    lines = [line.strip() for line in item["text"].split('\n') if line.strip()]
    config_text = '\n'.join(lines)
    escaped = html.escape(config_text)
    await message.answer(
        "🟢 <b>V2Ray</b>\n<pre>" + escaped[:1000] + "</pre>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

async def send_proxy(message: Message, item: Dict):
    text = item["text"]
    link = None
    for line in text.split('\n'):
        if 't.me/proxy' in line:
            urls = re.findall(r'https?://t\.me/proxy\S+', line)
            if urls:
                link = urls[0]
            break
    if link:
        await message.answer(
            "🔵 <b>MTProto</b>\n\n<a href='" + html.escape(link) + "'>⚡ کلیک کنید</a>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
    else:
        await message.answer(
            "🔵 <b>پروکسی</b>\n\n" + html.escape(text[:400]),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

async def send_nepster(message: Message, item: Dict):
    if item.get("file_id"):
        new_name = item.get('file_name', 'config.npvt')
        try:
            file_data = await bot.download(item["file_id"])
            with tempfile.NamedTemporaryFile(delete=False, suffix=".npvt") as tmp:
                tmp.write(file_data.read())
                tmp_path = tmp.name
            await bot.send_document(
                chat_id=message.chat.id,
                document=FSInputFile(tmp_path, filename=new_name),
                caption="🟣 <b>نپستر</b>\n📄 " + html.escape(new_name),
                parse_mode=ParseMode.HTML
            )
            os.remove(tmp_path)
        except Exception as e:
            logger.error(f"❌ Nepster send error: {e}")
            await message.answer("🟣 <b>نپستر</b>\n\n❌ خطا در ارسال فایل.", parse_mode=ParseMode.HTML)
    else:
        await message.answer(
            "🟣 <b>نپستر</b>\n\n❌ فایل در دسترس نیست.",
            parse_mode=ParseMode.HTML
        )

# ============ MAIN (WEBHOOK) ============
async def main():
    logger.info("🚀 Starting bot...")
    init_database()
    
    if WEBHOOK_URL:
        # WEBHOOK MODE
        from aiogram.webhook.aiohttp_server import SimpleRequestHandler
        
        webhook_path = "/webhook"
        webhook_full_url = WEBHOOK_URL + webhook_path
        
        await bot.delete_webhook()
        await bot.set_webhook(webhook_full_url, allowed_updates=["message", "channel_post", "callback_query"])
        logger.info("✅ Webhook set to: " + webhook_full_url)
        
        app = web.Application()
        app.router.add_get("/", health_check)
        
        handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
        handler.register(app, path=webhook_path)
        
        logger.info("🚀 Webhook server starting on port " + str(PORT))
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        
        await asyncio.Event().wait()
    else:
        # POLLING MODE (fallback)
        Thread(target=run_health_server, daemon=True).start()
        logger.info("✅ Bot ready!")
        await dp.start_polling(bot, allowed_updates=["message", "channel_post", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())

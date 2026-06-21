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
        db_query("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT,
                username TEXT,
                join_date TEXT
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

def save_user_to_db(user_id: int, full_name: str, username: str):
    try:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        result = db_query("SELECT user_id FROM users WHERE user_id = ?", [user_id])
        if result and "rows" in result and len(result["rows"]) > 0:
            db_query("UPDATE users SET full_name = ?, username = ?, join_date = ? WHERE user_id = ?",
                     [full_name, username or "", now, user_id])
        else:
            db_query("INSERT INTO users (user_id, full_name, username, join_date) VALUES (?, ?, ?, ?)",
                     [user_id, full_name, username or "", now])
    except Exception as e:
        logger.error(f"❌ User save error: {e}")

def get_users_count() -> int:
    try:
        result = db_query("SELECT COUNT(*) FROM users")
        if result and "rows" in result and len(result["rows"]) > 0:
            return result["rows"][0][0]
    except Exception as e:
        logger.error(f"❌ User count error: {e}")
    return 0

def get_all_users() -> List[Dict]:
    try:
        result = db_query("SELECT * FROM users ORDER BY join_date DESC")
        users = []
        if result and "rows" in result:
            for row in result["rows"]:
                users.append({
                    "user_id": row[0],
                    "full_name": row[1] or "بی‌نام",
                    "username": row[2] or "",
                    "join_date": row[3] or ""
                })
        return users
    except Exception as e:
        logger.error(f"❌ User fetch error: {e}")
        return []

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
def to_jalali(dt):
    if isinstance(dt, str):
        try:
            dt = datetime.strptime(dt, '%Y-%m-%d %H:%M:%S')
        except:
            return dt
    iran_tz = pytz.timezone('Asia/Tehran')
    dt_iran = dt.astimezone(iran_tz) if dt.tzinfo else iran_tz.localize(dt)
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
    save_user_to_db(user.id, user.full_name, user.username or "")
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
    
    # Send waiting emoji
    wait_msg = await message.answer("⌛")
    
    try:
        item = random.choice(items)
        await send_nepster(message, item)
    except Exception as e:
        logger.error(f"❌ Nepster error: {e}")
        await message.answer("❌ خطا در ارسال فایل.", reply_markup=get_main_menu())
    finally:
        # Delete waiting emoji
        try:
            await wait_msg.delete()
        except:
            pass
        return
    item = random.choice(items)
    await send_nepster(message, item)
    # ============ MANAGE PANEL ============
def get_manage_kb():
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="👥 آمار کاربران", callback_data="stats_users"))
    kb.row(InlineKeyboardButton(text="🗑 حذف همه V2Ray", callback_data="del_v2ray"), InlineKeyboardButton(text="🗑 حذف همه پروکسی", callback_data="del_proxy"))
    kb.row(InlineKeyboardButton(text="🗑 حذف همه نپستر", callback_data="del_nepster"), InlineKeyboardButton(text="💣 حذف همه چیز", callback_data="del_all"))
    kb.row(InlineKeyboardButton(text="❌ خروج", callback_data="manage_exit"))
    return kb.as_markup()

@dp.message(Command("manage"))
async def cmd_manage(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.clear()
    
    v2ray_count = len(get_from_db("v2ray"))
    proxy_count = len(get_from_db("proxy"))
    nepster_count = len(get_from_db("nepster"))
    total = v2ray_count + proxy_count + nepster_count
    
    t = f"🛠 **پنل مدیریت**\n\n🟢 V2Ray: {v2ray_count}\n🔵 پروکسی: {proxy_count}\n🟣 نپستر: {nepster_count}\n📊 کل: {total}"
    await message.answer(t, parse_mode=ParseMode.MARKDOWN, reply_markup=get_manage_kb())

@dp.callback_query(F.data == "manage_back")
async def manage_back(c: types.CallbackQuery):
    t = f"🛠 **پنل مدیریت**\n\n🟢 V2Ray: {len(get_from_db('v2ray'))}\n🔵 پروکسی: {len(get_from_db('proxy'))}\n🟣 نپستر: {len(get_from_db('nepster'))}\n📊 کل: {len(get_from_db('all'))}"
    await c.message.edit_text(t, parse_mode=ParseMode.MARKDOWN, reply_markup=get_manage_kb())

@dp.callback_query(F.data == "stats_users")
async def stats_users(c: types.CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📋 مشاهده جزئیات", callback_data="stats_details"))
    kb.row(InlineKeyboardButton(text="بازگشت 🔙", callback_data="manage_back"))
    await c.message.edit_text(f"👥 **آمار کاربران**\n\nتعداد کل کاربران ربات: {get_users_count()} نفر", parse_mode=ParseMode.MARKDOWN, reply_markup=kb.as_markup())

@dp.callback_query(F.data == "stats_details")
async def stats_details(c: types.CallbackQuery):
    users = get_all_users()
    if not users: return await c.answer("کاربری یافت نشد.", show_alert=True)
    
    txt, msgs = "📋 **لیست کاربران:**\n\n", []
    for i, u in enumerate(users, 1):
        name = u['full_name'].replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
        uname = f"@{u['username']}".replace('_', '\\_') if u['username'] else "ندارد"
        line = f"👤 {i}. {name} | 🆔 `{u['user_id']}`\n🔗 {uname} | 🕒 {to_jalali(u['join_date'])}\n\n"
        if len(txt) + len(line) > 4000:
            msgs.append(txt)
            txt = line
        else: txt += line
    if txt: msgs.append(txt)
    
    for idx, msg in enumerate(msgs):
        if idx == 0:
            kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="بازگشت 🔙", callback_data="stats_users")).as_markup()
            await c.message.edit_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        else: await c.message.answer(msg, parse_mode=ParseMode.MARKDOWN)

@dp.callback_query(F.data == "manage_exit")
async def manage_exit(c: types.CallbackQuery):
    await c.message.delete()

@dp.callback_query(F.data.startswith("del_"))
async def del_callback(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    ftype = c.data.replace("del_", "")
    delete_from_db(filter_type=ftype)
    await c.message.edit_text(f"✅ بخش {ftype} پاکسازی شد!", reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="بازگشت 🔙", callback_data="manage_back")).as_markup())

# ============ SUPPORT ============
@dp.message(F.text == "Support")
async def support_start(message: Message, state: FSMContext):
    await message.answer("📨 **پشتیبانی**\n\nپیام خود را بنویسید.\n🚫 لغو: /cancel", parse_mode=ParseMode.MARKDOWN, reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(SupportState.waiting_for_message)

@dp.message(SupportState.waiting_for_message)
async def support_recv(message: Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        return await message.answer("❌ لغو شد.", reply_markup=get_main_menu())
    u = message.from_user
    info = f"📩 **پیام پشتیبانی**\n\n👤 {u.full_name}\n🆔 @{u.username or 'ندارد'}\n🔢 `{u.id}`\n🕒 {to_jalali(message.date)}\n\n📝 {message.text}"
    try:
        await bot.send_message(ADMIN_ID, info, parse_mode=ParseMode.MARKDOWN)
        await message.answer("✅ ارسال شد.", reply_markup=get_main_menu())
    except Exception: await message.answer("❌ خطا در ارسال.")
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
        from aiogram.webhook.aiohttp_server import SimpleRequestHandler
        await bot.delete_webhook()
        await bot.set_webhook(WEBHOOK_URL + "/webhook", allowed_updates=["message", "channel_post", "callback_query"])
        app = web.Application()
        app.router.add_get("/", health_check)
        SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", PORT).start()
        await asyncio.Event().wait()
    else:
        from threading import Thread as T
        def run_health():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            app = web.Application()
            app.router.add_get("/", health_check)
            runner = web.AppRunner(app)
            loop.run_until_complete(runner.setup())
            loop.run_until_complete(web.TCPSite(runner, "0.0.0.0", PORT).start())
            loop.run_forever()
        T(target=run_health, daemon=True).start()
        await dp.start_polling(bot, allowed_updates=["message", "channel_post", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())

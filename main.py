import asyncio
import logging
import os
import random
import re
import json
import requests
from datetime import datetime
from threading import Thread
from typing import List, Dict

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode, ButtonStyle
from aiogram.filters import Command
from aiogram.types import Message, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
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

if DB_URL and DB_URL.startswith("libsql://"):
    DB_URL = DB_URL.replace("libsql://", "https://")

# ============ BOT SETUP ============
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties())
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ============ FSM FOR SUPPORT ============
class SupportState(StatesGroup):
    waiting_for_message = State()

# ============ FSM FOR MANAGE ============
class ManageState(StatesGroup):
    waiting_for_delete = State()

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
        return None
    except Exception as e:
        logger.error(f"❌ DB error: {e}")
        return None

def init_database():
    try:
        result = db_query("""
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
        if result is not None:
            logger.info("✅ Database initialized")
        else:
            logger.error("❌ Database init failed")
    except Exception as e:
        logger.error(f"❌ Database init error: {e}")

def save_to_db(item: Dict):
    try:
        db_query("""
            INSERT INTO configs (message_id, text, date, type, file_id, file_name)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [
            item["id"],
            item["text"],
            item["date"].strftime('%Y-%m-%d %H:%M:%S'),
            item["type"],
            item.get("file_id", ""),
            item.get("file_name", "")
        ])
        logger.info(f"💾 Saved to DB: {item['type']}")
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

def delete_from_db(db_id: int = None):
    try:
        if db_id:
            db_query("DELETE FROM configs WHERE id = ?", [db_id])
            logger.info(f"🗑 Deleted item {db_id} from DB")
    except Exception as e:
        logger.error(f"❌ DB delete error: {e}")

# ============ HEALTH CHECK ============
async def health_check(request):
    return web.Response(text="OK")

async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Health server on {PORT}")

def run_health_server():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_health_server())
    loop.run_forever()

# ============ DETECTION ============
def detect_type(text: str) -> str:
    text_lower = text.lower()
    v2ray_protocols = [
        'vmess://', 'vless://', 'trojan://', 'hysteria2://', 'hysteria://',
        'tuic://', 'ss://', 'ssr://', 'shadowrocket://'
    ]
    for protocol in v2ray_protocols:
        if protocol in text_lower:
            return "v2ray"
    return "proxy"

def is_npvt_file(file_name: str = None) -> bool:
    if file_name and file_name.lower().endswith('.npvt'):
        return True
    return False

# ============ CHANNEL POST HANDLER ============
@dp.channel_post()
async def handle_channel_post(message: Message):
    if message.chat.id != CHANNEL_ID:
        return
    
    if message.document:
        file_name = message.document.file_name or ""
        if is_npvt_file(file_name):
            item = {
                "id": message.message_id,
                "text": message.caption or "🟣 نپستر کانفیگ",
                "date": message.date,
                "type": "nepster",
                "file_id": message.document.file_id,
                "file_name": file_name
            }
            save_to_db(item)
            logger.info(f"✅ Nepster saved: {file_name}")
            return
    
    if message.text:
        msg_type = detect_type(message.text)
        item = {
            "id": message.message_id,
            "text": message.text,
            "date": message.date,
            "type": msg_type,
            "file_id": None,
            "file_name": ""
        }
        save_to_db(item)
        logger.info(f"✅ {msg_type} saved!")

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

def get_manage_menu():
    v2ray_count = len(get_from_db("v2ray"))
    proxy_count = len(get_from_db("proxy"))
    nepster_count = len(get_from_db("nepster"))
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=f"🟢 V2Ray ({v2ray_count})", callback_data="manage_v2ray"),
        InlineKeyboardButton(text=f"🔵 Proxy ({proxy_count})", callback_data="manage_proxy")
    )
    builder.row(
        InlineKeyboardButton(text=f"🟣 NPT ({nepster_count})", callback_data="manage_nepster")
    )
    builder.row(
        InlineKeyboardButton(text="❌ خروج", callback_data="manage_exit")
    )
    return builder.as_markup()

# ============ HANDLERS ============
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user = message.from_user
    user_link = f"[{user.full_name}](tg://user?id={user.id})"
    await message.answer(
        f"سلام {user_link} 👋 خوش آمدید!",
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
    await message.answer("🟢 **V2Ray رندوم:**", parse_mode=ParseMode.MARKDOWN)
    await send_v2ray(message, item)
    await message.answer("✅", reply_markup=get_main_menu())

@dp.message(F.text == "Proxy")
async def get_proxy(message: Message):
    items = get_from_db("proxy")
    if not items:
        await message.answer("❌ پروکسی یافت نشد.", reply_markup=get_main_menu())
        return
    item = random.choice(items)
    await message.answer("🔵 **پروکسی رندوم:**", parse_mode=ParseMode.MARKDOWN)
    await send_proxy(message, item)
    await message.answer("✅", reply_markup=get_main_menu())

@dp.message(F.text == "NPT (NapsternetV)")
async def get_nepster(message: Message):
    items = get_from_db("nepster")
    if not items:
        await message.answer("❌ نپستر یافت نشد.", reply_markup=get_main_menu())
        return
    item = random.choice(items)
    await message.answer("🟣 **NPT رندوم:**", parse_mode=ParseMode.MARKDOWN)
    await send_nepster(message, item)
    await message.answer("✅", reply_markup=get_main_menu())

# ============ MANAGE PANEL ============
@dp.message(Command("manage"))
async def cmd_manage(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ شما دسترسی ندارید.")
        return
    
    await state.clear()
    
    v2ray_count = len(get_from_db("v2ray"))
    proxy_count = len(get_from_db("proxy"))
    nepster_count = len(get_from_db("nepster"))
    total = v2ray_count + proxy_count + nepster_count
    
    await message.answer(
        f"🛠 **پنل مدیریت**\n\n"
        f"📊 **کل:** {total} عدد\n"
        f"🟢 V2Ray: {v2ray_count}\n"
        f"🔵 پروکسی: {proxy_count}\n"
        f"🟣 نپستر: {nepster_count}\n\n"
        f"یه دسته رو انتخاب کن:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_manage_menu()
    )

@dp.callback_query(F.data == "manage_v2ray")
async def manage_v2ray(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ دسترسی غیرمجاز", show_alert=True)
        return
    
    items = get_from_db("v2ray")
    if not items:
        await callback.answer("V2Ray خالیه", show_alert=True)
        return
    
    await state.update_data(manage_type="v2ray", manage_items=items)
    await state.set_state(ManageState.waiting_for_delete)
    
    text = "🟢 **V2Ray ها:**\n\n"
    for i, item in enumerate(items, 1):
        short_text = item["text"][:80].replace('\n', ' ')
        text += f"{i}️⃣ `{short_text}...`\n"
        text += f"   📅 {item['date'].strftime('%Y-%m-%d %H:%M')}\n\n"
    
    text += "برای حذف، **شماره** رو بفرست.\nبرای برگشت، **برگشت** رو بنویس."
    
    await callback.message.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    await callback.answer()

@dp.callback_query(F.data == "manage_proxy")
async def manage_proxy(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ دسترسی غیرمجاز", show_alert=True)
        return
    
    items = get_from_db("proxy")
    if not items:
        await callback.answer("پروکسی خالیه", show_alert=True)
        return
    
    await state.update_data(manage_type="proxy", manage_items=items)
    await state.set_state(ManageState.waiting_for_delete)
    
    text = "🔵 **پروکسی‌ها:**\n\n"
    for i, item in enumerate(items, 1):
        short_text = item["text"][:80].replace('\n', ' ')
        text += f"{i}️⃣ `{short_text}...`\n"
        text += f"   📅 {item['date'].strftime('%Y-%m-%d %H:%M')}\n\n"
    
    text += "برای حذف، **شماره** رو بفرست.\nبرای برگشت، **برگشت** رو بنویس."
    
    await callback.message.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    await callback.answer()

@dp.callback_query(F.data == "manage_nepster")
async def manage_nepster(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ دسترسی غیرمجاز", show_alert=True)
        return
    
    items = get_from_db("nepster")
    if not items:
        await callback.answer("نپستر خالیه", show_alert=True)
        return
    
    await state.update_data(manage_type="nepster", manage_items=items)
    await state.set_state(ManageState.waiting_for_delete)
    
    text = "🟣 **نپستر ها:**\n\n"
    for i, item in enumerate(items, 1):
        text += f"{i}️⃣ `{item.get('file_name', 'Unknown')}`\n"
        text += f"   📅 {item['date'].strftime('%Y-%m-%d %H:%M')}\n\n"
    
    text += "برای حذف، **شماره** رو بفرست.\nبرای برگشت، **برگشت** رو بنویس."
    
    await callback.message.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    await callback.answer()

@dp.callback_query(F.data == "manage_exit")
async def manage_exit(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ دسترسی غیرمجاز", show_alert=True)
        return
    
    await state.clear()
    await callback.message.delete()
    await callback.answer("خروج از پنل")

@dp.message(ManageState.waiting_for_delete)
async def manage_delete(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ دسترسی غیرمجاز.")
        await state.clear()
        return
    
    if message.text == "برگشت":
        await state.clear()
        v2ray_count = len(get_from_db("v2ray"))
        proxy_count = len(get_from_db("proxy"))
        nepster_count = len(get_from_db("nepster"))
        total = v2ray_count + proxy_count + nepster_count
        
        await message.answer(
            f"🛠 **پنل مدیریت**\n\n"
            f"📊 **کل:** {total} عدد\n"
            f"🟢 V2Ray: {v2ray_count}\n"
            f"🔵 پروکسی: {proxy_count}\n"
            f"🟣 نپستر: {nepster_count}\n\n"
            f"یه دسته رو انتخاب کن:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_manage_menu()
        )
        return
    
    try:
        index = int(message.text) - 1
    except ValueError:
        await message.answer("❌ یه **عدد** بفرست یا **برگشت**.")
        return
    
    data = await state.get_data()
    items = data.get("manage_items", [])
    manage_type = data.get("manage_type", "")
    
    if index < 0 or index >= len(items):
        await message.answer(f"❌ عدد باید بین ۱ تا {len(items)} باشه.")
        return
    
    item_to_delete = items[index]
    delete_from_db(db_id=item_to_delete["db_id"])
    
    await message.answer(f"✅ شماره {index + 1} حذف شد!")
    
    items = get_from_db(manage_type)
    await state.update_data(manage_items=items)
    
    if not items:
        await state.clear()
        v2ray_count = len(get_from_db("v2ray"))
        proxy_count = len(get_from_db("proxy"))
        nepster_count = len(get_from_db("nepster"))
        total = v2ray_count + proxy_count + nepster_count
        
        await message.answer(
            f"🛠 **پنل مدیریت**\n\n"
            f"📊 **کل:** {total} عدد\n"
            f"🟢 V2Ray: {v2ray_count}\n"
            f"🔵 پروکسی: {proxy_count}\n"
            f"🟣 نپستر: {nepster_count}\n\n"
            f"یه دسته رو انتخاب کن:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_manage_menu()
        )
        return
    
    type_names = {"v2ray": "🟢 V2Ray ها", "proxy": "🔵 پروکسی‌ها", "nepster": "🟣 نپستر ها"}
    text = f"{type_names.get(manage_type, 'آیتم ها')}:\n\n"
    
    for i, item in enumerate(items, 1):
        if manage_type == "nepster":
            text += f"{i}️⃣ `{item.get('file_name', 'Unknown')}`\n"
        else:
            short_text = item["text"][:80].replace('\n', ' ')
            text += f"{i}️⃣ `{short_text}...`\n"
        text += f"   📅 {item['date'].strftime('%Y-%m-%d %H:%M')}\n\n"
    
    text += "برای حذف، **شماره** رو بفرست.\nبرای برگشت، **برگشت** رو بنویس."
    
    await message.answer(text, parse_mode=ParseMode.MARKDOWN)

# ============ SUPPORT ============
@dp.message(F.text == "Support")
async def support_start(message: Message, state: FSMContext):
    await message.answer(
        "📨 **پشتیبانی**\n\nپیام خود را بنویسید تا برای ادمین ارسال شود.\n🚫 برای لغو، /cancel را بزنید.",
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
        await message.answer("❌ پشتیبانی در دسترس نیست.", reply_markup=get_main_menu())
        await state.clear()
        return
    user = message.from_user
    user_info = (
        f"📩 **پیام پشتیبانی جدید**\n\n"
        f"👤 **کاربر:** {user.full_name}\n"
        f"🆔 **یوزرنیم:** @{user.username if user.username else 'ندارد'}\n"
        f"🔢 **آیدی عددی:** `{user.id}`\n"
        f"🕐 **تاریخ:** {message.date.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"📝 **پیام:**\n{message.text}"
    )
    try:
        await bot.send_message(ADMIN_ID, user_info, parse_mode=ParseMode.MARKDOWN)
        await message.answer("✅ پیام شما با موفقیت ارسال شد.", reply_markup=get_main_menu())
    except Exception as e:
        logger.error(f"Support forward failed: {e}")
        await message.answer("❌ خطا در ارسال پیام.", reply_markup=get_main_menu())
    await state.clear()

# ============ SEND FUNCTIONS ============
async def send_v2ray(message: Message, item: Dict):
    text = item["text"]
    date_str = item["date"].strftime('%Y-%m-%d %H:%M')
    lines = text.strip().split('\n')
    config_text = '\n'.join(line.strip() for line in lines if line.strip())
    await message.answer(
        f"🟢 **V2Ray**\n📅 {date_str}\n\n{config_text[:1000]}",
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True
    )

async def send_proxy(message: Message, item: Dict):
    text = item["text"]
    date_str = item["date"].strftime('%Y-%m-%d %H:%M')
    lines = text.strip().split('\n')
    proxy_link = None
    for line in lines:
        line = line.strip()
        if 't.me/proxy' in line:
            urls = re.findall(r'https?://t\.me/proxy\S+', line)
            if urls:
                proxy_link = urls[0]
            break
    if proxy_link:
        await message.answer(
            f"🔵 **پروکسی MTProto**\n📅 {date_str}\n\n[⚡ برای اتصال کلیک کنید]({proxy_link})",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
    else:
        await message.answer(
            f"🔵 **پروکسی**\n📅 {date_str}\n\n{text[:400]}",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )

async def send_nepster(message: Message, item: Dict):
    date_str = item["date"].strftime('%Y-%m-%d %H:%M')
    if item.get("file_id"):
        await bot.send_document(
            chat_id=message.chat.id,
            document=item["file_id"],
            caption=f"🟣 **نپستر**\n📅 {date_str}\n📄 {item.get('file_name', 'config.npvt')}",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await message.answer(
            f"🟣 **نپستر**\n📅 {date_str}\n\n❌ فایل در دسترس نیست.",
            parse_mode=ParseMode.MARKDOWN
        )

# ============ MA

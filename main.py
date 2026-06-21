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

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties())
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

class SupportState(StatesGroup):
    waiting_for_message = State()

# ============ DATABASE ============
def db_query(sql, params=None):
    try:
        url = DB_URL
        headers = {"Authorization": f"Bearer {DB_TOKEN}", "Content-Type": "application/json"}
        data = {"statements": [{"q": sql, "params": params or []}]}
        response = requests.post(url, headers=headers, json=data, timeout=10)
        result = response.json()
        if isinstance(result, list) and len(result) > 0:
            first = result[0]
            if "results" in first:
                return first["results"]
        return None
    except Exception as e:
        logger.error(f"DB error: {e}")
        return None

def init_database():
    try:
        db_query("CREATE TABLE IF NOT EXISTS configs (id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER, text TEXT, date TEXT, type TEXT, operator TEXT, file_id TEXT, file_name TEXT)")
        db_query("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, full_name TEXT, username TEXT, join_date TEXT)")
        logger.info("✅ Database initialized")
    except Exception as e:
        logger.error(f"Database init error: {e}")

def save_to_db(item):
    try:
        db_query("INSERT INTO configs (message_id, text, date, type, operator, file_id, file_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 [item["id"], item["text"], item["date"].strftime('%Y-%m-%d %H:%M:%S'), item["type"], item.get("operator", ""), item.get("file_id", ""), item.get("file_name", "")])
        logger.info(f"💾 Saved: {item['type']} [{item.get('operator', '')}]")
    except Exception as e:
        logger.error(f"Save error: {e}")

def get_from_db(filter_type="all", operator=None):
    try:
        if operator:
            sql = "SELECT * FROM configs WHERE type = ? AND operator = ? ORDER BY id"
            params = [filter_type, operator]
        elif filter_type == "all":
            sql = "SELECT * FROM configs ORDER BY id"
            params = []
        else:
            sql = "SELECT * FROM configs WHERE type = ? ORDER BY id"
            params = [filter_type]
        result = db_query(sql, params)
        items = []
        if result and "rows" in result:
            for row in result["rows"]:
                items.append({"db_id": row[0], "id": row[1], "text": row[2], "date": datetime.strptime(row[3], '%Y-%m-%d %H:%M:%S'), "type": row[4], "operator": row[5] or "", "file_id": row[6] if row[6] else None, "file_name": row[7] if row[7] else None})
        return items
    except Exception as e:
        logger.error(f"Fetch error: {e}")
        return []

def delete_from_db(db_id=None, filter_type=None):
    try:
        if filter_type == "all":
            db_query("DELETE FROM configs")
        elif db_id:
            db_query("DELETE FROM configs WHERE id = ?", [db_id])
        elif filter_type:
            db_query("DELETE FROM configs WHERE type = ?", [filter_type])
    except Exception as e:
        logger.error(f"Delete error: {e}")

def save_user_to_db(user_id, full_name, username):
    try:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        result = db_query("SELECT user_id FROM users WHERE user_id = ?", [user_id])
        if result and "rows" in result and len(result["rows"]) > 0:
            db_query("UPDATE users SET full_name = ?, username = ?, join_date = ? WHERE user_id = ?", [full_name, username or "", now, user_id])
        else:
            db_query("INSERT INTO users (user_id, full_name, username, join_date) VALUES (?, ?, ?, ?)", [user_id, full_name, username or "", now])
    except Exception as e:
        logger.error(f"User save error: {e}")

def get_users_count():
    try:
        result = db_query("SELECT COUNT(*) FROM users")
        if result and "rows" in result and len(result["rows"]) > 0:
            return result["rows"][0][0]
    except:
        pass
    return 0

def get_all_users():
    try:
        result = db_query("SELECT * FROM users ORDER BY join_date DESC")
        users = []
        if result and "rows" in result:
            for row in result["rows"]:
                users.append({"user_id": row[0], "full_name": row[1] or "بی‌نام", "username": row[2] or "", "join_date": row[3] or ""})
        return users
    except:
        return []

# ============ HELPERS ============
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
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length)) + ".npvt"

async def health_check(request):
    return web.Response(text="OK")

def detect_type(text):
    for protocol in ['vmess://', 'vless://', 'trojan://', 'hysteria2://', 'hysteria://', 'tuic://', 'ss://', 'ssr://']:
        if protocol in text.lower():
            return "v2ray"
    return "proxy"

def is_npvt_file(file_name=None):
    return file_name and file_name.lower().endswith('.npvt')

# ============ OPERATORS ============
VALID_OPERATORS = {"#همراه_اول": "mci", "#ایرانسل": "mtn", "#رایتل": "rtl"}
OPERATOR_LABELS = {"mci": "🚀 همراه اول", "mtn": "⚡ ایرانسل", "rtl": "🌐 رایتل"}
OPERATOR_PHRASES = {
    "mci": ["🚀 همراه اول VIP", "📡 همراه اول - پرسرعت", "🔥 MCI - ویژه"],
    "mtn": ["⚡ ایرانسل - پایدار", "🌐 MTN - مناسب", "💎 ایرانسل VIP"],
    "rtl": ["📶 رایتل - جدید", "🔷 RTL - مخصوص", "🎯 رایتل VIP"]
}
current_operator = {"value": ""}

# ============ CHANNEL POST HANDLER ============
@dp.channel_post()
async def handle_channel_post(message: Message):
    if message.chat.id != CHANNEL_ID:
        return
    if message.text:
        text = message.text.strip()
        for tag, op_code in VALID_OPERATORS.items():
            if text == tag:
                current_operator["value"] = op_code
                return
    if not current_operator["value"]:
        return
    op = current_operator["value"]
    if message.document:
        file_name = message.document.file_name or ""
        if is_npvt_file(file_name):
            save_to_db({"id": message.message_id, "text": message.caption or "نپستر", "date": message.date, "type": "nepster", "operator": op, "file_id": message.document.file_id, "file_name": random_name()})
            return
    text = message.text or message.caption or ""
    if not text:
        return
    proxy_links = re.findall(r'(?:https?://t\.me/proxy|tg://proxy)\S+', text)
    v2ray_links = re.findall(r'(?:vmess|vless|trojan|hysteria2?|tuic|ss|ssr)://\S+', text)
    if proxy_links:
        for link in proxy_links:
            save_to_db({"id": message.message_id, "text": link, "date": message.date, "type": "proxy", "operator": op, "file_id": None, "file_name": ""})
    elif v2ray_links:
        for link in v2ray_links:
            save_to_db({"id": message.message_id, "text": link, "date": message.date, "type": "v2ray", "operator": op, "file_id": None, "file_name": ""})

# ============ KEYBOARDS ============
def get_main_menu():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="V2Ray", style=ButtonStyle.SUCCESS), KeyboardButton(text="Proxy", style=ButtonStyle.SUCCESS))
    builder.row(KeyboardButton(text="NPT (NapsternetV)", style=ButtonStyle.SUCCESS))
    builder.row(KeyboardButton(text="Support", style=ButtonStyle.PRIMARY))
    return builder.as_markup(resize_keyboard=True)

def get_operator_kb():
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🚀 همراه اول", callback_data="op_mci"), InlineKeyboardButton(text="⚡ ایرانسل", callback_data="op_mtn"))
    kb.row(InlineKeyboardButton(text="🌐 رایتل", callback_data="op_rtl"))
    kb.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="op_back"))
    return kb.as_markup()

# ============ HANDLERS ============
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user = message.from_user
    save_user_to_db(user.id, user.full_name, user.username or "")
    await message.answer(f"سلام [{user.full_name}](tg://user?id={user.id}) 👋 خوش آمدید!", parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu())

@dp.message(F.text == "V2Ray")
async def get_v2ray(message: Message):
    await message.answer("🔵 **انتخاب اپراتور:**", parse_mode=ParseMode.MARKDOWN, reply_markup=get_operator_kb())

@dp.message(F.text == "Proxy")
async def get_proxy(message: Message):
    await message.answer("🔵 **انتخاب اپراتور:**", parse_mode=ParseMode.MARKDOWN, reply_markup=get_operator_kb())

@dp.message(F.text == "NPT (NapsternetV)")
async def get_nepster_menu(message: Message):
    await message.answer("🔵 **انتخاب اپراتور:**", parse_mode=ParseMode.MARKDOWN, reply_markup=get_operator_kb())

@dp.callback_query(F.data == "op_back")
async def op_back(c: types.CallbackQuery):
    await c.message.delete()
    await c.answer()

@dp.callback_query(F.data.startswith("op_"))
async def operator_selected(c: types.CallbackQuery):
    op_code = c.data.replace("op_", "")
    op_label = OPERATOR_LABELS.get(op_code, "")
    await c.message.delete()
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🟢 V2Ray", callback_data=f"type_v2ray_{op_code}"), InlineKeyboardButton(text="🔵 Proxy", callback_data=f"type_proxy_{op_code}"))
    kb.row(InlineKeyboardButton(text="🟣 NPT", callback_data=f"type_nepster_{op_code}"))
    await c.message.answer(f"📱 **{op_label}**\n\nحالا نوع کانفیگ رو انتخاب کن:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb.as_markup())
    await c.answer()    
@dp.callback_query(F.data.startswith("type_"))
async def type_selected(c: types.CallbackQuery):
    parts = c.data.split("_")
    config_type = parts[1]
    op_code = parts[2]
    phrase = random.choice(OPERATOR_PHRASES.get(op_code, [""]))
    items = get_from_db(config_type, operator=op_code)
    if not items:
        await c.message.delete()
        await c.message.answer("موجود نیست شماهم نگردید نیست😅😄")
        await c.answer()
        return
    item = random.choice(items)
    await c.message.delete()
    if config_type == "v2ray":
        lines = [line.strip() for line in item["text"].split('\n') if line.strip()]
        config_text = '\n'.join(lines)
        await c.message.answer(f"🟢 <b>V2Ray - {phrase}</b>\n<pre>{html.escape(config_text)[:1000]}</pre>", parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    elif config_type == "proxy":
        link = None
        for line in item["text"].split('\n'):
            if 't.me/proxy' in line:
                urls = re.findall(r'https?://t\.me/proxy\S+', line)
                if urls:
                    link = urls[0]
                break
        if link:
            await c.message.answer(f"🔵 <b>MTProto - {phrase}</b>\n\n<a href='{html.escape(link)}'>⚡ کلیک کنید</a>", parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        else:
            await c.message.answer(f"🔵 <b>پروکسی - {phrase}</b>\n\n{html.escape(item['text'][:400])}", parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    elif config_type == "nepster":
        wait_msg = await c.message.answer("⌛")
        try:
            if item.get("file_id"):
                new_name = item.get('file_name', 'config.npvt')
                file_data = await bot.download(item["file_id"])
                with tempfile.NamedTemporaryFile(delete=False, suffix=".npvt") as tmp:
                    tmp.write(file_data.read())
                    tmp_path = tmp.name
                await bot.send_document(chat_id=c.message.chat.id, document=FSInputFile(tmp_path, filename=new_name), caption=f"🟣 <b>نپستر - {phrase}</b>\n📄 {html.escape(new_name)}", parse_mode=ParseMode.HTML)
                os.remove(tmp_path)
            else:
                await c.message.answer("🟣 <b>نپستر</b>\n\n❌ فایل در دسترس نیست.", parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Nepster error: {e}")
            await c.message.answer("🟣 <b>نپستر</b>\n\n❌ خطا در ارسال فایل.", parse_mode=ParseMode.HTML)
        finally:
            try:
                await wait_msg.delete()
            except:
                pass
    await c.answer()

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
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    t = f"🛠 **پنل مدیریت**\n\n🟢 V2Ray: {len(get_from_db('v2ray'))}\n🔵 پروکسی: {len(get_from_db('proxy'))}\n🟣 نپستر: {len(get_from_db('nepster'))}\n📊 کل: {len(get_from_db('all'))}"
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
    if not users:
        return await c.answer("کاربری یافت نشد.", show_alert=True)
    txt, msgs = "📋 **لیست کاربران:**\n\n", []
    for i, u in enumerate(users, 1):
        name = u['full_name'].replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
        uname = f"@{u['username']}".replace('_', '\\_') if u['username'] else "ندارد"
        line = f"👤 {i}. {name} | 🆔 `{u['user_id']}`\n🔗 {uname} | 🕒 {to_jalali(u['join_date'])}\n\n"
        if len(txt) + len(line) > 4000:
            msgs.append(txt)
            txt = line
        else:
            txt += line
    if txt:
        msgs.append(txt)
    for idx, msg in enumerate(msgs):
        if idx == 0:
            await c.message.edit_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="بازگشت 🔙", callback_data="stats_users")).as_markup())
        else:
            await c.message.answer(msg, parse_mode=ParseMode.MARKDOWN)

@dp.callback_query(F.data == "manage_exit")
async def manage_exit(c: types.CallbackQuery):
    await c.message.delete()

@dp.callback_query(F.data.startswith("del_"))
async def del_callback(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        return
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
    try:
        await bot.send_message(ADMIN_ID, f"📩 **پیام پشتیبانی**\n\n👤 {u.full_name}\n🆔 @{u.username or 'ندارد'}\n🔢 `{u.id}`\n🕒 {to_jalali(message.date)}\n\n📝 {message.text}", parse_mode=ParseMode.MARKDOWN)
        await message.answer("✅ ارسال شد.", reply_markup=get_main_menu())
    except:
        await message.answer("❌ خطا در ارسال.")
    await state.clear()

# ============ MAIN ============
async def main():
    logger.info("🚀 Starting bot...")
    init_database()
    if WEBHOOK_URL:
        from aiogram.webhook.aiohttp_server import SimpleRequestHandler
        await bot.delete_webhook()
        await bot.set_webhook(WEBHOOK_URL + "/webhook", allowed_updates=["message", "channel_post", "callback_query"])
        logger.info("✅ Webhook set to: " + WEBHOOK_URL + "/webhook")
        app = web.Application()
        app.router.add_get("/", health_check)
        SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", PORT).start()
        logger.info("🚀 Webhook server started on port " + str(PORT))
        await asyncio.Event().wait()
    else:
        def run_health():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            app = web.Application()
            app.router.add_get("/", health_check)
            runner = web.AppRunner(app)
            loop.run_until_complete(runner.setup())
            loop.run_until_complete(web.TCPSite(runner, "0.0.0.0", PORT).start())
            loop.run_forever()
        Thread(target=run_health, daemon=True).start()
        await dp.start_polling(bot, allowed_updates=["message", "channel_post", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())

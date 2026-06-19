import asyncio
import logging
import os
import random
import re
import html
import string
import tempfile
import requests
import jdatetime
import pytz
from datetime import datetime, timezone
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PORT = int(os.getenv("PORT", "8080"))
DB_URL = (os.getenv("DB_URL") or "").replace("libsql://", "https://")
DB_TOKEN = os.getenv("DB_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties())
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

class SupportState(StatesGroup):
    waiting_for_message = State()

# ============ DATABASE ============
def db_query(sql: str, params: list = None):
    try:
        headers = {"Authorization": f"Bearer {DB_TOKEN}", "Content-Type": "application/json"}
        data = {"statements": [{"q": sql, "params": params or []}]}
        response = requests.post(DB_URL, headers=headers, json=data, timeout=10)
        result = response.json()
        if isinstance(result, list) and len(result) > 0:
            if "results" in result[0]: return result[0]["results"]
            if "error" in result[0]: logger.error(f"❌ DB error: {result[0]['error']}")
        return None
    except Exception as e:
        logger.error(f"❌ DB connection error: {e}")
        return None

def init_database():
    db_query("CREATE TABLE IF NOT EXISTS configs (id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER, text TEXT, date TEXT, type TEXT, file_id TEXT, file_name TEXT)")
    db_query("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, full_name TEXT, username TEXT, join_date TEXT)")
    logger.info("✅ Database initialized")

def save_to_db(item: dict):
    db_query("INSERT INTO configs (message_id, text, date, type, file_id, file_name) VALUES (?, ?, ?, ?, ?, ?)",
             [item["id"], item["text"], item["date"].strftime('%Y-%m-%d %H:%M:%S'), item["type"], item.get("file_id", ""), item.get("file_name", "")])

def get_from_db(filter_type: str = "all") -> list:
    sql = "SELECT * FROM configs ORDER BY id" if filter_type == "all" else "SELECT * FROM configs WHERE type = ? ORDER BY id"
    res = db_query(sql, [] if filter_type == "all" else [filter_type])
    items = []
    if res and "rows" in res:
        for r in res["rows"]:
            items.append({"db_id": r[0], "id": r[1], "text": r[2], "date": datetime.strptime(r[3], '%Y-%m-%d %H:%M:%S'), "type": r[4], "file_id": r[5] or None, "file_name": r[6] or None})
    return items

def delete_from_db(db_id: int = None, filter_type: str = None):
    if filter_type == "all": db_query("DELETE FROM configs")
    elif db_id: db_query("DELETE FROM configs WHERE id = ?", [db_id])
    elif filter_type: db_query("DELETE FROM configs WHERE type = ?", [filter_type])

def save_user(user_id: int, full_name: str, username: str, join_date: datetime):
    exist = db_query("SELECT user_id FROM users WHERE user_id = ?", [user_id])
    if not exist or "rows" not in exist or len(exist["rows"]) == 0:
        db_query("INSERT INTO users (user_id, full_name, username, join_date) VALUES (?, ?, ?, ?)", [user_id, full_name, username, join_date.strftime('%Y-%m-%d %H:%M:%S')])

def get_users_count() -> int:
    res = db_query("SELECT COUNT(*) FROM users")
    return res["rows"][0][0] if res and "rows" in res and res["rows"] else 0

def get_all_users() -> list:
    res = db_query("SELECT user_id, full_name, username, join_date FROM users ORDER BY join_date DESC")
    users = []
    if res and "rows" in res:
        for r in res["rows"]:
            dt = datetime.strptime(r[3], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
            users.append({"user_id": r[0], "full_name": r[1] or "", "username": r[2] or "", "join_date": dt})
    return users

# ============ HELPERS ============
def to_jalali(dt: datetime) -> str:
    dt_iran = dt.astimezone(pytz.timezone('Asia/Tehran'))
    return jdatetime.datetime.fromgregorian(datetime=dt_iran).strftime('%Y/%m/%d %H:%M')

def random_name():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=6)) + ".npvt"

async def health_check(request):
    return web.Response(text="OK")

# ============ CHANNEL POST HANDLER ============
@dp.channel_post()
async def handle_channel_post(message: Message):
    if message.chat.id != CHANNEL_ID: return
    if message.document and (message.document.file_name or "").lower().endswith('.npvt'):
        save_to_db({"id": message.message_id, "text": message.caption or "🟣 نپستر کانفیگ", "date": message.date, "type": "nepster", "file_id": message.document.file_id, "file_name": random_name()})
        return
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    if not text and not entities: return
    
    links = []
    is_v2ray = lambda u: any(u.startswith(p) for p in ['vmess://', 'vless://', 'trojan://', 'hysteria2://', 'hysteria://', 'tuic://', 'ss://', 'ssr://', 'shadowrocket://'])
    is_proxy = lambda u: 't.me/proxy' in u or 'tg://proxy' in u
    
    for e in entities:
        url = e.url if e.type == 'text_link' else text[e.offset:e.offset + e.length]
        if url and (is_proxy(url) or is_v2ray(url)): links.append(url)
        
    if not links: links.extend(re.findall(r'(?:https?://t\.me/proxy|tg://proxy)\S+', text))
    if not links: links.extend(re.findall(r'(?:vmess|vless|trojan|hysteria2?|tuic|ss|ssr|shadowrocket)://\S+', text))
    
    for link in links:
        save_to_db({"id": message.message_id, "text": link, "date": message.date, "type": "v2ray" if is_v2ray(link) else "proxy"})

# ============ HANDLERS ============
def get_main_menu():
    b = ReplyKeyboardBuilder()
    b.row(KeyboardButton(text="V2Ray"), KeyboardButton(text="Proxy"))
    b.row(KeyboardButton(text="NPT (NapsternetV)"))
    b.row(KeyboardButton(text="Support"))
    return b.as_markup(resize_keyboard=True)

@dp.message(Command("start"))
async def cmd_start(message: Message):
    u = message.from_user
    save_user(u.id, u.full_name or "", u.username or "", message.date)
    await message.answer(f"سلام [{u.full_name}](tg://user?id={u.id}) 👋 خوش آمدید!", parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu())

@dp.message(F.text == "V2Ray")
async def get_v2ray(message: Message):
    items = get_from_db("v2ray")
    if not items: return await message.answer("❌ V2Ray یافت نشد.")
    txt = '\n'.join([l.strip() for l in random.choice(items)["text"].split('\n') if l.strip()])
    await message.answer(f"🟢 <b>V2Ray</b>\n<pre>{html.escape(txt[:1000])}</pre>", parse_mode=ParseMode.HTML, disable_web_page_preview=True)

@dp.message(F.text == "Proxy")
async def get_proxy(message: Message):
    items = get_from_db("proxy")
    if not items: return await message.answer("❌ پروکسی یافت نشد.")
    sel = random.sample(items, min(3, len(items)))
    await message.answer(f"🔵 **{len(sel)} پروکسی رندوم:**", parse_mode=ParseMode.MARKDOWN)
    for it in sel:
        lnk = next((u for u in re.findall(r'https?://t\.me/proxy\S+', it["text"])), None)
        if lnk: await message.answer(f"🔵 <b>MTProto</b>\n\n<a href='{html.escape(lnk)}'>⚡ کلیک کنید</a>", parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        else: await message.answer(f"🔵 <b>پروکسی</b>\n\n{html.escape(it['text'][:400])}", parse_mode=ParseMode.HTML, disable_web_page_preview=True)

@dp.message(F.text == "NPT (NapsternetV)")
async def get_nepster(message: Message):
    items = get_from_db("nepster")
    if not items: return await message.answer("❌ نپستر یافت نشد.")
    it = random.choice(items)
    if it.get("file_id"):
        try:
            fd = await bot.download(it["file_id"])
            with tempfile.NamedTemporaryFile(delete=False, suffix=".npvt") as tmp:
                tmp.write(fd.read())
                p = tmp.name
            await bot.send_document(message.chat.id, FSInputFile(p, filename=it['file_name']), caption=f"🟣 <b>نپستر</b>\n📄 {html.escape(it['file_name'])}", parse_mode=ParseMode.HTML)
            os.remove(p)
        except Exception: await message.answer("❌ خطا در ارسال فایل.")
    else: await message.answer("❌ فایل در دسترس نیست.")

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

# ============ MAIN ============
async def main():
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

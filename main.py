import asyncio
import logging
import os
import random
import re
from datetime import datetime
from threading import Thread
from typing import List, Dict

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode, ButtonStyle
from aiogram.filters import Command
from aiogram.types import Message, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder
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

# ============ STORAGE ============
proxy_storage: List[Dict] = []
last_update_time = None
known_message_ids = set()

# ============ BOT SETUP ============
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties())
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ============ FSM FOR SUPPORT ============
class SupportState(StatesGroup):
    waiting_for_message = State()

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
    """Detect config/proxy type from text"""
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
    """Check if file is nepster config"""
    if file_name and file_name.lower().endswith('.npvt'):
        return True
    return False

# ============ CHANNEL POST HANDLER ============
@dp.channel_post()
async def handle_channel_post(message: Message):
    global proxy_storage, last_update_time, known_message_ids
    
    if message.chat.id != CHANNEL_ID:
        return
    
    if message.message_id in known_message_ids:
        return
    
    known_message_ids.add(message.message_id)
    
    # Handle NEPSTER file (.npvt)
    if message.document:
        file_name = message.document.file_name or ""
        if is_npvt_file(file_name):
            proxy_storage.append({
                "id": message.message_id,
                "text": message.caption or "🟣 نپستر کانفیگ",
                "date": message.date,
                "type": "nepster",
                "emoji": "🟣",
                "file_id": message.document.file_id,
                "file_name": file_name
            })
            last_update_time = datetime.now()
            logger.info(f"✅ Nepster saved: {file_name}")
            return
    
    # Handle text messages
    if message.text:
        msg_type = detect_type(message.text)
        emoji = "🟢" if msg_type == "v2ray" else "🔵"
        
        proxy_storage.append({
            "id": message.message_id,
            "text": message.text,
            "date": message.date,
            "type": msg_type,
            "emoji": emoji,
            "file_id": None
        })
        
        last_update_time = datetime.now()
        logger.info(f"✅ {msg_type} saved! Total: {len(proxy_storage)}")

# ============ KEYBOARD ============
def get_main_menu():
    """Main menu with colored buttons"""
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
    user_link = f"[{user.full_name}](tg://user?id={user.id})"
    
    await message.answer(
        f"سلام {user_link} 👋 خوش آمدید!",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_menu()
    )

@dp.message(F.text == "V2Ray")
async def get_v2ray(message: Message):
    items = [m for m in proxy_storage if m["type"] == "v2ray"]
    
    if not items:
        await message.answer("❌ V2Ray یافت نشد.", reply_markup=get_main_menu())
        return
    
    item = random.choice(items)
    await message.answer("🟢 **V2Ray رندوم:**", parse_mode=ParseMode.MARKDOWN)
    await send_v2ray(message, item)
    await message.answer("✅", reply_markup=get_main_menu())

@dp.message(F.text == "Proxy")
async def get_proxy(message: Message):
    items = [m for m in proxy_storage if m["type"] == "proxy"]
    
    if not items:
        await message.answer("❌ پروکسی یافت نشد.", reply_markup=get_main_menu())
        return
    
    item = random.choice(items)
    await message.answer("🔵 **پروکسی رندوم:**", parse_mode=ParseMode.MARKDOWN)
    await send_proxy(message, item)
    await message.answer("✅", reply_markup=get_main_menu())

@dp.message(F.text == "NPT (NapsternetV)")
async def get_nepster(message: Message):
    items = [m for m in proxy_storage if m["type"] == "nepster"]
    
    if not items:
        await message.answer("❌ نپستر یافت نشد.", reply_markup=get_main_menu())
        return
    
    item = random.choice(items)
    await message.answer("🟣 **NPT رندوم:**", parse_mode=ParseMode.MARKDOWN)
    await send_nepster(message, item)
    await message.answer("✅", reply_markup=get_main_menu())

# ============ SUPPORT ============
@dp.message(F.text == "Support")
async def support_start(message: Message, state: FSMContext):
    await message.answer(
        "📨 **پشتیبانی**\n\n"
        "پیام خود را بنویسید تا برای ادمین ارسال شود.\n"
        "🚫 برای لغو، /cancel را بزنید.",
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
        await bot.send_message(
            ADMIN_ID,
            user_info,
            parse_mode=ParseMode.MARKDOWN
        )
        await message.answer(
            "✅ پیام شما با موفقیت ارسال شد.",
            reply_markup=get_main_menu()
        )
    except Exception as e:
        logger.error(f"Support forward failed: {e}")
        await message.answer(
            "❌ خطا در ارسال پیام. لطفاً دوباره تلاش کنید.",
            reply_markup=get_main_menu()
        )
    
    await state.clear()

# ============ SEND FUNCTIONS ============
async def send_v2ray(message: Message, item: Dict):
    """Send V2Ray config as plain text"""
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
    """Send proxy as clickable link"""
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
    """Send nepster config as file"""
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

# ============ MAIN ============
async def main():
    logger.info("🚀 Starting bot...")
    
    Thread(target=run_health_server, daemon=True).start()
    
    logger.info("✅ Bot ready!")
    await dp.start_polling(bot, allowed_updates=["message", "channel_post"])

if __name__ == "__main__":
    asyncio.run(main())

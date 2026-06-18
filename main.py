import asyncio
import logging
import os
from datetime import datetime, timedelta
from threading import Thread
from typing import List, Dict

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode, ButtonStyle
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    Message
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiohttp import web

# ============ CONFIG ============
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 8080))
ADMIN_ID = os.getenv("ADMIN_ID", "0")  # Your Telegram ID (optional)

if not BOT_TOKEN or not CHANNEL_ID:
    raise ValueError("BOT_TOKEN and CHANNEL_ID must be set")

CHANNEL_ID = int(CHANNEL_ID)
ADMIN_ID = int(ADMIN_ID) if ADMIN_ID != "0" else None

logger.info(f"Starting with Channel: {CHANNEL_ID}")

# ============ STORAGE ============
proxy_storage: List[Dict] = []
last_update_time = None
last_message_id = 0  # Track last seen message

# ============ BOT SETUP ============
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties())
dp = Dispatcher()

# ============ HEALTH CHECK SERVER ============
async def health_check(request):
    return web.Response(text="Bot is running!")

async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Health server on port {PORT}")

def run_health_server():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_health_server())
    loop.run_forever()

# ============ FUNCTIONS ============
async def fetch_channel_posts():
    """Fetch posts by copying messages to a private chat"""
    global proxy_storage, last_update_time, last_message_id
    
    try:
        logger.info("🔍 Fetching channel posts...")
        
        if not ADMIN_ID:
            logger.error("❌ ADMIN_ID not set! Can't fetch messages.")
            logger.error("Add ADMIN_ID (your Telegram ID) to Railway variables")
            return
        
        # Get latest message ID from channel
        chat = await bot.get_chat(CHANNEL_ID)
        logger.info(f"✅ Channel: {chat.full_name}")
        
        # Forward messages from channel to admin (to get the content)
        messages = []
        
        # Try to forward last 5 messages
        for offset in range(5):
            try:
                msg_id = chat.pinned_message.message_id - offset if chat.pinned_message else 0
                if msg_id <= 0:
                    # Try forwarding by known IDs
                    if last_message_id > 0:
                        msg_id = last_message_id - offset
                    else:
                        continue
                
                # Forward message to admin
                forwarded = await bot.forward_message(
                    chat_id=ADMIN_ID,
                    from_chat_id=CHANNEL_ID,
                    message_id=msg_id
                )
                
                if forwarded.text:
                    age = datetime.now() - forwarded.date
                    if age < timedelta(hours=24):
                        msg_type = "v2ray" if ("vmess" in forwarded.text.lower() or "vless" in forwarded.text.lower()) else "proxy"
                        messages.append({
                            "id": forwarded.message_id,
                            "text": forwarded.text,
                            "date": forwarded.date,
                            "type": msg_type
                        })
                        logger.info(f"✅ Found: {msg_type}")
                
                # Delete forwarded message from admin chat
                await bot.delete_message(ADMIN_ID, forwarded.message_id)
                
            except Exception as e:
                logger.debug(f"Skip msg {offset}: {e}")
                continue
        
        if messages:
            proxy_storage = messages
            last_update_time = datetime.now()
            logger.info(f"💾 Storage: {len(messages)} messages")
        else:
            logger.warning("⚠️ No messages found")
            
    except Exception as e:
        logger.error(f"💥 Error: {e}")

async def clean_old_posts():
    global proxy_storage
    before = len(proxy_storage)
    now = datetime.now()
    proxy_storage = [m for m in proxy_storage if now - m["date"] < timedelta(hours=24)]
    if before != len(proxy_storage):
        logger.info(f"🧹 Cleaned: {before} -> {len(proxy_storage)}")

async def periodic_update():
    logger.info("⏰ Periodic update started")
    while True:
        try:
            await fetch_channel_posts()
            await clean_old_posts()
        except Exception as e:
            logger.error(f"Update error: {e}")
        await asyncio.sleep(7200)

# ============ HANDLER FOR RECEIVING FORWARDED MESSAGES ============
@dp.message(F.forward_from_chat)
async def handle_forwarded(message: Message):
    """This catches forwarded messages from channel"""
    global proxy_storage, last_update_time, last_message_id
    
    if message.forward_from_chat.id == CHANNEL_ID:
        logger.info(f"📨 Received forwarded message from channel")
        
        if message.text:
            msg_type = "v2ray" if ("vmess" in message.text.lower() or "vless" in message.text.lower()) else "proxy"
            
            proxy_storage.append({
                "id": message.message_id,
                "text": message.text,
                "date": message.date,
                "type": msg_type
            })
            
            last_message_id = message.forward_from_message_id
            last_update_time = datetime.now()
            
            logger.info(f"✅ Added {msg_type} to storage")
            
            # Clean old
            await clean_old_posts()

# ============ KEYBOARDS ============
def get_main_menu():
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="📡 دریافت لینک‌های جدید", style=ButtonStyle.SUCCESS)
    )
    builder.row(
        KeyboardButton(text="📋 راهنما", style=ButtonStyle.PRIMARY),
        KeyboardButton(text="💬 پشتیبانی", style=ButtonStyle.DANGER)
    )
    return builder.as_markup(resize_keyboard=True)

def get_inline_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔵 دریافت پروکسی و کانفیگ", callback_data="get_all"),
        InlineKeyboardButton(text="📊 آمار امروز", callback_data="stats")
    )
    return builder.as_markup()

# ============ HANDLERS ============
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "🚀 **به ربات پروکسی و کانفیگ خوش آمدید!**\n\n"
        "برای دریافت لینک‌ها از دکمه‌های زیر استفاده کنید 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_menu()
    )
    await message.answer(
        "⚡ **دسترسی سریع:**",
        reply_markup=get_inline_keyboard()
    )

@dp.message(Command("forward"))
async def cmd_forward(message: Message):
    """Manual command to forward latest messages from channel"""
    await message.answer("🔄 در حال دریافت پیام‌ها از کانال...")
    await fetch_channel_posts()
    await send_proxy_list(message)

@dp.message(F.text == "📡 دریافت لینک‌های جدید")
async def get_new_links(message: Message):
    await send_proxy_list(message)

@dp.message(F.text == "📋 راهنما")
async def show_help(message: Message):
    await message.answer(
        "📖 **راهنما:**\n\n"
        "• پیام‌های کانال را به ربات Forward کنید\n"
        "• یا از دستور /forward استفاده کنید\n"
        "• ربات خودکار لینک‌ها را ذخیره می‌کند",
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message(F.text == "💬 پشتیبانی")
async def support(message: Message):
    await message.answer("📨 @YourUsername")

@dp.callback_query(F.data == "get_all")
async def inline_get_all(callback: types.CallbackQuery):
    await callback.answer("در حال دریافت...")
    await send_proxy_list(callback.message)

@dp.callback_query(F.data == "stats")
async def inline_stats(callback: types.CallbackQuery):
    await callback.answer()
    v2ray_count = sum(1 for m in proxy_storage if m["type"] == "v2ray")
    proxy_count = sum(1 for m in proxy_storage if m["type"] == "proxy")
    
    await callback.message.answer(
        f"📊 **آمار:**\n\n"
        f"🟢 V2Ray: {v2ray_count}\n"
        f"🔵 پروکسی: {proxy_count}\n"
        f"🕐 بروزرسانی: {last_update_time.strftime('%H:%M') if last_update_time else 'ندارد'}",
        parse_mode=ParseMode.MARKDOWN
    )

async def send_proxy_list(message: Message):
    if not proxy_storage:
        await message.answer(
            "❌ هنوز لینکی ذخیره نشده.\n\n"
            "📌 **دو روش برای اضافه کردن:**\n"
            "1️⃣ پیام‌های کانال را Forward کنید به ربات\n"
            "2️⃣ دستور /forward را بزنید",
            reply_markup=get_main_menu()
        )
        return
    
    for msg in proxy_storage[-5:]:  # Last 5 messages
        prefix = "🟢 V2Ray" if msg["type"] == "v2ray" else "🔵 پروکسی"
        await message.answer(
            f"{prefix}\n📅 {msg['date'].strftime('%Y-%m-%d %H:%M')}\n\n`{msg['text'][:400]}`",
            parse_mode=ParseMode.MARKDOWN
        )
    
    await message.answer("✅", reply_markup=get_main_menu())

# ============ MAIN ============
async def main():
    logger.info("🚀 Starting...")
    
    Thread(target=run_health_server, daemon=True).start()
    asyncio.create_task(periodic_update())
    
    logger.info("✅ Ready!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

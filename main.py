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
from aiohttp import web

# ============ CONFIG ============
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")  # Like: -1001234567890
PORT = int(os.getenv("PORT", 8080))

if not BOT_TOKEN or not CHANNEL_ID:
    raise ValueError("BOT_TOKEN and CHANNEL_ID must be set in environment variables")

CHANNEL_ID = int(CHANNEL_ID)

# ============ STORAGE ============
# In-memory storage (for production use Redis or PostgreSQL)
proxy_storage: List[Dict] = []
last_update_time = None

# ============ BOT SETUP ============
bot = Bot(token=BOT_TOKEN)
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
    logger.info(f"Health check server started on port {PORT}")

def run_health_server():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_health_server())
    loop.run_forever()

# ============ FUNCTIONS ============
async def fetch_channel_posts():
    """Fetch posts from channel and update storage"""
    global proxy_storage, last_update_time
    
    try:
        # Get last 5 messages from channel
        updates = await bot.get_updates(offset=-1, limit=5, timeout=30)
        
        messages = []
        async for message in bot.get_chat_history(CHANNEL_ID, limit=5):
            if message.text and message.date:
                # Check if message is within 24 hours
                if datetime.now() - message.date < timedelta(hours=24):
                    messages.append({
                        "id": message.message_id,
                        "text": message.text,
                        "date": message.date,
                        "type": "v2ray" if "vmess" in message.text.lower() or "vless" in message.text.lower() else "proxy"
                    })
        
        if messages:
            proxy_storage = messages
            last_update_time = datetime.now()
            logger.info(f"Updated storage with {len(messages)} messages")
            
    except Exception as e:
        logger.error(f"Failed to fetch channel posts: {e}")

async def clean_old_posts():
    """Remove posts older than 24 hours"""
    global proxy_storage
    now = datetime.now()
    proxy_storage = [msg for msg in proxy_storage if now - msg["date"] < timedelta(hours=24)]
    logger.info(f"Cleaned old posts. Remaining: {len(proxy_storage)}")

async def periodic_update():
    """Run update every 2 hours"""
    while True:
        try:
            await fetch_channel_posts()
            await clean_old_posts()
        except Exception as e:
            logger.error(f"Update failed: {e}")
        await asyncio.sleep(7200)  # 2 hours

# ============ KEYBOARDS ============
def get_main_menu():
    """Main menu with colored ReplyKeyboard"""
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
    """Inline keyboard with colored buttons"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔵 دریافت پروکسی و کانفیگ", callback_data="get_all"),
        InlineKeyboardButton(text="📊 آمار امروز", callback_data="stats")
    )
    builder.row(
        InlineKeyboardButton(text="💬 پشتیبانی", url="https://t.me/YourUsername")
    )
    return builder.as_markup()

# ============ HANDLERS ============
@dp.message(Command("start"))
async def cmd_start(message: Message):
    welcome_text = (
        "🚀 **به ربات پروکسی و کانفیگ خوش آمدید!**\n\n"
        "🔹 این ربات هر ۲ ساعت از کانال مخصوص، پروکسی و کانفیگ‌های جدید دریافت می‌کند.\n"
        "🔸 لینک‌های قدیمی‌تر از ۲۴ ساعت خودکار حذف می‌شوند.\n\n"
        "برای دریافت لینک‌ها از دکمه‌های زیر استفاده کنید 👇"
    )
    
    await message.answer(
        welcome_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_menu()
    )
    
    # Also show inline keyboard
    await message.answer(
        "⚡ **دسترسی سریع:**",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_inline_keyboard()
    )

@dp.message(F.text == "📡 دریافت لینک‌های جدید")
async def get_new_links(message: Message):
    await send_proxy_list(message)

@dp.message(F.text == "📋 راهنما")
async def show_help(message: Message):
    help_text = (
        "📖 **راهنمای ربات:**\n\n"
        "1️⃣ دکمه 'دریافت لینک‌های جدید' را بزنید\n"
        "2️⃣ ربات آخرین پروکسی‌ها و کانفیگ‌ها را نشان می‌دهد\n"
        "3️⃣ لینک‌ها هر ۲ ساعت بروز می‌شوند\n"
        "4️⃣ محتوای قدیمی‌تر از ۲۴ ساعت حذف می‌شود\n\n"
        "⚠️ برای استفاده از کانفیگ‌ها، آنها را در کلاینت V2Ray خود کپی کنید."
    )
    await message.answer(help_text, parse_mode=ParseMode.MARKDOWN)

@dp.message(F.text == "💬 پشتیبانی")
async def support(message: Message):
    await message.answer(
        "📨 برای پشتیبانی به آیدی زیر پیام دهید:\n@YourUsername",
        parse_mode=ParseMode.MARKDOWN
    )

@dp.callback_query(F.data == "get_all")
async def inline_get_all(callback: types.CallbackQuery):
    await callback.answer("در حال دریافت...")
    await send_proxy_list(callback.message)
    await callback.message.delete()

@dp.callback_query(F.data == "stats")
async def inline_stats(callback: types.CallbackQuery):
    await callback.answer()
    
    v2ray_count = sum(1 for msg in proxy_storage if msg["type"] == "v2ray")
    proxy_count = sum(1 for msg in proxy_storage if msg["type"] == "proxy")
    
    stats_text = (
        "📊 **آمار امروز:**\n\n"
        f"🟢 کانفیگ V2Ray: {v2ray_count} عدد\n"
        f"🔵 پروکسی: {proxy_count} عدد\n"
        f"📅 آخرین بروزرسانی: {last_update_time.strftime('%H:%M:%S') if last_update_time else 'نامشخص'}"
    )
    
    await callback.message.answer(stats_text, parse_mode=ParseMode.MARKDOWN)

async def send_proxy_list(message: Message):
    """Send proxy list to user"""
    if not proxy_storage:
        await message.answer(
            "❌ هنوز هیچ لینکی دریافت نشده. لطفاً چند دقیقه دیگر تلاش کنید.",
            reply_markup=get_main_menu()
        )
        return
    
    await message.answer("📡 **در حال دریافت لینک‌ها...**", parse_mode=ParseMode.MARKDOWN)
    
    for msg in proxy_storage:
        try:
            if msg["type"] == "v2ray":
                await message.answer(
                    f"🟢 **کانفیگ V2Ray:**\n\n`{msg['text']}`\n\n📅 {msg['date'].strftime('%Y-%m-%d %H:%M')}",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await message.answer(
                    f"🔵 **پروکسی:**\n\n`{msg['text']}`\n\n📅 {msg['date'].strftime('%Y-%m-%d %H:%M')}",
                    parse_mode=ParseMode.MARKDOWN
                )
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
    
    await message.answer(
        "✅ **دریافت شد!** برای بروزرسانی مجدد، دوباره کلیک کنید.",
        reply_markup=get_main_menu()
    )

# ============ MAIN ============
async def main():
    # Start health check server in separate thread
    health_thread = Thread(target=run_health_server, daemon=True)
    health_thread.start()
    
    # Initial fetch
    await fetch_channel_posts()
    
    # Start periodic update task
    asyncio.create_task(periodic_update())
    
    # Start bot
    logger.info("Bot started!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

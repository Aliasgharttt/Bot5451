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
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PORT = int(os.getenv("PORT", "8080"))

# ============ STORAGE ============
proxy_storage: List[Dict] = []
last_checked_msg_id = 0
last_update_time = None

# ============ BOT SETUP ============
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties())
dp = Dispatcher()

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

def run_health_server():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_health_server())
    loop.run_forever()

# ============ CORE FUNCTION ============
async def fetch_new_messages():
    """Forward new messages from channel to admin, then read them"""
    global proxy_storage, last_checked_msg_id, last_update_time
    
    try:
        # Forward latest message to admin
        try:
            forwarded = await bot.forward_message(
                chat_id=ADMIN_ID,
                from_chat_id=CHANNEL_ID,
                message_id=last_checked_msg_id + 1  # Try next message
            )
            
            if forwarded and forwarded.text:
                # Got a new message!
                last_checked_msg_id = forwarded.forward_from_message_id
                
                # Check what type
                text = forwarded.text.lower()
                if "vmess" in text or "vless" in text or "v2ray" in text or "نپستر" in text:
                    msg_type = "v2ray"
                    emoji = "🟢"
                elif "proxy" in text or "پروکسی" in text or "mtproto" in text:
                    msg_type = "proxy"
                    emoji = "🔵"
                else:
                    msg_type = "proxy"
                    emoji = "🔵"
                
                # Save it
                proxy_storage.append({
                    "id": forwarded.message_id,
                    "text": forwarded.text,
                    "date": forwarded.date,
                    "type": msg_type,
                    "emoji": emoji
                })
                
                # Delete forwarded message from admin chat
                await bot.delete_message(ADMIN_ID, forwarded.message_id)
                
                last_update_time = datetime.now()
                logger.info(f"✅ New {msg_type} saved! Total: {len(proxy_storage)}")
                
        except Exception as e:
            # No new message or error - that's fine
            if "message to forward not found" not in str(e):
                logger.debug(f"No new message: {e}")
    
    except Exception as e:
        logger.error(f"Fetch error: {e}")

async def clean_old_posts():
    """Remove posts older than 24 hours"""
    global proxy_storage
    now = datetime.now()
    before = len(proxy_storage)
    proxy_storage = [m for m in proxy_storage if now - m["date"] < timedelta(hours=24)]
    if before != len(proxy_storage):
        logger.info(f"🧹 Cleaned: {before} -> {len(proxy_storage)}")

async def auto_fetch_loop():
    """Check for new messages every 60 seconds"""
    global last_checked_msg_id
    
    # First, find the current latest message ID
    try:
        chat = await bot.get_chat(CHANNEL_ID)
        logger.info(f"📡 Monitoring channel: {chat.full_name}")
        
        # Forward latest message to find its ID
        forwarded = await bot.forward_message(ADMIN_ID, CHANNEL_ID, chat.pinned_message.message_id if chat.pinned_message else 1)
        last_checked_msg_id = forwarded.forward_from_message_id if forwarded.forward_from_message_id else 0
        await bot.delete_message(ADMIN_ID, forwarded.message_id)
        logger.info(f"Starting from message ID: {last_checked_msg_id}")
    except Exception as e:
        logger.warning(f"Could not get latest message: {e}")
    
    while True:
        try:
            await fetch_new_messages()
            await clean_old_posts()
        except Exception as e:
            logger.error(f"Loop error: {e}")
        
        await asyncio.sleep(60)  # Check every 60 seconds

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
        "🚀 **ربات پروکسی و کانفیگ**\n\n"
        "🔹 کانال به صورت خودکار بررسی می‌شود\n"
        "🔸 لینک‌های قدیمی (>۲۴ ساعت) حذف می‌شوند\n\n"
        "👇 از دکمه‌های زیر استفاده کنید",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_menu()
    )
    await message.answer("⚡ **دسترسی سریع:**", reply_markup=get_inline_keyboard())

@dp.message(F.text == "📡 دریافت لینک‌های جدید")
async def get_new_links(message: Message):
    await send_proxy_list(message)

@dp.message(F.text == "📋 راهنما")
async def show_help(message: Message):
    await message.answer(
        "📖 **راهنما:**\n\n"
        "• ربات خودکار کانال رو هر ۱ دقیقه چک می‌کنه\n"
        "• V2Ray، نپستر، MTProto و پروکسی تشخیص داده میشه\n"
        "• لینک‌های قدیمی (>۲۴ ساعت) حذف میشن",
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
    v2ray = sum(1 for m in proxy_storage if m["type"] == "v2ray")
    proxy = sum(1 for m in proxy_storage if m["type"] == "proxy")
    
    await callback.message.answer(
        f"📊 **آمار امروز:**\n\n"
        f"🟢 V2Ray/نپستر: {v2ray}\n"
        f"🔵 پروکسی: {proxy}\n"
        f"🕐 آخرین: {last_update_time.strftime('%H:%M') if last_update_time else 'ندارد'}",
        parse_mode=ParseMode.MARKDOWN
    )

async def send_proxy_list(message: Message):
    if not proxy_storage:
        await message.answer(
            "❌ هنوز لینکی ذخیره نشده.\n"
            "⏳ ربات هر ۶۰ ثانیه کانال رو چک می‌کنه.\n"
            "📌 یه پیام جدید تو کانال بفرست، ۱ دقیقه بعد چک کن.",
            reply_markup=get_main_menu()
        )
        return
    
    await message.answer(f"📡 **{len(proxy_storage)} لینک فعال:**\n", parse_mode=ParseMode.MARKDOWN)
    
    for msg in proxy_storage[-10:]:  # Last 10
        await message.answer(
            f"{msg['emoji']} **{msg['type'].upper()}**\n"
            f"📅 {msg['date'].strftime('%m/%d %H:%M')}\n\n"
            f"`{msg['text'][:400]}`",
            parse_mode=ParseMode.MARKDOWN
        )
    
    await message.answer("✅", reply_markup=get_main_menu())

# ============ MAIN ============
async def main():
    logger.info("🚀 Starting bot...")
    
    Thread(target=run_health_server, daemon=True).start()
    asyncio.create_task(auto_fetch_loop())
    
    logger.info("✅ Bot is ready!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

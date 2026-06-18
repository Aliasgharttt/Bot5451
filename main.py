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
PORT = int(os.getenv("PORT", "8080"))

# ============ STORAGE ============
proxy_storage: List[Dict] = []
last_update_time = None
known_message_ids = set()

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
    logger.info(f"Health server on {PORT}")

def run_health_server():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_health_server())
    loop.run_forever()

# ============ CHANNEL POST HANDLER ============
@dp.channel_post()
async def handle_channel_post(message: Message):
    """Automatically catch new posts in channel"""
    global proxy_storage, last_update_time, known_message_ids
    
    if message.chat.id == CHANNEL_ID and message.text:
        # Check if we already have this message
        if message.message_id in known_message_ids:
            return
        
        known_message_ids.add(message.message_id)
        
        text = message.text.lower()
        if "vmess" in text or "vless" in text or "v2ray" in text or "نپستر" in text:
            msg_type = "v2ray"
            emoji = "🟢"
        else:
            msg_type = "proxy"
            emoji = "🔵"
        
        proxy_storage.append({
            "id": message.message_id,
            "text": message.text,
            "date": message.date,
            "type": msg_type,
            "emoji": emoji
        })
        
        # Clean old
        now = datetime.now()
        proxy_storage[:] = [m for m in proxy_storage if now - m["date"] < timedelta(hours=24)]
        
        last_update_time = datetime.now()
        logger.info(f"✅ Auto-saved {msg_type}! Total: {len(proxy_storage)}")

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
        "🔹 پیام‌های جدید کانال خودکار ذخیره می‌شوند\n"
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
        "• ربات خودکار پیام‌های کانال رو می‌خونه\n"
        "• V2Ray، نپستر، MTProto و پروکسی تشخیص داده میشه\n"
        "• لینک‌های قدیمی (>۲۴ ساعت) حذف میشن\n"
        "• کافیه تو کانال پیام بدی!",
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
            "❌ هنوز لینکی ذخیره نشده.\n\n"
            "📌 **کافیه تو کانال پیام بفرستی!**\n"
            "ربات خودکار می‌خونه و ذخیره می‌کنه.",
            reply_markup=get_main_menu()
        )
        return
    
    await message.answer(f"📡 **{len(proxy_storage)} لینک فعال:**\n", parse_mode=ParseMode.MARKDOWN)
    
    for msg in proxy_storage[-10:]:
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
    
    logger.info("✅ Bot ready! Send a message to your channel...")
    await dp.start_polling(bot, allowed_updates=["message", "channel_post"])

if __name__ == "__main__":
    asyncio.run(main())

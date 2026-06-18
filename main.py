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
    Message, FSInputFile
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

# ============ DETECTION ============
def detect_type(text: str) -> str:
    """Detect config/proxy type from text"""
    text_lower = text.lower()
    
    # V2Ray types
    v2ray_keywords = [
        'vmess://', 'vless://', 'trojan://', 'hysteria2://', 'hysteria://',
        'tuic://', 'ss://', 'ssr://', 'shadowrocket://', 'socks://',
        'wireguard://', 'wg://'
    ]
    
    for keyword in v2ray_keywords:
        if keyword in text_lower:
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
    """Automatically catch new posts in channel"""
    global proxy_storage, last_update_time, known_message_ids
    
    if message.chat.id != CHANNEL_ID:
        return
    
    # Check for forwarded message
    if message.forward_from_chat and message.forward_from_chat.id == CHANNEL_ID:
        return
    
    # Check if we already have this message
    if message.message_id in known_message_ids:
        return
    
    known_message_ids.add(message.message_id)
    
    # Handle NEPSTER file (.npvt)
    if message.document:
        file_name = message.document.file_name or ""
        if is_npvt_file(file_name):
            proxy_storage.append({
                "id": message.message_id,
                "text": message.caption or "نپستر کانفیگ",
                "date": message.date,
                "type": "nepster",
                "emoji": "🟣",
                "file_id": message.document.file_id,
                "file_name": file_name
            })
            last_update_time = datetime.now()
            logger.info(f"✅ Nepster file saved: {file_name}")
            return
    
    # Handle text messages
    if message.text:
        msg_type = detect_type(message.text)
        
        if msg_type == "v2ray":
            emoji = "🟢"
        else:
            emoji = "🔵"
        
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

# ============ KEYBOARDS ============
def get_main_menu():
    """Main menu"""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="📡 همه لینک‌ها", style=ButtonStyle.SUCCESS)
    )
    builder.row(
        KeyboardButton(text="🟢 V2Ray", style=ButtonStyle.PRIMARY),
        KeyboardButton(text="🔵 پروکسی", style=ButtonStyle.SUCCESS)
    )
    builder.row(
        KeyboardButton(text="🟣 نپستر", style=ButtonStyle.DANGER)
    )
    builder.row(
        KeyboardButton(text="📋 راهنما"),
        KeyboardButton(text="📊 آمار")
    )
    return builder.as_markup(resize_keyboard=True)

def get_inline_keyboard():
    """Inline keyboard for quick access"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🟢 دریافت V2Ray", callback_data="get_v2ray"),
        InlineKeyboardButton(text="🔵 دریافت پروکسی", callback_data="get_proxy")
    )
    builder.row(
        InlineKeyboardButton(text="🟣 دریافت نپستر", callback_data="get_nepster"),
        InlineKeyboardButton(text="📡 همه", callback_data="get_all")
    )
    builder.row(
        InlineKeyboardButton(text="📊 آمار", callback_data="stats")
    )
    return builder.as_markup()

# ============ HANDLERS ============
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "🚀 **به ربات پروکسی و کانفیگ خوش آمدید!**\n\n"
        "🔹 **V2Ray**: vless, vmess, trojan, hysteria2, tuic\n"
        "🔹 **پروکسی**: HTTP, SOCKS5, MTProto\n"
        "🔹 **نپستر**: فایل .npvt\n\n"
        "👇 از دکمه‌های زیر استفاده کنید",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_menu()
    )
    await message.answer("⚡ **دسترسی سریع:**", reply_markup=get_inline_keyboard())

@dp.message(F.text == "📡 همه لینک‌ها")
async def get_all_links(message: Message):
    await send_filtered_list(message, "all")

@dp.message(F.text == "🟢 V2Ray")
async def get_v2ray_links(message: Message):
    await send_filtered_list(message, "v2ray")

@dp.message(F.text == "🔵 پروکسی")
async def get_proxy_links(message: Message):
    await send_filtered_list(message, "proxy")

@dp.message(F.text == "🟣 نپستر")
async def get_nepster_links(message: Message):
    await send_filtered_list(message, "nepster")

@dp.message(F.text == "📋 راهنما")
async def show_help(message: Message):
    await message.answer(
        "📖 **راهنمای ربات:**\n\n"
        "• ربات خودکار کانال رو می‌خونه\n"
        "• **V2Ray**: vless, vmess, trojan, hysteria2\n"
        "• **پروکسی**: HTTP, SOCKS5, MTProto\n"
        "• **نپستر**: فایل‌های npvt\n"
        "• همه لینک‌ها از کانال دریافت میشن",
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message(F.text == "📊 آمار")
async def show_stats(message: Message):
    v2ray = sum(1 for m in proxy_storage if m["type"] == "v2ray")
    proxy = sum(1 for m in proxy_storage if m["type"] == "proxy")
    nepster = sum(1 for m in proxy_storage if m["type"] == "nepster")
    
    await message.answer(
        f"📊 **آمار ربات:**\n\n"
        f"🟢 V2Ray: {v2ray} عدد\n"
        f"🔵 پروکسی: {proxy} عدد\n"
        f"🟣 نپستر: {nepster} عدد\n"
        f"📦 مجموع: {len(proxy_storage)}\n"
        f"🕐 آخرین: {last_update_time.strftime('%H:%M') if last_update_time else 'ندارد'}",
        parse_mode=ParseMode.MARKDOWN
    )

@dp.callback_query(F.data == "get_all")
async def inline_get_all(callback: types.CallbackQuery):
    await callback.answer("در حال دریافت...")
    await send_filtered_list(callback.message, "all")

@dp.callback_query(F.data == "get_v2ray")
async def inline_get_v2ray(callback: types.CallbackQuery):
    await callback.answer("دریافت V2Ray...")
    await send_filtered_list(callback.message, "v2ray")

@dp.callback_query(F.data == "get_proxy")
async def inline_get_proxy(callback: types.CallbackQuery):
    await callback.answer("دریافت پروکسی...")
    await send_filtered_list(callback.message, "proxy")

@dp.callback_query(F.data == "get_nepster")
async def inline_get_nepster(callback: types.CallbackQuery):
    await callback.answer("دریافت نپستر...")
    await send_filtered_list(callback.message, "nepster")

@dp.callback_query(F.data == "stats")
async def inline_stats(callback: types.CallbackQuery):
    await callback.answer()
    v2ray = sum(1 for m in proxy_storage if m["type"] == "v2ray")
    proxy = sum(1 for m in proxy_storage if m["type"] == "proxy")
    nepster = sum(1 for m in proxy_storage if m["type"] == "nepster")
    
    await callback.message.answer(
        f"📊 **آمار:**\n"
        f"🟢 V2Ray: {v2ray}\n"
        f"🔵 پروکسی: {proxy}\n"
        f"🟣 نپستر: {nepster}\n"
        f"📦 کل: {len(proxy_storage)}",
        parse_mode=ParseMode.MARKDOWN
    )

# ============ SEND FUNCTIONS ============
async def send_filtered_list(message: Message, filter_type: str):
    """Send configs one by one based on filter"""
    
    if filter_type == "all":
        items = proxy_storage.copy()
    else:
        items = [m for m in proxy_storage if m["type"] == filter_type]
    
    if not items:
        type_names = {
            "all": "هیچ",
            "v2ray": "V2Ray",
            "proxy": "پروکسی",
            "nepster": "نپستر"
        }
        await message.answer(
            f"❌ {type_names.get(filter_type, 'هیچ')} لینکی ذخیره نشده.\n"
            "📌 در کانال پیام بفرستید.",
            reply_markup=get_main_menu()
        )
        return
    
    await message.answer(
        f"📡 **{len(items)} عدد پیدا شد**\n"
        "در حال ارسال...",
        parse_mode=ParseMode.MARKDOWN
    )
    
    for i, item in enumerate(items, 1):
        try:
            # NEPSTER - Send as file
            if item["type"] == "nepster" and item.get("file_id"):
                await bot.send_document(
                    chat_id=message.chat.id,
                    document=item["file_id"],
                    caption=f"🟣 **نپستر #{i}**\n📅 {item['date'].strftime('%Y-%m-%d %H:%M')}\n📄 {item.get('file_name', 'config.npvt')}",
                    parse_mode=ParseMode.MARKDOWN
                )
            
            # V2Ray - Send as code (no link preview)
            elif item["type"] == "v2ray":
                # Extract first link from text
                lines = item["text"].strip().split('\n')
                config_links = []
                for line in lines:
                    line = line.strip()
                    if any(line.lower().startswith(p) for p in ['vmess://', 'vless://', 'trojan://', 'hysteria2://', 'hysteria://', 'tuic://', 'ss://', 'ssr://']):
                        config_links.append(line)
                    elif line and not line.startswith('🔹') and not line.startswith('🟢') and not line.startswith('📡'):
                        if any(p in line.lower() for p in ['vmess://', 'vless://', 'trojan://']):
                            config_links.append(line)
                
                if config_links:
                    for link in config_links[:3]:  # Max 3 links per message
                        await message.answer(
                            f"🟢 **V2Ray #{i}**\n📅 {item['date'].strftime('%Y-%m-%d %H:%M')}\n\n`{link}`",
                            parse_mode=ParseMode.MARKDOWN,
                            disable_web_page_preview=True
                        )
                else:
                    await message.answer(
                        f"🟢 **V2Ray #{i}**\n📅 {item['date'].strftime('%Y-%m-%d %H:%M')}\n\n`{item['text'][:400]}`",
                        parse_mode=ParseMode.MARKDOWN,
                        disable_web_page_preview=True
                    )
            
            # PROXY - Send as clickable links
            elif item["type"] == "proxy":
                lines = item["text"].strip().split('\n')
                for line in lines:
                    line = line.strip()
                    if line and not line.startswith('🔹') and not line.startswith('🔵') and not line.startswith('📡'):
                        # Check if it's an MTProto link
                        if 't.me/proxy' in line:
                            await message.answer(
                                f"🔵 **MTProto #{i}**\n📅 {item['date'].strftime('%Y-%m-%d %H:%M')}\n\n[کلیک برای اتصال]({line})",
                                parse_mode=ParseMode.MARKDOWN
                            )
                        # Check if it's IP:PORT format
                        elif ':' in line and not line.startswith('http'):
                            parts = line.split(':')
                            if len(parts) >= 2:
                                ip = parts[0].strip()
                                port = parts[1].strip().split()[0]  # Get port only
                                await message.answer(
                                    f"🔵 **پروکسی #{i}**\n📅 {item['date'].strftime('%Y-%m-%d %H:%M')}\n\n"
                                    f"**IP:** `{ip}`\n**Port:** `{port}`",
                                    parse_mode=ParseMode.MARKDOWN
                                )
                        else:
                            await message.answer(
                                f"🔵 **پروکسی #{i}**\n📅 {item['date'].strftime('%Y-%m-%d %H:%M')}\n\n`{line[:200]}`",
                                parse_mode=ParseMode.MARKDOWN
                            )
            
            # Small delay between messages
            await asyncio.sleep(0.3)
            
        except Exception as e:
            logger.error(f"Failed to send item {i}: {e}")
    
    await message.answer(
        f"✅ **{len(items)} مورد ارسال شد!**",
        reply_markup=get_main_menu()
    )

# ============ MAIN ============
async def main():
    logger.info("🚀 Starting bot...")
    
    Thread(target=run_health_server, daemon=True).start()
    
    logger.info("✅ Bot ready!")
    await dp.start_polling(bot, allowed_updates=["message", "channel_post"])

if __name__ == "__main__":
    asyncio.run(main())

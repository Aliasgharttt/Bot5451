# ============ MANAGE PANEL ============
ITEMS_PER_PAGE = 10

async def send_manage_page(target, state: FSMContext, manage_type: str, title: str, items: list, page: int, edit: bool = False):
    total_pages = max(1, (len(items) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_items = items[start_idx:end_idx]
    
    text = f"{title} ها (صفحه {page + 1}/{total_pages}):\n\n"
    for i, item in enumerate(page_items, start_idx + 1):
        if manage_type == "nepster":
            text += f"{i}️⃣ {item.get('file_name', 'Unknown')}\n"
        else:
            text += f"{i}️⃣ {item['text'][:70].replace(chr(10), ' ')}...\n"
        text += f"   📅 {to_jalali(item['date'])}\n\n"
    text += "شماره (۳) | چندتایی (۱,۴,۷) | بازه (۱-۹) | all"
    
    builder = InlineKeyboardBuilder()
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text="⬅️", callback_data=f"manage_page_{page - 1}"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton(text="➡️", callback_data=f"manage_page_{page + 1}"))
    if row:
        builder.row(*row)
    builder.row(InlineKeyboardButton(text="❌ خروج", callback_data="manage_exit"))
    markup = builder.as_markup()
    
    await state.update_data(manage_type=manage_type, manage_items=items, current_page=page)
    await state.set_state(ManageState.waiting_for_delete)
    
    if edit:
        await target.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)
    else:
        await target.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=markup)

@dp.message(Command("manage"))
async def cmd_manage(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    v2ray_count = len(get_from_db("v2ray"))
    proxy_count = len(get_from_db("proxy"))
    nepster_count = len(get_from_db("nepster"))
    await message.answer(
        f"🛠 **پنل مدیریت**\n\n"
        f"🟢 V2Ray: {v2ray_count}\n"
        f"🔵 پروکسی: {proxy_count}\n"
        f"🟣 نپستر: {nepster_count}\n"
        f"📊 کل: {v2ray_count + proxy_count + nepster_count}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_manage_menu()
    )

@dp.callback_query(F.data == "manage_exit")
async def manage_exit(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()

@dp.callback_query(F.data.in_(["manage_v2ray", "manage_proxy", "manage_nepster"]))
async def manage_show_list(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("دسترسی غیرمجاز", show_alert=True)
        return
    type_map = {
        "manage_v2ray": ("v2ray", "🟢 V2Ray"),
        "manage_proxy": ("proxy", "🔵 پروکسی"),
        "manage_nepster": ("nepster", "🟣 نپستر")
    }
    filter_type, title = type_map[callback.data]
    items = get_from_db(filter_type)
    if not items:
        await callback.answer(f"{title} خالیه", show_alert=True)
        return
    await send_manage_page(callback.message, state, filter_type, title, items, 0, edit=True)

@dp.callback_query(F.data.startswith("manage_page_"))
async def manage_paginate(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("دسترسی غیرمجاز", show_alert=True)
        return
    page = int(callback.data.split("_")[2])
    data = await state.get_data()
    items = data.get("manage_items", [])
    manage_type = data.get("manage_type", "")
    type_names = {"v2ray": "🟢 V2Ray", "proxy": "🔵 پروکسی", "nepster": "🟣 نپستر"}
    title = type_names.get(manage_type, "")
    await send_manage_page(callback.message, state, manage_type, title, items, page, edit=True)
    await callback.answer()

@dp.message(ManageState.waiting_for_delete)
async def manage_delete(message: Message, state: FSMContext):
    if message.text == "برگشت":
        await state.clear()
        return await cmd_manage(message, state)
    
    data = await state.get_data()
    items = data.get("manage_items", [])
    manage_type = data.get("manage_type", "")
    current_page = data.get("current_page", 0)
    
    if message.text.lower() == "all":
        delete_from_db(filter_type=manage_type)
        await message.answer(f"✅ همه {len(items)} مورد حذف شدند!")
        await state.clear()
        return await cmd_manage(message, state)
    
    text = message.text.strip()
    indices = set()
    
    try:
        if '-' in text and ',' not in text:
            start, end = text.split('-')
            for i in range(int(start), int(end) + 1):
                indices.add(i - 1)
        elif ',' in text:
            for part in text.split(','):
                indices.add(int(part.strip()) - 1)
        else:
            indices.add(int(text) - 1)
    except ValueError:
        return await message.answer("❌ فرمت اشتباه. مثال: ۳ یا ۱,۴,۷ یا ۱-۹ یا all")
    
    invalid = [i + 1 for i in indices if i < 0 or i >= len(items)]
    if invalid:
        return await message.answer(f"❌ اعداد {invalid} خارج از محدوده (۱ تا {len(items)})")
    
    for index in sorted(indices, reverse=True):
        delete_from_db(db_id=items[index]["db_id"])
    
    await message.answer(f"✅ {len(indices)} مورد حذف شد!")
    
    items = get_from_db(manage_type)
    if not items:
        await state.clear()
        return await cmd_manage(message, state)
    
    type_names = {"v2ray": "🟢 V2Ray", "proxy": "🔵 پروکسی", "nepster": "🟣 نپستر"}
    title = type_names.get(manage_type, "")
    await send_manage_page(message, state, manage_type, title, items, current_page, edit=False)

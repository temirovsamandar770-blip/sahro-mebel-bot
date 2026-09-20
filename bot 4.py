import asyncio
import base64
import logging
import os
import sqlite3

import aiohttp
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message, InputMediaPhoto, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ============================================================
# SOZLAMALAR — shu joyni o'zingizga moslang
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8987682267:AAHsfYU2_i2iU3bXf-SDZN74EsHFFTV6WKQ")

ADMIN_IDS = [
    7596992302,  # sizning Telegram ID raqamingiz
]

# OpenAI (ChatGPT) API kaliti — xonalar rasmini tahrirlab, mos mebel qo'shib beruvchi AI uchun.
# Railway loyihangizda "Variables" bo'limiga OPENAI_API_KEY nomi bilan qo'shing.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

DB_PATH = "furniture_bot.db"

CATEGORIES = [
    ("yotoqxona", "🛏 Yotoqxona mebellari"),
    ("mehmonxona", "🛋 Mehmonxona mebellari"),
    ("oshxona", "🍽 Oshxona mebellari"),
    ("yumshoq", "🛋 Yumshoq mebellar"),
    ("bolalar", "🧸 Bolalar mebellari"),
]

ORDER_TYPES = [
    ("zakaz", "📐 Zakaz mebel (buyurtma asosida)"),
    ("tayyor", "✅ Tayyor mebellar"),
]

DEFAULT_SETTINGS = {
    "phone_1": "+998901234567",
    "phone_2": "+998901234568",
    "phone_3": "+998901234569",
    "phone_4": "+998901234570",
    "telegram_username": "@sahro_mebel_manager",
    "instagram": "https://instagram.com/sahro_mebel",
    "telegram_group": "https://t.me/sahro_mebel_group",
    "video_qollanma": "",
}

# ============================================================
# DATABASE
# ============================================================

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS furniture (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_type TEXT NOT NULL,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            code TEXT,
            size TEXT,
            color TEXT,
            price TEXT,
            photo_file_id TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    for k, v in DEFAULT_SETTINGS.items():
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()


def get_setting(key):
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else ""


def set_setting(key, value):
    conn = get_conn()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def add_furniture(order_type, category, name, code, size, color, price, photo_file_id):
    conn = get_conn()
    conn.execute(
        """INSERT INTO furniture
        (order_type, category, name, code, size, color, price, photo_file_id)
        VALUES (?,?,?,?,?,?,?,?)""",
        (order_type, category, name, code, size, color, price, photo_file_id),
    )
    conn.commit()
    conn.close()


def get_furniture(order_type, category):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM furniture WHERE order_type=? AND category=? ORDER BY id DESC",
        (order_type, category),
    ).fetchall()
    conn.close()
    return rows


# ============================================================
# FSM HOLATLARI
# ============================================================

class AddFurniture(StatesGroup):
    order_type = State()
    category = State()
    photo = State()
    name = State()
    code = State()
    size = State()
    color = State()
    price = State()


class EditValue(StatesGroup):
    value = State()


class AIDesign(StatesGroup):
    waiting_photo = State()


# ============================================================
# TUGMALAR (KEYBOARDS)
# ============================================================

def is_admin(user_id):
    return user_id in ADMIN_IDS


def main_menu(admin=False):
    b = InlineKeyboardBuilder()
    b.button(text="📖 Qo'llanma", callback_data="menu_qollanma")
    b.button(text="🪑 Mebellar", callback_data="menu_mebel")
    b.button(text="📞 Aloqa", callback_data="menu_aloqa")
    b.button(text="🛒 Zakaz berish", callback_data="menu_zakaz")
    b.button(text="🤖 AI dizayner", callback_data="menu_ai")
    if admin:
        b.button(text="⚙️ Admin panel", callback_data="menu_admin")
    b.adjust(1)
    return b.as_markup()


def furniture_type_menu():
    b = InlineKeyboardBuilder()
    for code, title in ORDER_TYPES:
        b.button(text=title, callback_data=f"ordertype_{code}")
    b.button(text="⬅️ Orqaga", callback_data="back_main")
    b.adjust(1)
    return b.as_markup()


def category_menu(order_type):
    b = InlineKeyboardBuilder()
    for code, title in CATEGORIES:
        b.button(text=title, callback_data=f"cat_{order_type}_{code}")
    b.button(text="⬅️ Orqaga", callback_data="menu_mebel")
    b.adjust(1)
    return b.as_markup()


def contact_menu():
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Orqaga", callback_data="back_main")
    return b.as_markup()


def contact_text():
    lines = ["👨‍💼 Menejerlarimiz bilan bog'laning:\n"]
    for i in range(1, 5):
        phone = get_setting(f"phone_{i}")
        if phone:
            lines.append(f"📞 Menejer {i}: {phone}")
    lines.append("\nRaqamga bosib, to'g'ridan-to'g'ri qo'ng'iroq qilishingiz mumkin.")
    return "\n".join(lines)


def order_contact_text():
    lines = ["🛒 Zakaz berish uchun menejerlarimiz bilan bog'laning:\n"]
    for i in range(1, 5):
        phone = get_setting(f"phone_{i}")
        if phone:
            lines.append(f"📞 {phone}")
    return "\n".join(lines)


def order_contact_menu():
    b = InlineKeyboardBuilder()
    tg = get_setting("telegram_username")
    if tg:
        b.button(text="✈️ Telegram", url=f"https://t.me/{tg.lstrip('@')}")
    ig = get_setting("instagram")
    if ig:
        b.button(text="📷 Instagram", url=ig)
    grp = get_setting("telegram_group")
    if grp:
        b.button(text="👥 Telegram guruh", url=grp)
    b.button(text="⬅️ Orqaga", callback_data="back_main")
    b.adjust(1)
    return b.as_markup()


QOLLANMA_TEXT = (
    "📖 SAHRO MEBEL botidan foydalanish qo'llanmasi\n\n"
    "🪑 Mebellar — mebel turini (Zakaz asosida yoki Tayyor) va bo'limni "
    "(Yotoqxona, Mehmonxona, Oshxona, Yumshoq, Bolalar) tanlab, mahsulotlarni "
    "rasmi, o'lchami, rangi va narxi bilan ko'rishingiz mumkin.\n\n"
    "🤖 AI dizayner — xona turini tanlang, AI shu uslubga mos 5 ta dizayn "
    "g'oyasini yaratib beradi.\n\n"
    "📞 Aloqa — menejerlarimizning telefon raqamlari shu yerda, raqamga "
    "bosib to'g'ridan-to'g'ri qo'ng'iroq qilishingiz mumkin.\n\n"
    "🛒 Zakaz berish — buyurtma berish uchun menejer, Telegram, Instagram "
    "va guruh havolalari shu yerda.\n\n"
    "Savol tug'ilsa, 📞 Aloqa bo'limidan menejerlarimizga murojaat qiling."
)


# ============================================================
# ROUTER — QO'LLANMA
# ============================================================

qollanma_router = Router()


@qollanma_router.callback_query(F.data == "menu_qollanma")
async def menu_qollanma(call: CallbackQuery):
    text = QOLLANMA_TEXT
    video = get_setting("video_qollanma")
    if video:
        text += f"\n\n🎬 Video qo'llanma: {video}"
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Orqaga", callback_data="back_main")
    await call.message.edit_text(text, reply_markup=b.as_markup())


# ============================================================
# ROUTER — MIJOZ MENYULARI
# ============================================================

user_router = Router()


@user_router.message(CommandStart())
async def start(message: Message):
    text = (
        f"Assalomu alaykum, {message.from_user.first_name}! 👋\n\n"
        "SAHRO MEBEL botiga xush kelibsiz.\n"
        "Quyidagi bo'limlardan birini tanlang:"
    )
    await message.answer(text, reply_markup=main_menu(is_admin(message.from_user.id)))


@user_router.callback_query(F.data == "back_main")
async def back_main(call: CallbackQuery):
    await call.message.edit_text(
        "Bosh menyu. Bo'limni tanlang:",
        reply_markup=main_menu(is_admin(call.from_user.id)),
    )


@user_router.callback_query(F.data == "menu_mebel")
async def menu_mebel(call: CallbackQuery):
    await call.message.edit_text("Qaysi turdagi mebel kerak?", reply_markup=furniture_type_menu())


@user_router.callback_query(F.data.startswith("ordertype_"))
async def order_type_chosen(call: CallbackQuery):
    order_type = call.data.split("_", 1)[1]
    await call.message.edit_text("Bo'limni tanlang:", reply_markup=category_menu(order_type))


@user_router.callback_query(F.data.startswith("cat_"))
async def category_chosen(call: CallbackQuery):
    _, order_type, category = call.data.split("_", 2)
    items = get_furniture(order_type, category)
    if not items:
        await call.answer("Hozircha bu bo'limda mahsulot yo'q.", show_alert=True)
        return
    await call.message.answer(f"Topildi: {len(items)} ta mahsulot 👇")
    zakaz_kb = InlineKeyboardBuilder()
    zakaz_kb.button(text="🛒 Zakaz berish", callback_data="menu_zakaz")
    zakaz_markup = zakaz_kb.as_markup()
    for item in items:
        caption = (
            f"🪑 {item['name']}\n"
            + (f"🔖 Kod: {item['code']}\n" if item["code"] else "")
            + (f"📏 O'lcham: {item['size']}\n" if item["size"] else "")
            + (f"🎨 Rang: {item['color']}\n" if item["color"] else "")
            + (f"💵 Narx: {item['price']}\n" if item["price"] else "")
        )
        await call.message.answer_photo(item["photo_file_id"], caption=caption, reply_markup=zakaz_markup)
    await call.message.answer("Yana bo'lim tanlashingiz mumkin:", reply_markup=category_menu(order_type))


@user_router.callback_query(F.data == "menu_aloqa")
async def menu_aloqa(call: CallbackQuery):
    await call.message.edit_text(contact_text(), reply_markup=contact_menu())


@user_router.callback_query(F.data == "menu_zakaz")
async def menu_zakaz(call: CallbackQuery):
    await call.message.edit_text(order_contact_text(), reply_markup=order_contact_menu())


# ============================================================
# ROUTER — AI DIZAYNER
# ============================================================

ai_router = Router()

# Har bir bo'lim uchun ingliz tilida stil ta'rifi (AI ingliz tilida yaxshiroq tushunadi)
PROMPTS = {
    "yotoqxona": "cozy modern bedroom furniture (bed, wardrobe, nightstands)",
    "mehmonxona": "elegant modern living room furniture (sofa set, coffee table, TV stand)",
    "oshxona": "modern kitchen and dining furniture (table, chairs, cabinets)",
    "yumshoq": "cozy soft upholstered furniture (sofa, armchairs)",
    "bolalar": "colorful cozy children's bedroom furniture (bed, desk, wardrobe)",
}


@ai_router.callback_query(F.data == "menu_ai")
async def menu_ai(call: CallbackQuery, state: FSMContext):
    b = InlineKeyboardBuilder()
    for code, title in CATEGORIES:
        b.button(text=title, callback_data=f"ai_cat_{code}")
    b.button(text="⬅️ Orqaga", callback_data="back_main")
    b.adjust(1)
    await call.message.edit_text(
        "🤖 AI dizayner (ChatGPT)\n\n"
        "Qaysi xona uchun dizayn kerak, tanlang. Keyin xonangizning rasmini yuboring — "
        "ChatGPT aynan o'sha rasmga, devor rangi va uslubiga mos mebel qo'shib beradi.",
        reply_markup=b.as_markup(),
    )


@ai_router.callback_query(F.data.startswith("ai_cat_"))
async def ai_category(call: CallbackQuery, state: FSMContext):
    category = call.data.split("_", 2)[2]
    await state.update_data(category=category)
    await state.set_state(AIDesign.waiting_photo)
    await call.message.edit_text("📸 Endi xonangizning aniq va yorug' rasmini yuboring.")


@ai_router.message(AIDesign.waiting_photo, F.photo)
async def ai_generate(message: Message, state: FSMContext, bot: Bot):
    if not OPENAI_API_KEY:
        await message.answer(
            "⚠️ AI dizayner hali sozlanmagan (OpenAI API kaliti kiritilmagan). "
            "Admin bilan bog'laning."
        )
        await state.clear()
        return

    data = await state.get_data()
    category = data.get("category", "mehmonxona")
    style = PROMPTS.get(category, PROMPTS["mehmonxona"])
    await message.answer("🎨 ChatGPT orqali dizaynlar tayyorlanmoqda, 30-60 soniya kuting...")

    photo = message.photo[-1]
    tg_file = await bot.get_file(photo.file_id)
    file_io = await bot.download_file(tg_file.file_path)
    image_bytes = file_io.read()

    prompt = (
        f"Edit this real room photo. Keep the walls, floor, windows and lighting exactly as they are, "
        f"do not change the room's architecture. Add tastefully matching {style} that complements the "
        f"existing wall color and overall style. Photorealistic, high quality interior design result."
    )

    media = []
    try:
        form = aiohttp.FormData()
        form.add_field("model", "gpt-image-1-mini")
        form.add_field("image", image_bytes, filename="room.jpg", content_type="image/jpeg")
        form.add_field("prompt", prompt)
        form.add_field("n", "4")
        form.add_field("size", "1024x1024")
        form.add_field("quality", "medium")
        headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.openai.com/v1/images/edits", headers=headers, data=form
            ) as resp:
                result = await resp.json()

        if "data" in result:
            for idx, item in enumerate(result["data"]):
                img_bytes = base64.b64decode(item["b64_json"])
                media.append(
                    InputMediaPhoto(
                        media=BufferedInputFile(img_bytes, filename=f"ai_design_{idx + 1}.png"),
                        caption="🎨 ChatGPT AI dizayn g'oyalari" if idx == 0 else None,
                    )
                )
        else:
            logging.error(f"OpenAI xatoligi: {result}")
    except Exception:
        logging.exception("OpenAI so'rovida xatolik yuz berdi")

    if media:
        await message.answer_media_group(media)
        await message.answer("Mos mebellarni tanlash uchun 🪑 Mebellar bo'limiga o'ting.")
    else:
        await message.answer("Kechirasiz, AI dizaynlarni yarata olmadi. Birozdan so'ng qayta urinib ko'ring.")
    await state.clear()


@ai_router.message(AIDesign.waiting_photo)
async def ai_need_photo(message: Message):
    await message.answer("📸 Iltimos, matn emas, xonangizning rasmini (fotosini) yuboring.")


# ============================================================
# ROUTER — ADMIN PANEL
# ============================================================

admin_router = Router()


@admin_router.callback_query(F.data == "menu_admin")
async def menu_admin(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q.", show_alert=True)
        return
    b = InlineKeyboardBuilder()
    b.button(text="➕ Mebel qo'shish", callback_data="adm_add")
    b.button(text="📞 Telefon raqamlarni o'zgartirish", callback_data="adm_phones")
    b.button(text="✈️ Telegram/Instagram/Guruh linklarni o'zgartirish", callback_data="adm_links")
    b.button(text="⬅️ Orqaga", callback_data="back_main")
    b.adjust(1)
    await call.message.edit_text("⚙️ Admin panel", reply_markup=b.as_markup())


@admin_router.callback_query(F.data == "adm_add")
async def adm_add(call: CallbackQuery, state: FSMContext):
    b = InlineKeyboardBuilder()
    for code, title in ORDER_TYPES:
        b.button(text=title, callback_data=f"adm_type_{code}")
    b.adjust(1)
    await call.message.edit_text("Mebel turi:", reply_markup=b.as_markup())
    await state.set_state(AddFurniture.order_type)


@admin_router.callback_query(AddFurniture.order_type, F.data.startswith("adm_type_"))
async def adm_type(call: CallbackQuery, state: FSMContext):
    order_type = call.data.split("_", 2)[2]
    await state.update_data(order_type=order_type)
    b = InlineKeyboardBuilder()
    for code, title in CATEGORIES:
        b.button(text=title, callback_data=f"adm_cat_{code}")
    b.adjust(1)
    await call.message.edit_text("Bo'lim:", reply_markup=b.as_markup())
    await state.set_state(AddFurniture.category)


@admin_router.callback_query(AddFurniture.category, F.data.startswith("adm_cat_"))
async def adm_cat(call: CallbackQuery, state: FSMContext):
    category = call.data.split("_", 2)[2]
    await state.update_data(category=category)
    await call.message.edit_text("📸 Mebel rasmini yuboring:")
    await state.set_state(AddFurniture.photo)


@admin_router.message(AddFurniture.photo, F.photo)
async def adm_photo(message: Message, state: FSMContext):
    await state.update_data(photo_file_id=message.photo[-1].file_id)
    await message.answer("📝 Mebel nomini yozing:")
    await state.set_state(AddFurniture.name)


@admin_router.message(AddFurniture.name)
async def adm_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("🔖 Mebel kodini yozing (bo'lmasa '-' yozing):")
    await state.set_state(AddFurniture.code)


@admin_router.message(AddFurniture.code)
async def adm_code(message: Message, state: FSMContext):
    await state.update_data(code=message.text)
    await message.answer("📏 O'lchamini yozing (masalan: 200x160):")
    await state.set_state(AddFurniture.size)


@admin_router.message(AddFurniture.size)
async def adm_size(message: Message, state: FSMContext):
    await state.update_data(size=message.text)
    await message.answer("🎨 Rangini yozing:")
    await state.set_state(AddFurniture.color)


@admin_router.message(AddFurniture.color)
async def adm_color(message: Message, state: FSMContext):
    await state.update_data(color=message.text)
    await message.answer("💵 Narxini yozing:")
    await state.set_state(AddFurniture.price)


@admin_router.message(AddFurniture.price)
async def adm_price(message: Message, state: FSMContext):
    data = await state.get_data()
    add_furniture(
        order_type=data["order_type"], category=data["category"], name=data["name"],
        code=data["code"], size=data["size"], color=data["color"],
        price=message.text, photo_file_id=data["photo_file_id"],
    )
    await message.answer("✅ Mebel muvaffaqiyatli qo'shildi!", reply_markup=main_menu(True))
    await state.clear()


@admin_router.callback_query(F.data == "adm_phones")
async def adm_phones(call: CallbackQuery):
    b = InlineKeyboardBuilder()
    for i in range(1, 5):
        current = get_setting(f"phone_{i}")
        b.button(text=f"{i}) {current}", callback_data=f"adm_ph_{i}")
    b.adjust(1)
    await call.message.edit_text("Qaysi raqamni o'zgartirmoqchisiz?", reply_markup=b.as_markup())


@admin_router.callback_query(F.data.startswith("adm_ph_"))
async def adm_ph_choose(call: CallbackQuery, state: FSMContext):
    i = call.data.split("_")[-1]
    await state.update_data(which=f"phone_{i}")
    await state.set_state(EditValue.value)
    await call.message.edit_text("Yangi telefon raqamini yuboring (masalan: +998901234567):")


@admin_router.callback_query(F.data == "adm_links")
async def adm_links(call: CallbackQuery):
    b = InlineKeyboardBuilder()
    b.button(text="✈️ Telegram username", callback_data="adm_lnk_telegram_username")
    b.button(text="📷 Instagram link", callback_data="adm_lnk_instagram")
    b.button(text="👥 Telegram guruh link", callback_data="adm_lnk_telegram_group")
    b.button(text="🎬 Video qo'llanma link", callback_data="adm_lnk_video_qollanma")
    b.adjust(1)
    await call.message.edit_text("Nimani o'zgartirasiz?", reply_markup=b.as_markup())


@admin_router.callback_query(F.data.startswith("adm_lnk_"))
async def adm_lnk_choose(call: CallbackQuery, state: FSMContext):
    key = call.data[len("adm_lnk_"):]
    await state.update_data(which=key)
    await state.set_state(EditValue.value)
    await call.message.edit_text("Yangi qiymatini yuboring:")


@admin_router.message(EditValue.value)
async def adm_value_save(message: Message, state: FSMContext):
    data = await state.get_data()
    set_setting(data["which"], message.text.strip())
    await message.answer("✅ Yangilandi!", reply_markup=main_menu(True))
    await state.clear()


# ============================================================
# BOTNI ISHGA TUSHIRISH
# ============================================================

async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin_router)
    dp.include_router(ai_router)
    dp.include_router(qollanma_router)
    dp.include_router(user_router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

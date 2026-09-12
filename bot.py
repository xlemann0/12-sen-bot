import asyncio
import datetime
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import uuid

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

import qrcode
from dotenv import load_dotenv


# =========================================================
# CONFIG
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("8946349098:AAGUSADz6F6tj2BBNxmw5I5gJakZru7quqs")

SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0"))

GITHUB_WEBAPP_URL = os.getenv(
    "GITHUB_WEBAPP_URL",
    "https://xlemann0.github.io/ttj-scanner/"
)

DB_NAME = "dorm_bot.db"
BACKUP_DIR = "backups"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN .env faylida topilmadi!")

if not SUPER_ADMIN_ID:
    raise RuntimeError("SUPER_ADMIN_ID .env faylida topilmadi!")


bot = Bot(token=BOT_TOKEN)

dp = Dispatcher(storage=MemoryStorage())

router = Router()


# =========================================================
# DATABASE
# =========================================================

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = get_db()
    cursor = conn.cursor()

    # Adminlar
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            telegram_id INTEGER PRIMARY KEY
        )
    """)

    # Talabalar
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            telegram_id INTEGER PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            course TEXT,
            room TEXT,
            phone TEXT
        )
    """)

    # Sozlamalar
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Davomat
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            status TEXT NOT NULL,
            UNIQUE(student_id, date)
        )
    """)

    # QR tokenlar
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS qr_tokens (
            token TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        )
    """)

    # Super Admin
    cursor.execute("""
        INSERT OR IGNORE INTO admins (telegram_id)
        VALUES (?)
    """, (SUPER_ADMIN_ID,))

    # Default sozlamalar
    default_settings = {
        "check_in_time": "20:00",
        "late_limit_minutes": "30",
        "qr_expiration_seconds": "60"
    }

    for key, value in default_settings.items():

        cursor.execute("""
            INSERT OR IGNORE INTO settings (key, value)
            VALUES (?, ?)
        """, (key, value))

    conn.commit()
    conn.close()


init_db()


# =========================================================
# DATABASE HELPERS
# =========================================================

def is_admin(tg_id: int) -> bool:

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT 1 FROM admins WHERE telegram_id = ?",
        (tg_id,)
    )

    result = cursor.fetchone()

    conn.close()

    return result is not None


def is_super_admin(tg_id: int) -> bool:
    return tg_id == SUPER_ADMIN_ID


def is_student(tg_id: int) -> bool:

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT 1 FROM students WHERE telegram_id = ?",
        (tg_id,)
    )

    result = cursor.fetchone()

    conn.close()

    return result is not None


def get_setting(key: str, default: str) -> str:

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT value FROM settings WHERE key = ?",
        (key,)
    )

    result = cursor.fetchone()

    conn.close()

    if result:
        return result["value"]

    return default


def set_setting(key: str, value: str):

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT OR REPLACE INTO settings (key, value)
        VALUES (?, ?)
    """, (key, value))

    conn.commit()
    conn.close()


# =========================================================
# VALIDATION
# =========================================================

def valid_time(value: str) -> bool:

    if not re.fullmatch(r"\d{2}:\d{2}", value):
        return False

    try:

        hour, minute = map(int, value.split(":"))

        return (
            0 <= hour <= 23
            and
            0 <= minute <= 59
        )

    except ValueError:
        return False


def valid_date(value: str) -> bool:

    try:
        datetime.datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def valid_month(value: str) -> bool:

    try:
        datetime.datetime.strptime(value, "%Y-%m")
        return True
    except ValueError:
        return False


# =========================================================
# FSM
# =========================================================

class AddStudentStates(StatesGroup):

    waiting_for_tg_id = State()
    waiting_for_first_name = State()
    waiting_for_last_name = State()
    waiting_for_course = State()
    waiting_for_room = State()
    waiting_for_phone = State()


class EditStudentStates(StatesGroup):

    waiting_for_tg_id = State()
    waiting_for_field = State()
    waiting_for_value = State()


class DeleteStudentStates(StatesGroup):

    waiting_for_tg_id = State()


class DeleteAdminStates(StatesGroup):

    waiting_for_tg_id = State()


class AddAdminStates(StatesGroup):

    waiting_for_tg_id = State()


class SearchStates(StatesGroup):

    waiting_for_query = State()


class MonthlyReportStates(StatesGroup):

    waiting_for_month = State()


class BroadcastStates(StatesGroup):

    waiting_for_message = State()


class DeleteAllStudentsStates(StatesGroup):

    waiting_for_confirmation = State()


class SettingsStates(StatesGroup):

    waiting_for_check_in_time = State()
    waiting_for_late_limit = State()
    waiting_for_qr_expiration = State()


# =========================================================
# KEYBOARDS
# =========================================================

def main_menu(tg_id: int):

    if is_admin(tg_id):

        kb = [

            [
                KeyboardButton(
                    text="📷 QR Skaner",
                    web_app=WebAppInfo(
                        url=GITHUB_WEBAPP_URL
                    )
                ),
                KeyboardButton(text="📷 QR kod olish")
            ],

            [
                KeyboardButton(text="➕ Talaba qo'shish"),
                KeyboardButton(text="✏️ Talabani tahrirlash")
            ],

            [
                KeyboardButton(text="👥 Talabalar"),
                KeyboardButton(text="🗑 Talabani o'chirish")
            ],

            [
                KeyboardButton(text="📊 Bugungi davomat"),
                KeyboardButton(text="❌ Kelmaganlar")
            ],

            [
                KeyboardButton(text="📈 Statistika"),
                KeyboardButton(text="🔍 Qidirish va Hisobot")
            ],

            [
                KeyboardButton(text="➕ Admin qo'shish"),
                KeyboardButton(text="🗑 Adminni o'chirish")
            ],

            [
                KeyboardButton(text="📢 Xabar tarqatish"),
                KeyboardButton(text="💾 Backup")
            ],

            [
                KeyboardButton(text="⚙️ Sozlamalar")
            ],

            [
                KeyboardButton(
                    text="🗑 Barcha talabalarni o'chirish"
                )
            ]
        ]

        return ReplyKeyboardMarkup(
            keyboard=kb,
            resize_keyboard=True
        )

    elif is_student(tg_id):

        kb = [

            [
                KeyboardButton(
                    text="📷 QR Skaner",
                    web_app=WebAppInfo(
                        url=GITHUB_WEBAPP_URL
                    )
                )
            ],

            [
                KeyboardButton(text="📊 Mening davomatim")
            ]

        ]

        return ReplyKeyboardMarkup(
            keyboard=kb,
            resize_keyboard=True
        )

    else:

        return ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(
                        text="Ro'yxatdan o'tmagansiz ❌"
                    )
                ]
            ],
            resize_keyboard=True
        )


def settings_keyboard():

    kb = [

        [
            InlineKeyboardButton(
                text="⏰ Davomat vaqtini o'zgartirish",
                callback_data="setting_time"
            )
        ],

        [
            InlineKeyboardButton(
                text="🟡 Kechikish vaqtini o'zgartirish",
                callback_data="setting_late"
            )
        ],

        [
            InlineKeyboardButton(
                text="📷 QR muddatini o'zgartirish",
                callback_data="setting_qr"
            )
        ]

    ]

    return InlineKeyboardMarkup(
        inline_keyboard=kb
    )


# =========================================================
# START
# =========================================================

@router.message(Command("start"))
async def cmd_start(
    message: Message,
    state: FSMContext
):

    await state.clear()

    tg_id = message.from_user.id

    if is_admin(tg_id) or is_student(tg_id):

        await message.answer(
            "🏠 **TTJ Davomat tizimi**\n\n"
            "Kerakli bo'limni tanlang:",
            reply_markup=main_menu(tg_id),
            parse_mode="Markdown"
        )

    else:

        await message.answer(
            "❌ Siz tizimda ro'yxatdan o'tmagansiz.\n\n"
            "Ro'yxatdan o'tish uchun administrator bilan bog'laning.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="👨‍💼 Adminga bog'lanish",
                            url="https://t.me/mekhanizatsiya"
                        )
                    ]
                ]
            )
        )


# =========================================================
# QR GENERATION
# =========================================================

@router.message(F.text == "📷 QR kod olish")
async def generate_qr(message: Message):

    if not is_admin(message.from_user.id):
        return

    token = uuid.uuid4().hex

    now = datetime.datetime.now()

    conn = get_db()
    cursor = conn.cursor()

    # Eski QRlarni tozalash
    cursor.execute("DELETE FROM qr_tokens")

    cursor.execute("""
        INSERT INTO qr_tokens (
            token,
            created_at
        )
        VALUES (?, ?)
    """, (
        token,
        now.isoformat()
    ))

    conn.commit()
    conn.close()

    qr_url = f"{GITHUB_WEBAPP_URL}?token={token}"

    qr_image = qrcode.make(qr_url)

    buffer = io.BytesIO()

    qr_image.save(
        buffer,
        format="PNG"
    )

    buffer.seek(0)

    expire_seconds = get_setting(
        "qr_expiration_seconds",
        "60"
    )

    await message.answer_photo(

        photo=BufferedInputFile(
            buffer.read(),
            filename="ttj_qr.png"
        ),

        caption=(
            "📷 **TTJ DAVOMAT QR KODI**\n\n"
            f"⏱ Amal qilish muddati: "
            f"**{expire_seconds} soniya**\n\n"
            "⚠️ Muddati o'tgach yangi QR yarating."
        ),

        parse_mode="Markdown"
    )


# =========================================================
# WEB APP / ATTENDANCE
# =========================================================

@router.message(F.web_app_data)
async def handle_web_app(message: Message):

    tg_id = message.from_user.id

    if not is_student(tg_id):

        if is_admin(tg_id):

            await message.answer(
                "ℹ️ Siz administratorsiz.\n"
                "Adminning shaxsiy davomati hisoblanmaydi."
            )

        else:

            await message.answer(
                "❌ Siz tizimda ro'yxatdan o'tmagansiz."
            )

        return

    try:

        data = json.loads(
            message.web_app_data.data
        )

        incoming_token = data.get("qr_token")

        if not incoming_token:

            await message.answer(
                "❌ QR token topilmadi!"
            )

            return

    except Exception:

        await message.answer(
            "❌ Web App ma'lumotlarini o'qishda xatolik."
        )

        return

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT created_at
        FROM qr_tokens
        WHERE token = ?
    """, (incoming_token,))

    token_row = cursor.fetchone()

    if not token_row:

        conn.close()

        await message.answer(
            "❌ QR kod noto'g'ri yoki muddati tugagan."
        )

        return

    try:

        created_at = datetime.datetime.fromisoformat(
            token_row["created_at"]
        )

        age = (
            datetime.datetime.now()
            - created_at
        ).total_seconds()

        expiration = int(
            get_setting(
                "qr_expiration_seconds",
                "60"
            )
        )

        if age > expiration:

            conn.close()

            await message.answer(
                "❌ QR kodning amal qilish muddati tugagan."
            )

            return

    except Exception:

        conn.close()

        await message.answer(
            "❌ QR tekshirishda xatolik."
        )

        return

    now = datetime.datetime.now()

    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    # Bugun oldin o'tganmi?
    cursor.execute("""
        SELECT id
        FROM attendance
        WHERE student_id = ?
        AND date = ?
    """, (
        tg_id,
        date_str
    ))

    already = cursor.fetchone()

    if already:

        conn.close()

        await message.answer(
            "⚠️ Siz bugun allaqachon davomatdan o'tgansiz!"
        )

        return

    # Davomat vaqti
    check_in_str = get_setting(
        "check_in_time",
        "20:00"
    )

    late_limit = int(
        get_setting(
            "late_limit_minutes",
            "30"
        )
    )

    target_h, target_m = map(
        int,
        check_in_str.split(":")
    )

    current_minutes = (
        now.hour * 60
        + now.minute
    )

    target_minutes = (
        target_h * 60
        + target_m
    )

    difference = (
        current_minutes
        - target_minutes
    )

    # Juda erta
    if difference < 0:

        status = "Kelgan 🟢"

    # O'z vaqtida
    elif difference == 0:

        status = "Kelgan 🟢"

    # Kechikkan
    elif difference <= late_limit:

        status = (
            f"Kechikkan 🟡 "
            f"({difference} min)"
        )

    # Juda kech
    else:

        conn.close()

        await message.answer(
            "🔴 **Davomat vaqti o'tib ketgan!**\n\n"
            f"⏰ Davomat vaqti: {check_in_str}\n"
            f"🟡 Ruxsat etilgan kechikish: "
            f"{late_limit} daqiqa",
            parse_mode="Markdown"
        )

        return

    try:

        cursor.execute("""
            INSERT INTO attendance (
                student_id,
                date,
                time,
                status
            )
            VALUES (?, ?, ?, ?)
        """, (
            tg_id,
            date_str,
            time_str,
            status
        ))

        conn.commit()

        await message.answer(
            "✅ **Davomat muvaffaqiyatli qayd etildi!**\n\n"
            f"📅 Sana: {date_str}\n"
            f"⏰ Vaqt: {time_str}\n"
            f"📊 Holat: {status}",
            parse_mode="Markdown"
        )

    except sqlite3.IntegrityError:

        await message.answer(
            "⚠️ Siz bugun allaqachon davomatdan o'tgansiz!"
        )

    finally:

        conn.close()


# =========================================================
# MY ATTENDANCE
# =========================================================

@router.message(F.text == "📊 Mening davomatim")
async def my_attendance(message: Message):

    if not is_student(message.from_user.id):
        return

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT date, time, status
        FROM attendance
        WHERE student_id = ?
        ORDER BY date DESC
        LIMIT 30
    """, (
        message.from_user.id,
    ))

    records = cursor.fetchall()

    conn.close()

    if not records:

        await message.answer(
            "📭 Sizda hali davomat tarixi yo'q."
        )

        return

    text = "📊 **Sizning davomat tarixingiz:**\n\n"

    for record in records:

        text += (
            f"📅 {record['date']} | "
            f"⏰ {record['time']} | "
            f"{record['status']}\n"
        )

    await message.answer(
        text,
        parse_mode="Markdown"
    )


# =========================================================
# TODAY ATTENDANCE
# =========================================================

@router.message(F.text == "📊 Bugungi davomat")
async def today_attendance(message: Message):

    if not is_admin(message.from_user.id):
        return

    today = datetime.datetime.now().strftime(
        "%Y-%m-%d"
    )

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            s.first_name,
            s.last_name,
            s.room,
            a.time,
            a.status
        FROM attendance a
        JOIN students s
            ON a.student_id = s.telegram_id
        WHERE a.date = ?
        ORDER BY s.room
    """, (
        today,
    ))

    records = cursor.fetchall()

    conn.close()

    if not records:

        await message.answer(
            f"📅 {today} uchun hali davomat yo'q."
        )

        return

    text = (
        f"📊 **Bugungi davomat**\n"
        f"📅 {today}\n\n"
    )

    for r in records:

        text += (
            f"🏠 {r['room']} | "
            f"{r['first_name']} {r['last_name']}\n"
            f"⏰ {r['time']} — {r['status']}\n\n"
        )

    await message.answer(
        text,
        parse_mode="Markdown"
    )


# =========================================================
# ABSENT STUDENTS
# =========================================================

@router.message(F.text == "❌ Kelmaganlar")
async def absent_students(message: Message):

    if not is_admin(message.from_user.id):
        return

    today = datetime.datetime.now().strftime(
        "%Y-%m-%d"
    )

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            s.first_name,
            s.last_name,
            s.room,
            s.phone
        FROM students s
        WHERE NOT EXISTS (
            SELECT 1
            FROM attendance a
            WHERE a.student_id = s.telegram_id
            AND a.date = ?
        )
        ORDER BY s.room
    """, (
        today,
    ))

    records = cursor.fetchall()

    conn.close()

    if not records:

        await message.answer(
            f"🎉 {today} kuni barcha talabalar davomatdan o'tgan!"
        )

        return

    text = (
        f"❌ **Bugun kelmaganlar**\n"
        f"📅 {today}\n\n"
    )

    for r in records:

        text += (
            f"🏠 {r['room']} | "
            f"{r['first_name']} {r['last_name']}\n"
            f"📞 {r['phone']}\n\n"
        )

    await message.answer(
        text,
        parse_mode="Markdown"
    )


# =========================================================
# STATISTICS
# =========================================================

@router.message(F.text == "📈 Statistika")
async def statistics(message: Message):

    if not is_admin(message.from_user.id):
        return

    today = datetime.datetime.now().strftime(
        "%Y

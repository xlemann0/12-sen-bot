import asyncio
import datetime
import io
import json
import logging
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

# --- CONFIG ---
BOT_TOKEN = "8946349098:AAEdpBjScBu3n8VfxZOA1GoUnrcPyFjx_f0"
SUPER_ADMIN_ID = 5874144878
GITHUB_WEBAPP_URL = "https://xlemann0.github.io/ttj-scanner/"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()


# --- DATABASE SETUP ---
def init_db():
  conn = sqlite3.connect("dorm_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      "CREATE TABLE IF NOT EXISTS admins (telegram_id INTEGER PRIMARY KEY)"
  )
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            telegram_id INTEGER PRIMARY KEY,
            first_name TEXT,
            last_name TEXT,
            course TEXT,
            room TEXT,
            phone TEXT
        )
    """)
  cursor.execute(
      "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
  )
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            date TEXT,
            time TEXT,
            status TEXT,
            UNIQUE(student_id, date)
        )
    """)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS qr_tokens (
            token TEXT PRIMARY KEY,
            created_at TEXT
        )
    """)
  cursor.execute(
      "INSERT OR IGNORE INTO admins (telegram_id) VALUES (?)",
      (SUPER_ADMIN_ID,),
  )
  cursor.execute(
      "INSERT OR IGNORE INTO settings (key, value) VALUES ('check_in_time',"
      " '20:00')"
  )
  cursor.execute(
      "INSERT OR IGNORE INTO settings (key, value) VALUES ('late_limit_minutes',"
      " '30')"
  )
  cursor.execute(
      "INSERT OR IGNORE INTO settings (key, value) VALUES ('qr_expiration_seconds',"
      " '60')"
  )
  conn.commit()
  conn.close()


init_db()


def is_admin(tg_id: int) -> bool:
  conn = sqlite3.connect("dorm_bot.db")
  cursor = conn.cursor()
  cursor.execute("SELECT 1 FROM admins WHERE telegram_id = ?", (tg_id,))
  res = cursor.fetchone()
  conn.close()
  return res is not None


def is_student(tg_id: int) -> bool:
  conn = sqlite3.connect("dorm_bot.db")
  cursor = conn.cursor()
  cursor.execute("SELECT 1 FROM students WHERE telegram_id = ?", (tg_id,))
  res = cursor.fetchone()
  conn.close()
  return res is not None


def get_setting(key: str, default: str) -> str:
  conn = sqlite3.connect("dorm_bot.db")
  cursor = conn.cursor()
  cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
  res = cursor.fetchone()
  conn.close()
  return res[0] if res else default


def set_setting(key: str, value: str):
  conn = sqlite3.connect("dorm_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
  )
  conn.commit()
  conn.close()


# --- FSM STATES ---
class AddStudentStates(StatesGroup):
  waiting_for_tg_id = State()
  waiting_for_first_name = State()
  waiting_for_last_name = State()
  waiting_for_course = State()
  waiting_for_room = State()
  waiting_for_phone = State()


class DeleteStudentStates(StatesGroup):
  waiting_for_tg_id = State()


class DeleteAdminStates(StatesGroup):
  waiting_for_tg_id = State()


class AdminSettingsStates(StatesGroup):
  waiting_for_new_admin_id = State()
  waiting_for_check_in_time = State()
  waiting_for_late_limit = State()


class SearchStates(StatesGroup):
  waiting_for_query = State()


class MonthlyReportStates(StatesGroup):
  waiting_for_month = State()


class BroadcastStates(StatesGroup):
  waiting_for_message = State()


class DeleteAllStudentsStates(StatesGroup):
  waiting_for_confirmation = State()


# --- KEYBOARDS ---
def main_menu(tg_id: int):
  if is_admin(tg_id):
    kb = [
        [
            KeyboardButton(
                text="📷 QR Skaner", web_app=WebAppInfo(url=GITHUB_WEBAPP_URL)
            ),
            KeyboardButton(text="📷 QR kod olish"),
        ],
        [KeyboardButton(text="➕ Talaba qo'shish"), KeyboardButton(text="👥 Talabalar")],
        [
            KeyboardButton(text="📊 Bugungi davomat"),
            KeyboardButton(text="❌ Kelmaganlar"),
        ],
        [KeyboardButton(text="🔍 Qidirish va Hisobot")],
        [
            KeyboardButton(text="➕ Admin qo'shish"),
            KeyboardButton(text="🗑 Adminni o'chirish"),
        ],
        [KeyboardButton(text="sh 🛡 Adminlar ro'yxati".replace("sh ", ""))],  # Adminlar ro'yxati tugmasi
        [
            KeyboardButton(text="🗑 Talabani o'chirish"),
            KeyboardButton(text="🗑 Barcha talabalarni o'chirish"),
        ],
        [KeyboardButton(text="📢 Xabar tarqatish")],
        [KeyboardButton(text="⏰ Davomat vaqti"), KeyboardButton(text="🟡 Kechikish vaqti")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
  elif is_student(tg_id):
    kb = [
        [
            KeyboardButton(
                text="📷 QR Skaner", web_app=WebAppInfo(url=GITHUB_WEBAPP_URL)
            )
        ],
        [KeyboardButton(text="📊 Mening davomatim")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
  else:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Ro'yxatdan o'tmagansiz ❌")]],
        resize_keyboard=True,
    )


# --- BACKGROUND TASK ---
async def clean_expired_qr_loop():
  while True:
    await asyncio.sleep(5)
    try:
      expire_secs = int(get_setting("qr_expiration_seconds", "60"))
      conn = sqlite3.connect("dorm_bot.db")
      cursor = conn.cursor()
      cursor.execute("SELECT token, created_at FROM qr_tokens")
      for t in cursor.fetchall():
        try:
          if (
              datetime.datetime.now()
              - datetime.datetime.fromisoformat(t[1])
          ).total_seconds() > expire_secs:
            cursor.execute("DELETE FROM qr_tokens WHERE token = ?", (t[0],))
        except Exception:
          pass
      conn.commit()
      conn.close()
    except Exception:
      pass


# --- HANDLERS ---
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
  await state.clear()
  tg_id = message.from_user.id
  if is_admin(tg_id) or is_student(tg_id):
    await message.answer(
        "Assalomu alaykum! Davomat menyusi:", reply_markup=main_menu(tg_id)
    )
  else:
    await message.answer(
        "Siz tizimda ro'yxatdan o'tmagansiz.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(
                    text="👨‍💻 Adminga bog'lanish", url="https://t.me/mekhanizatsiya"
                )
            ]]
        ),
    )


@router.message(F.text == "📷 QR kod olish")
async def get_admin_qr(message: Message):
  if not is_admin(message.from_user.id):
    return
  token = str(uuid.uuid4())[:8]
  now = datetime.datetime.now()

  conn = sqlite3.connect("dorm_bot.db")
  cursor = conn.cursor()
  cursor.execute("DELETE FROM qr_tokens")
  cursor.execute(
      "INSERT INTO qr_tokens (token, created_at) VALUES (?, ?)", (token, now.isoformat())
  )
  conn.commit()
  conn.close()

  webapp_qr_link = f"{GITHUB_WEBAPP_URL}?token={token}"
  img = qrcode.make(webapp_qr_link)
  bio = io.BytesIO()
  img.save(bio, format="PNG")
  bio.seek(0)
  expire_secs = get_setting("qr_expiration_seconds", "60")

  await message.answer_photo(
      photo=BufferedInputFile(bio.read(), filename="qr.png"),
      caption=(
          f"📷 **Vaqtinchalik QR Kod**\n⏱ Bu kod {expire_secs} soniya amal qiladi."
      ),
      parse_mode="Markdown",
  )


# --- WEB APP HANDLER (QR TOKEN ONLY) ---
@router.message(F.web_app_data)
async def handle_web_app(message: Message):
  tg_id = message.from_user.id
  if not is_student(tg_id):
    if is_admin(tg_id):
      return await message.answer(
          "ℹ️ Siz adminsiz, shuning uchun shaxsiy davomatingiz yozilmaydi."
      )
    return await message.answer("Siz tizimda ro'yxatdan o'tmagansiz.")

  try:
    data = json.loads(message.web_app_data.data)
    incoming_token = data.get("qr_token")
    if not incoming_token:
      return await message.answer("❌ QR kod tokeni topilmadi!")
  except Exception:
    return await message.answer("❌ Ma'lumotni o'qishda xatolik!")

  conn = sqlite3.connect("dorm_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      "SELECT created_at FROM qr_tokens WHERE token = ?", (incoming_token,)
  )
  token_row = cursor.fetchone()

  if not token_row:
    conn.close()
    return await message.answer(
        "❌ QR kod amal qilish muddati tugagan yoki noto'g'ri!"
    )

  try:
    if (
        datetime.datetime.now()
        - datetime.datetime.fromisoformat(token_row[0])
    ).total_seconds() > int(get_setting("qr_expiration_seconds", "60")):
      conn.close()
      return await message.answer("❌ QR kod muddati (60 sekund) o'tib ketdi!")
  except Exception:
    conn.close()
    return await message.answer("❌ Xatolik yuz berdi.")

  now = datetime.datetime.now()
  date_str, time_str = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")

  check_in_str = get_setting("check_in_time", "20:00")
  target_h, target_m = map(int, check_in_str.split(":"))
  late_limit = int(get_setting("late_limit_minutes", "30"))

  cur_mins = now.hour * 60 + now.minute
  target_mins = target_h * 60 + target_m

  if cur_mins < target_mins:
    status = "Kelgan 🟢"
  elif cur_mins <= target_mins + late_limit:
    status = f"Kechikkan 🟡 ({cur_mins - target_mins} min)"
  else:
    status = "Kelmagan / Vaqti o'tgan 🔴"

  try:
    cursor.execute(
        "INSERT INTO attendance (student_id, date, time, status) VALUES (?, ?, ?, ?)",
        (tg_id, date_str, time_str, status),
    )
    conn.commit()
    await message.answer(
        f"✅ Muvaffaqiyatli!\n📅 Sana: {date_str}\n⏰ Vaqt:"
        f" {time_str}\n📊 Holat: {status}"
    )
  except sqlite3.IntegrityError:
    await message.answer("⚠️ Siz bugun allaqachon davomatdan o'tgansiz!")
  finally:
    conn.close()


# --- BARCHA TALABALARNI O'CHIRISH ---
@router.message(F.text == "🗑 Barcha talabalarni o'chirish")
async def ask_delete_all_students(message: Message, state: FSMContext):
  if message.from_user.id != SUPER_ADMIN_ID:
    return await message.answer(
        "❌ Bu amalni faqat Super Admin bajara oladi!"
    )

  await state.set_state(DeleteAllStudentsStates.waiting_for_confirmation)
  await message.answer(
      "⚠️ **DIQQAT!** Siz bazadagi **BARCHA** talabalarni o'chirib"
      " yubormoqchisiz.\n\nBu amalni ortga qaytarib bo'lmaydi! Tasdiqlash uchun"
      ' aynan **"HAMMASINI O\'CHIRISH"** deb yozib yuboring.',
      parse_mode="Markdown",
  )


@router.message(DeleteAllStudentsStates.waiting_for_confirmation)
async def confirm_delete_all_students(message: Message, state: FSMContext):
  if message.from_user.id != SUPER_ADMIN_ID:
    await state.clear()
    return

  if message.text.strip() == "HAMMASINI O'CHIRISH":
    conn = sqlite3.connect("dorm_bot.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM students")
    conn.commit()
    conn.close()

    await state.clear()
    await message.answer(
        "✅ Bazadagi barcha talabalar muvaffaqiyatli o'chirib yuborildi!",
        reply_markup=main_menu(message.from_user.id),
    )
  else:
    await state.clear()
    await message.answer(
        "❌ Tasdiq so'zi noto'g'ri yozildi. Amal bekor qilindi.",
        reply_markup=main_menu(message.from_user.id),
    )


# --- STATISTIKA VA DAVOMATNI KO'RISH ---
@router.message(F.text == "📊 Mening davomatim")
async def my_attendance(message: Message):
  if not is_student(message.from_user.id):
    return
  conn = sqlite3.connect("dorm_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      "SELECT date, time, status FROM attendance WHERE student_id = ? ORDER BY"
      " date DESC LIMIT 10",
      (message.from_user.id,),
  )
  records = cursor.fetchall()
  conn.close()
  if not records:
    return await message.answer("📭 Sizda davomat tarixi yo'q.")
  text = "📊 **Sizning oxirgi davomatlaringiz:**\n\n"
  for r in records:
    text += f"📅 {r[0]} | ⏰ {r[1]} — {r[2]}\n"
  await message.answer(text, parse_mode="Markdown")


@router.message(F.text == "📊 Bugungi davomat")
async def today_attendance(message: Message):
  if not is_admin(message.from_user.id):
    return
  today = datetime.datetime.now().strftime("%Y-%m-%d")
  conn = sqlite3.connect("dorm_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      "SELECT s.first_name, s.last_name, s.room, a.time, a.status FROM"
      " attendance a JOIN students s ON a.student_id = s.telegram_id WHERE"
      " a.date = ?",
      (today,),
  )
  records = cursor.fetchall()
  conn.close()
  if not records:
    return await message.answer(f"📅 {today} uchun davomat yo'q.")
  text = f"📊 **Bugungi davomat ({today}):**\n\n"
  for r in records:
    text += f"🏠 {r[2]} | {r[0]} {r[1]} — {r[3]} ({r[4]})\n"
  await message.answer(text, parse_mode="Markdown")


@router.message(F.text == "👥 Talabalar")
async def list_students_menu(message: Message):
  if not is_admin(message.from_user.id):
    return
  conn = sqlite3.connect("dorm_bot.db")
  cursor = conn.cursor()
  cursor.execute("SELECT DISTINCT course FROM students")
  courses = [row[0] for row in cursor.fetchall() if row[0]]
  conn.close()

  if not courses:
    return await message.answer(
        "Hozircha bazada ro'yxatdan o'tgan talabalar yoki kurslar yo'q."
    )

  kb = []
  for course in courses:
    kb.append([
        InlineKeyboardButton(text=f"📚 {course}", callback_data=f"course_{course}")
    ])

  await message.answer(
      "👥 Talabalar ro'yxatini ko'rish uchun **kursni tanlang**:",
      reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
      parse_mode="Markdown",
  )


@router.callback_query(F.data.startswith("course_"))
async def show_students_by_course(callback: CallbackQuery):
  if not is_admin(callback.from_user.id):
    return
  selected_course = callback.data.replace("course_", "", 1)

  conn = sqlite3.connect("dorm_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      "SELECT first_name, last_name, room, phone, telegram_id FROM students"
      " WHERE course = ?",
      (selected_course,),
  )
  students = cursor.fetchall()
  conn.close()

  if not students:
    return await callback.answer(
        f"{selected_course} bo'yicha talabalar topilmadi.", show_alert=True
    )

  text = f"👥 **{selected_course} talabalari ro'yxati:**\n\n"
  for s in students:
    text += (
        f"👤 **{s[0]} {s[1]}**\n🏠 Xona: {s[2]} | 📞 Tel: {s[3]}\n🆔 ID:"
        f" `{s[4]}`\n-------------------\n"
    )

  await callback.message.edit_text(text, parse_mode="Markdown")
  await callback.answer()


@router.message(F.text == "❌ Kelmaganlar")
async def absent_students(message: Message):
  if not is_admin(message.from_user.id):
    return
  today = datetime.datetime.now().strftime("%Y-%m-%d")

  conn = sqlite3.connect("dorm_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      """
        SELECT first_name, last_name, room, phone FROM students 
        WHERE telegram_id NOT IN (
            SELECT student_id FROM attendance WHERE date = ?
        )
    """,
      (today,),
  )
  absents = cursor.fetchall()
  conn.close()

  if not absents:
    return await message.answer(
        f"🎉 Bugun ({today}) barcha talabalar davomatdan o'tgan!"
    )

  text = f"❌ **Bugun kelmaganlar ({today}):**\n\n"
  for a in absents:
    text += f"🏠 {a[2]} | {a[0]} {a[1]} — 📞 {a[3]}\n"
  await message.answer(text, parse_mode="Markdown")


@router.message(F.text == "➕ Talaba qo'shish")
async def start_add_student(message: Message, state: FSMContext):
  if not is_admin(message.from_user.id):
    return
  await message.answer("1️⃣ Talabaning Telegram ID raqamini kiriting:")
  await state.set_state(AddStudentStates.waiting_for_tg_id)


@router.message(AddStudentStates.waiting_for_tg_id)
async def process_tg_id(message: Message, state: FSMContext):
  if not message.text.isdigit():
    return await message.answer("Faqat raqam kiriting:")
  await state.update_data(tg_id=int(message.text))
  await message.answer("2️⃣ Talabaning Ismini kiriting:")
  await state.set_state(AddStudentStates.waiting_for_first_name)


@router.message(AddStudentStates.waiting_for_first_name)
async def process_first_name(message: Message, state: FSMContext):
  await state.update_data(first_name=message.text)
  await message.answer("3️⃣ Talabaning Familiyasini kiriting:")
  await state.set_state(AddStudentStates.waiting_for_last_name)


@router.message(AddStudentStates.waiting_for_last_name)
async def process_last_name(message: Message, state: FSMContext):
  await state.update_data(last_name=message.text)
  await message.answer("4️⃣ Kursini kiriting (masalan: 1-kurs):")
  await state.set_state(AddStudentStates.waiting_for_course)


@router.message(AddStudentStates.waiting_for_course)
async def process_course(message: Message, state: FSMContext):
  await state.update_data(course=message.text)
  await message.answer("5️⃣ Xona raqamini kiriting (masalan: 214):")
  await state.set_state(AddStudentStates.waiting_for_room)


@router.message(AddStudentStates.waiting_for_room)
async def process_room(message: Message, state: FSMContext):
  await state.update_data(room=message.text)
  await message.answer("6️⃣ Telefon raqamini kiriting (+998...):")
  await state.set_state(AddStudentStates.waiting_for_phone)


@router.message(AddStudentStates.waiting_for_phone)
async def process_phone(message: Message, state: FSMContext):
  data = await state.get_data()
  phone = message.text
  student_id = data["tg_id"]

  conn = sqlite3.connect("dorm_bot.db")
  cursor = conn.cursor()
  try:
    cursor.execute(
        "INSERT INTO students (telegram_id, first_name, last_name, course,"
        " room, phone) VALUES (?, ?, ?, ?, ?, ?)",
        (
            student_id,
            data["first_name"],
            data["last_name"],
            data["course"],
            data["room"],
            phone,
        ),
    )
    conn.commit()

    await message.answer(
        "✅ Talaba muvaffaqiyatli qo'shildi va unga xabar yuborildi!",
        reply_markup=main_menu(message.from_user.id),
    )
    try:
      await bot.send_message(
          chat_id=student_id,
          text=(
              "🎉 **Tabriklayman!** Siz talabalar turar joyi davomat"
              " tizimidan ro'yxatdan o'tdingiz."
          ),
          reply_markup=main_menu(student_id),
          parse_mode="Markdown",
      )
    except Exception:
      pass
  except Exception as e:
    await message.answer(f"Xatolik yuz berdi: {e}")
  finally:
    conn.close()
    await state.clear()


@router.message(F.text == "🗑 Talabani o'chirish")
async def start_delete_student(message: Message, state: FSMContext):
  if not is_admin(message.from_user.id):
    return
  await message.answer(
      "O'chirmoqchi bo'lgan talabaning **Telegram ID** raqamini kiriting:"
  )
  await state.set_state(DeleteStudentStates.waiting_for_tg_id)


@router.message(DeleteStudentStates.waiting_for_tg_id)
async def process_delete_student(message: Message, state: FSMContext):
  if not message.text.isdigit():
    return await message.answer("Faqat raqam kiriting:")
  student_id = int(message.text)

  conn = sqlite3.connect("dorm_bot.db")
  cursor = conn.cursor()
  cursor.execute("DELETE FROM students WHERE telegram_id = ?", (student_id,))
  deleted = cursor.rowcount
  conn.commit()
  conn.close()

  if deleted > 0:
    await message.answer(
        "✅ Talaba muvaffaqiyatli o'chirildi!",
        reply_markup=main_menu(message.from_user.id),
    )
  else:
    await message.answer(
        "⚠️ Bunday ID raqamli talaba topilmadi.",
        reply_markup=main_menu(message.from_user.id),
    )
  await state.clear()


# --- ADMINLAR RO'YXATI ---
@router.message(F.text == "Adminlar ro'yxati")
async def list_admins(message: Message):
  if not is_admin(message.from_user.id):
    return
  conn = sqlite3.connect("dorm_bot.db")
  cursor = conn.cursor()
  cursor.execute("SELECT telegram_id FROM admins")
  admins = cursor.fetchall()
  conn.close()

  text = "🛡 **Tizimdagi adminlar ro'yxati:**\n\n"
  for idx, a in enumerate(admins, 1):
    role = " ⭐ (Super Admin)" if a[0] == SUPER_ADMIN_ID else ""
    text += f"{idx}. ID: `{a[0]}`{role}\n"

  await message.answer(text, parse_mode="Markdown")


# --- ADMIN QO'SHISH (FAQAT SUPER ADMIN) ---
@router.message(F.text == "➕ Admin qo'shish")
async def start_add_admin(message: Message, state: FSMContext):
  if message.from_user.id != SUPER_ADMIN_ID:
    return await message.answer("❌ Bu amalni faqat Super Admin bajara oladi!")
  await message.answer("Yangi adminning Telegram ID raqamini kiriting:")
  await state.set_state(AdminSettingsStates.waiting_for_new_admin_id)


@router.message(AdminSettingsStates.waiting_for_new_admin_id)
async def process_new_admin(message: Message, state: FSMContext):
  if message.from_user.id != SUPER_ADMIN_ID:
    await state.clear()
    return
  if not message.text.isdigit():
    return await message.answer("Faqat raqam kiriting:")
  new_admin_id = int(message.text)

  conn = sqlite3.connect("dorm_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      "INSERT OR IGNORE INTO admins (telegram_id) VALUES (?)", (new_admin_id,)
  )
  conn.commit()
  conn.close()
  await message.answer(
      "✅ Yangi admin muvaffaqiyatli qo'shildi!",
      reply_markup=main_menu(message.from_user.id),
  )
  await state.clear()


@router.message(F.text == "🗑 Adminni o'chirish")
async def start_delete_admin(message: Message, state: FSMContext):
  if message.from_user.id != SUPER_ADMIN_ID:
    return await message.answer("❌ Bu amalni faqat Super Admin bajara oladi!")
  await message.answer(
      "O'chirmoqchi bo'lgan adminning **Telegram ID** raqamini kiriting:"
  )
  await state.set_state(DeleteAdminStates.waiting_for_tg_id)


@router.message(DeleteAdminStates.waiting_for_tg_id)
async def process_delete_admin(message: Message, state: FSMContext):
  if message.from_user.id != SUPER_ADMIN_ID:
    await state.clear()
    return
  if not message.text.isdigit():
    return await message.answer("Faqat raqam kiriting:")
  admin_id = int(message.text)

  if admin_id == SUPER_ADMIN_ID:
    await state.clear()
    return await message.answer(
        "❌ Asosiy bosh adminni o'chirib bo'lmaydi!",
        reply_markup=main_menu(message.from_user.id),
    )

  if admin_id == message.from_user.id:
    await state.clear()
    return await message.answer(
        "❌ O'zingizni o'chira olmaysiz!",
        reply_markup=main_menu(message.from_user.id),
    )

  conn = sqlite3.connect("dorm_bot.db")
  cursor = conn.cursor()
  cursor.execute("DELETE FROM admins WHERE telegram_id = ?", (admin_id,))
  deleted = cursor.rowcount
  conn.commit()
  conn.close()

  if deleted > 0:
    await message.answer(
        "✅ Admin muvaffaqiyatli o'chirildi!",
        reply_markup=main_menu(message.from_user.id),
    )
  else:
    await message.answer(
        "⚠️ Bunday ID raqamli admin topilmadi.",
        reply_markup=main_menu(message.from_user.id),
    )
  await state.clear()


@router.message(F.text == "⏰ Davomat vaqti")
async def start_change_time(message: Message, state: FSMContext):
  if not is_admin(message.from_user.id):
    return
  current = get_setting("check_in_time", "20:00")
  await message.answer(
      f"Joriy vaqt: {current}\nYangi vaqtni kiriting (masalan: 20:00):"
  )
  await state.set_state(AdminSettingsStates.waiting_for_check_in_time)


@router.message(AdminSettingsStates.waiting_for_check_in_time)
async def process_change_time(message: Message, state: FSMContext):
  set_setting("check_in_time", message.text)
  await message.answer(
      f"✅ Vaqt o'zgartirildi: {message.text}",
      reply_markup=main_menu(message.from_user.id),
  )
  await state.clear()


@router.message(F.text == "🟡 Kechikish vaqti")
async def start_change_late(message: Message, state: FSMContext):
  if not is_admin(message.from_user.id):
    return
  current = get_setting("late_limit_minutes", "30")
  await message.answer(
      f"Joriy kechikish chegarasi: {current} daqiqa\nYangi daqiqani kiriting:"
  )
  await state.set_state(AdminSettingsStates.waiting_for_late_limit)


@router.message(AdminSettingsStates.waiting_for_late_limit)
async def process_change_late(message: Message, state: FSMContext):
  if not message.text.isdigit():
    return await message.answer("Faqat raqam kiriting:")
  set_setting("late_limit_minutes", message.text)
  await message.answer(
      f"✅ Kechikish chegarasi {message.text} daqiqaga o'zgartirildi.",
      reply_markup=main_menu(message.from_user.id),
  )
  await state.clear()


@router.message(F.text == "📢 Xabar tarqatish")
async def start_broadcast(message: Message, state: FSMContext):
  if not is_admin(message.from_user.id):
    return
  await message.answer(
      "📢 Barcha talabalarga yubormoqchi bo'lgan xabaringizni kiriting:"
  )
  await state.set_state(BroadcastStates.waiting_for_message)


@router.message(BroadcastStates.waiting_for_message)
async def process_broadcast(message: Message, state: FSMContext):
  if not is_admin(message.from_user.id):
    return

  conn = sqlite3.connect("dorm_bot.db")
  cursor = conn.cursor()
  cursor.execute("SELECT telegram_id FROM students")
  students = cursor.fetchall()
  conn.close()

  success_count = 0
  fail_count = 0

  for student in students:
    student_id = student[0]
    try:
      if message.text:
        await bot.send_message(student_id, message.text)
      else:
        await message.send_copy(student_id)
      success_count += 1
      await asyncio.sleep(0.05)
    except Exception:
      fail_count += 1

  await message.answer(
      f"✅ Xabar tarqatish yakunlandi!\n\n"
      f"📤 Muvaffaqiyatli yetib bordi: {success_count}\n"
      f"❌ Xatoliklar: {fail_count}",
      reply_markup=main_menu(message.from_user.id),
  )
  await state.clear()


@router.message(F.text == "🔍 Qidirish va Hisobot")
async def search_hub(message: Message):
  if not is_admin(message.from_user.id):
    return
  kb = [
      [
          KeyboardButton(text="🔍 Sana bo'yicha qidirish"),
          KeyboardButton(text="👤 Ism / Familiya bo'yicha"),
      ],
      [KeyboardButton(text="📅 Oylik davomat hisoboti")],
  ]
  await message.answer(
      "🔍 Qidirish va hisobot turini tanlang:",
      reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True),
  )


@router.message(F.text == "🔍 Sana bo'yicha qidirish")
async def start_search_date(message: Message, state: FSMContext):
  if not is_admin(message.from_user.id):
    return
  await message.answer(
      "Qaysi sanadagi davomatni ko'rmoqchisiz?\nFormat: `YYYY-MM-DD`:",
      parse_mode="Markdown",
  )
  await state.set_state(SearchStates.waiting_for_query)
  await state.update_data(search_type="date")


@router.message(F.text == "👤 Ism / Familiya bo'yicha")
async def start_search_name(message: Message, state: FSMContext):
  if not is_admin(message.from_user.id):
    return
  await message.answer(
      "Qidirilayotgan talabaning **Ismi** yoki **Familiyasini** kiriting:",
      parse_mode="Markdown",
  )
  await state.set_state(SearchStates.waiting_for_query)
  await state.update_data(search_type="name")


@router.message(F.text == "📅 Oylik davomat hisoboti")
async def start_monthly_report(message: Message, state: FSMContext):
  if not is_admin(message.from_user.id):
    return
  await message.answer(
      "Qaysi oy uchun hisobot kerak?\nFormat: `YYYY-MM`:",
      parse_mode="Markdown",
  )
  await state.set_state(MonthlyReportStates.waiting_for_month)


@router.message(MonthlyReportStates.waiting_for_month)
async def process_monthly_report(message: Message, state: FSMContext):
  month_query = message.text.strip()
  conn = sqlite3.connect("dorm_bot.db")
  cursor = conn.cursor()
  cursor.execute(
      """
        SELECT s.first_name, s.last_name, s.room, COUNT(a.id) as days_count
        FROM students s
        LEFT JOIN attendance a ON s.telegram_id = a.student_id AND a.date LIKE ? AND a.status LIKE '%Kelgan%'
        GROUP BY s.telegram_id
        ORDER BY days_count DESC
    """,
      (f"{month_query}%",),
  )
  records = cursor.fetchall()
  conn.close()

  if not records:
    await message.answer(
        f"⚠️ `{month_query}` oyi uchun ma'lumot topilmadi.",
        parse_mode="Markdown",
        reply_markup=main_menu(message.from_user.id),
    )
  else:
    text = f"📅 **{month_query} oyi uchun umumiy davomat hisoboti:**\n\n"
    for r in records:
      text += f"🏠 {r[2]} | {r[0]} {r[1]} — **{r[3]} kun** qatnashgan\n"
    await message.answer(
        text, parse_mode="Markdown", reply_markup=main_menu(message.from_user.id)
    )
  await state.clear()


@router.message(SearchStates.waiting_for_query)
async def process_search_query(message: Message, state: FSMContext):
  user_data = await state.get_data()
  search_type = user_data.get("search_type")
  query_text = message.text.strip()

  conn = sqlite3.connect("dorm_bot.db")
  cursor = conn.cursor()

  if search_type == "date":
    cursor.execute(
        "SELECT s.first_name, s.last_name, s.room, a.time, a.status FROM"
        " attendance a JOIN students s ON a.student_id = s.telegram_id WHERE"
        " a.date = ?",
        (query_text,),
    )
    records = cursor.fetchall()
    conn.close()

    if not records:
      await message.answer(
          f"⚠️ `{query_text}` sanasi uchun davomat topilmadi.",
          parse_mode="Markdown",
          reply_markup=main_menu(message.from_user.id),
      )
    else:
      text = f"📊 **{query_text} sanasidagi davomat:**\n\n"
      for r in records:
        text += f"🏠 {r[2]} | {r[0]} {r[1]} — {r[3]} ({r[4]})\n"
      await message.answer(
          text,
          parse_mode="Markdown",
          reply_markup=main_menu(message.from_user.id),
      )

  elif search_type == "name":
    like_pattern = f"%{query_text}%"
    cursor.execute(
        """
            SELECT s.first_name, s.last_name, s.room, a.date, a.time, a.status 
            FROM attendance a 
            JOIN students s ON a.student_id = s.telegram_id 
            WHERE s.first_name LIKE ? OR s.last_name LIKE ?
            ORDER BY a.date DESC LIMIT 20
        """,
        (like_pattern, like_pattern),
    )
    records = cursor.fetchall()
    conn.close()

    if not records:
      await message.answer(
          f"⚠️ `{query_text}` bo'yicha hech qanday davomat tarixi topilmadi.",
          parse_mode="Markdown",
          reply_markup=main_menu(message.from_user.id),
      )
    else:
      text = (
          f"👤 **'{query_text}' bo'yicha qidiruv natijalari (Oxirgi 20ta):**\n\n"
      )
      for r in records:
        text += (
            f"📅 {r[3]} | ⏰ {r[4]} | 🏠 {r[2]} | {r[0]} {r[1]} — {r[5]}\n"
        )
      await message.answer(
          text,
          parse_mode="Markdown",
          reply_markup=main_menu(message.from_user.id),
      )

  await state.clear()


# --- RUNNER ---
async def main():
  dp.include_router(router)
  await bot.delete_webhook(drop_pending_updates=True)
  asyncio.create_task(clean_expired_qr_loop())
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())

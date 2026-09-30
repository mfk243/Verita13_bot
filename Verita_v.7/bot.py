from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BotCommand, Message, PreCheckoutQuery, LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, BotCommandScopeChat
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PROVIDER_TOKEN = os.getenv("PROVIDER_TOKEN", "")
PAYMENTS_ENABLED = False  # Сейчас используются только ручные переводы.
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", os.getenv("ADMIN_ID", ""))
ADMIN_IDS = {int(x.strip()) for x in ADMIN_IDS_RAW.replace(";", ",").split(",") if x.strip().isdigit()}
CURRENCY = os.getenv("CURRENCY", "RUB")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("DB_PATH", "bot.db")
if not os.path.isabs(DB_PATH):
    DB_PATH = os.path.join(BASE_DIR, DB_PATH)
RESERVATION_MINUTES = int(os.getenv("RESERVATION_MINUTES", "15"))
MANUAL_RESERVATION_HOURS = int(os.getenv("MANUAL_RESERVATION_HOURS", "24"))
MANUAL_PAYMENT_TEXT = os.getenv(
    "MANUAL_PAYMENT_TEXT",
    "💳 <b>Оплата</b>\n"
    "Переведите <b>{price}</b>\n"
    "Телефон: 89265505488\n"
    "Банк: ВТБ\n"
    "Комментарий: <code>{order_id}</code>\n\n"
    "После перевода нажмите «💳 Я оплатил(а)». Сохраните чек."
)

PAYMENT_HELP_TEXT = os.getenv(
    "PAYMENT_HELP_TEXT",
    "Если возникла ошибка при оплате или переводе, обратитесь к организатору: @katerinaa_denisovna"
)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в .env")
if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS не задан в .env")

router = Router()

TARIFFS = {
    "book_single": {"name": "Книжная беседа — билет на одного участника", "short": "Билет на одного", "default_price": 75000},
    "book_11": {"name": "Книжная беседа — акция «1+1»", "short": "Акция «1+1»", "default_price": 111100},
    "book_sub": {"name": "Книжная беседа — абонемент на 3 занятия", "short": "Абонемент на 3 занятия", "default_price": 170000},
    "yoga_single": {"name": "Йога — билет на одного участника", "short": "Билет на одного", "default_price": 75000},
    "yoga_11": {"name": "Йога — акция «1+1»", "short": "Акция «1+1»", "default_price": 111100},
    "yoga_sub": {"name": "Йога — абонемент на 3 занятия", "short": "Абонемент на 3 занятия", "default_price": 170000},
    "both_single": {"name": "Йога + книжная беседа — билет на одного участника", "short": "Билет на одного", "default_price": 150000},
    "both_11": {"name": "Йога + книжная беседа — акция «1+1»", "short": "Акция «1+1»", "default_price": 111100},
    "both_sub": {"name": "Йога + книжная беседа — абонемент на 3 занятия", "short": "Абонемент на 3 занятия", "default_price": 300000},
}

TARIFF_GROUPS = {
    "book": ("📚 Книжная беседа", ["book_single", "book_11", "book_sub"]),
    "yoga": ("🧘 Йога", ["yoga_single", "yoga_11", "yoga_sub"]),
    "both": ("📚🧘 Йога + книжная беседа", ["both_single", "both_11", "both_sub"]),
}

ABOUT_TEXT = """Мы — команда студентов ВШЭ разных
направлений, которые любят хорошую
литературу, осознанность и глубокое,
тёплое общение.

Мы много обсуждали, как в большом
городе порой не хватает спокойного,
безопасного пространства, где можно
делиться мыслями, слышать друг друга
и просто быть собой.

Так появился проект Верита —
уютные оффлайн-встречи книжного
клуба, вдохновлённые идеями из работ
современных психологов.

Наша цель — собирать ребят, которым
не хватает глубокого и честного
общения.
Без терапии, без «лечения» — просто
место, где можно расслабиться,
обменяться мыслями, услышать что-то
новое и, возможно, чуть лучше понять
себя.

Формат: 
-Оффлайн
-1.5-2 часа
-Небольшая компания
-Комфортная атмосфера

Если возникли вопросы или проблемы, напиши:
@katerinaa_denisovna - вопросы по 
организации мероприятия"""

START_TEXT = """Привет! 👀
Это Верита бот. Через меня можно узнать о нашей команде и купить билеты на наше мероприятие.

Здесь ты можешь:
- Узнать детали о ближайшем мероприятии
- Купить билет
- Применить промокод

Если возникли проблемы с оплатой — /buymanual"""

MAIN_KB = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="О нас",
                callback_data="info"
            )
        ],
        [
            InlineKeyboardButton(
                text="Купить билет",
                callback_data="buy"
            )
        ],
        [
            InlineKeyboardButton(
                text="Применить промокод",
                callback_data="promo"
            )
        ]
    ]
)
INFO_KB = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Купить билет",
                callback_data="buy"
            )
        ],
        [
            InlineKeyboardButton(
                text="Применить промокод",
                callback_data="promo"
            )
        ]
    ]
)
PROMO_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Отмена")],
    ],
    resize_keyboard=True,
)

class PromoState(StatesGroup):
    waiting_for_code = State()


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def money_to_cents(value: str) -> int:
    amount = Decimal(value.replace(",", ".")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount < 0:
        raise ValueError("Цена не может быть отрицательной")
    return int(amount * 100)


def cents_to_money(cents: int) -> str:
    return f"{Decimal(cents) / Decimal(100):.2f} ₽" if CURRENCY == "RUB" else f"{Decimal(cents) / Decimal(100):.2f} {CURRENCY}"


class Database:
    def __init__(self, path: str):
        self.path = path

    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA foreign_keys=ON")
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS promo_codes (
                    code TEXT PRIMARY KEY,
                    discount_percent INTEGER NOT NULL,
                    max_uses INTEGER NOT NULL DEFAULT 0,
                    uses INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    status TEXT NOT NULL,
                    original_price INTEGER NOT NULL,
                    final_price INTEGER NOT NULL,
                    promo_code TEXT,
                    reserved_until TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    paid_at TEXT,
                    telegram_payment_charge_id TEXT UNIQUE,
                    payment_reported INTEGER NOT NULL DEFAULT 0,
                    tariff_code TEXT,
                    tariff_name TEXT
                );
            """)
            # Миграция для уже существующей bot.db. Старые базы были созданы
            # до появления ручного подтверждения оплаты, поэтому в них может
            # отсутствовать payment_reported.
            cursor = await db.execute("PRAGMA table_info(orders)")
            columns = {row[1] for row in await cursor.fetchall()}
            if "payment_reported" not in columns:
                await db.execute(
                    "ALTER TABLE orders ADD COLUMN payment_reported INTEGER NOT NULL DEFAULT 0"
                )
            if "tariff_code" not in columns:
                await db.execute("ALTER TABLE orders ADD COLUMN tariff_code TEXT")
            if "tariff_name" not in columns:
                await db.execute("ALTER TABLE orders ADD COLUMN tariff_name TEXT")

            await db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('tickets_left', '50')")
            await db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('price_cents', '75000')")
            for code, tariff in TARIFFS.items():
                await db.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                    (f"tariff_{code}", str(tariff["default_price"])),
                )
            await db.commit()

    async def cleanup_expired(self):
        current = iso(now())
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT id FROM orders WHERE status='reserved' AND reserved_until < ?",
                (current,),
            )
            rows = await cursor.fetchall()
            if rows:
                await db.execute(
                    "UPDATE settings SET value = CAST(value AS INTEGER) + ? WHERE key='tickets_left'",
                    (len(rows),),
                )
                await db.execute(
                    "UPDATE orders SET status='expired' WHERE status='reserved' AND reserved_until < ?",
                    (current,),
                )
            await db.commit()

    async def get_setting(self, key: str) -> str:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
            row = await cursor.fetchone()
            if not row:
                raise KeyError(key)
            return row[0]

    async def set_setting(self, key: str, value: str):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            await db.commit()

    async def get_stock(self) -> int:
        await self.cleanup_expired()
        return int(await self.get_setting("tickets_left"))

    async def get_price(self) -> int:
        return int(await self.get_setting("price_cents"))

    async def get_tariff_price(self, tariff_code: str) -> int:
        return int(await self.get_setting(f"tariff_{tariff_code}"))

    async def set_tariff_price(self, tariff_code: str, price_cents: int):
        await self.set_setting(f"tariff_{tariff_code}", str(price_cents))

    async def reserve_order(
        self,
        user_id: int,
        username: Optional[str],
        tariff_code: str,
        tariff_name: str,
        original_price: int,
        final_price: int,
        promo_code: Optional[str],
    ):
        current = now()
        expires = current + timedelta(minutes=RESERVATION_MINUTES)
        order_id = uuid.uuid4().hex[:16]

        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")

            # Free expired reservations before taking a new one.
            cursor = await db.execute(
                "SELECT id FROM orders WHERE status='reserved' AND reserved_until < ?",
                (iso(current),),
            )
            expired = await cursor.fetchall()
            if expired:
                await db.execute(
                    "UPDATE settings SET value = CAST(value AS INTEGER) + ? WHERE key='tickets_left'",
                    (len(expired),),
                )
                for expired_order in expired:
                    await db.execute(
                        "UPDATE promo_codes SET uses=CASE WHEN uses>0 THEN uses-1 ELSE 0 END "
                        "WHERE code=(SELECT promo_code FROM orders WHERE id=? AND promo_code IS NOT NULL)",
                        (expired_order[0],),
                    )
                await db.execute(
                    "UPDATE orders SET status='expired' WHERE status='reserved' AND reserved_until < ?",
                    (iso(current),),
                )

            cursor = await db.execute("SELECT value FROM settings WHERE key='tickets_left'")
            row = await cursor.fetchone()
            tickets_left = int(row[0])
            if tickets_left <= 0:
                await db.rollback()
                return None

            # Reserve one promo use together with the ticket. This prevents two
            # simultaneous buyers from both passing a limited promo code.
            if promo_code:
                cursor = await db.execute(
                    "SELECT discount_percent, max_uses, uses, active FROM promo_codes WHERE code=?",
                    (promo_code.upper(),),
                )
                promo = await cursor.fetchone()
                if not promo or not promo[3] or (promo[1] > 0 and promo[2] >= promo[1]):
                    await db.rollback()
                    return None
                await db.execute("UPDATE promo_codes SET uses=uses+1 WHERE code=?", (promo_code.upper(),))

            await db.execute(
                "UPDATE settings SET value=? WHERE key='tickets_left'",
                (str(tickets_left - 1),),
            )

            await db.execute(
                """INSERT INTO orders
                   (id,user_id,username,status,original_price,final_price,promo_code,
                    reserved_until,created_at,tariff_code,tariff_name)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    order_id, user_id, username, "reserved", original_price, final_price,
                    promo_code.upper() if promo_code else None, iso(expires), iso(current),
                    tariff_code, tariff_name,
                ),
            )
            await db.commit()
            return order_id

    async def get_order(self, order_id: str):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM orders WHERE id=?", (order_id,))
            return await cursor.fetchone()

    async def cancel_reservation(self, order_id: str):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT status FROM orders WHERE id=?", (order_id,)
            )
            row = await cursor.fetchone()
            if row and row[0] == "reserved":
                await db.execute("UPDATE orders SET status='cancelled' WHERE id=?", (order_id,))
                await db.execute(
                    "UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key='tickets_left'"
                )
                await db.execute(
                    "UPDATE promo_codes SET uses=CASE WHEN uses>0 THEN uses-1 ELSE 0 END "
                    "WHERE code=(SELECT promo_code FROM orders WHERE id=? AND promo_code IS NOT NULL)",
                    (order_id,),
                )
            await db.commit()

    async def promo(self, code: str):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM promo_codes WHERE code=? AND active=1",
                (code.upper(),),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            if row["max_uses"] > 0 and row["uses"] >= row["max_uses"]:
                return None
            return row

    async def report_manual_payment(self, order_id: str, user_id: int):
        """Фиксирует нажатие «Я оплатил» и не даёт отправить админам дубликаты."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute("SELECT * FROM orders WHERE id=?", (order_id,))
            order = await cursor.fetchone()
            if not order:
                await db.rollback()
                return None, "not_found"
            # Обычный пользователь может сообщить об оплате только своего заказа.
            # Администратору разрешаем это и для чужого заказа — это удобно для
            # проверки/тестирования бота и не даёт доступа к подтверждению оплаты,
            # которое всё равно отдельно защищено проверкой ADMIN_IDS.
            if order["user_id"] != user_id and user_id not in ADMIN_IDS:
                await db.rollback()
                return None, "forbidden"
            if order["status"] != "reserved":
                await db.rollback()
                return order, order["status"]
            if datetime.fromisoformat(order["reserved_until"]) < now():
                await db.execute("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key='tickets_left'")
                if order["promo_code"]:
                    await db.execute("UPDATE promo_codes SET uses=CASE WHEN uses>0 THEN uses-1 ELSE 0 END WHERE code=?", (order["promo_code"],))
                await db.execute("UPDATE orders SET status='expired' WHERE id=?", (order_id,))
                await db.commit()
                return None, "expired"
            if order["payment_reported"]:
                await db.commit()
                return order, "already_reported"
            await db.execute("UPDATE orders SET payment_reported=1 WHERE id=?", (order_id,))
            await db.commit()
            return order, "reported"

    async def mark_manual_paid(self, order_id: str):
        """Подтверждает ручную оплату администратором."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM orders WHERE id=?", (order_id,))
            order = await cursor.fetchone()
            if not order:
                await db.rollback()
                return None, "not_found"
            if order["status"] == "paid":
                await db.commit()
                return order, "already_paid"
            if order["status"] != "reserved":
                await db.rollback()
                return None, "not_reserved"

            # При ручной оплате срок резерва значительно длиннее обычного
            # автоматического платежа. Если он всё же истёк, билет освобождаем.
            if datetime.fromisoformat(order["reserved_until"]) < now():
                await db.execute("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key='tickets_left'")
                if order["promo_code"]:
                    await db.execute(
                        "UPDATE promo_codes SET uses=CASE WHEN uses>0 THEN uses-1 ELSE 0 END WHERE code=?",
                        (order["promo_code"],),
                    )
                await db.execute("UPDATE orders SET status='expired' WHERE id=?", (order_id,))
                await db.commit()
                return None, "expired"

            await db.execute(
                "UPDATE orders SET status='paid', paid_at=?, telegram_payment_charge_id=? WHERE id=?",
                (iso(now()), f"MANUAL_{order_id}", order_id),
            )
            await db.commit()
            return order, "paid"

    async def mark_test_paid(self, order_id: str):
        """Завершает заказ без реальной оплаты в режиме разработки."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM orders WHERE id=?", (order_id,))
            order = await cursor.fetchone()
            if not order:
                await db.rollback()
                return None, "not_found"
            if order["status"] == "paid":
                await db.commit()
                return order, "already_paid"
            if order["status"] != "reserved":
                await db.rollback()
                return None, "not_reserved"
            if datetime.fromisoformat(order["reserved_until"]) < now():
                await db.execute("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key='tickets_left'")
                if order["promo_code"]:
                    await db.execute(
                        "UPDATE promo_codes SET uses=CASE WHEN uses>0 THEN uses-1 ELSE 0 END WHERE code=?",
                        (order["promo_code"],),
                    )
                await db.execute("UPDATE orders SET status='expired' WHERE id=?", (order_id,))
                await db.commit()
                return None, "expired"

            await db.execute(
                "UPDATE orders SET status='paid', paid_at=?, telegram_payment_charge_id=? WHERE id=?",
                (iso(now()), f"TEST_MODE_{order_id}", order_id),
            )
            await db.commit()
            return order, "paid"

    async def mark_paid(self, order_id: str, charge_id: str):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM orders WHERE id=?", (order_id,))
            order = await cursor.fetchone()
            if not order:
                await db.rollback()
                return None, "not_found"
            if order["status"] == "paid":
                await db.commit()
                return order, "already_paid"
            if order["status"] != "reserved":
                await db.rollback()
                return None, "not_reserved"
            if datetime.fromisoformat(order["reserved_until"]) < now():
                await db.execute("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key='tickets_left'")
                await db.execute("UPDATE orders SET status='expired' WHERE id=?", (order_id,))
                await db.commit()
                return None, "expired"

            await db.execute(
                "UPDATE orders SET status='paid', paid_at=?, telegram_payment_charge_id=? WHERE id=?",
                (iso(now()), charge_id, order_id),
            )
            await db.commit()
            return order, "paid"

    async def stats(self):
        await self.cleanup_expired()
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT value FROM settings WHERE key='tickets_left'")
            left = int((await cur.fetchone())[0])
            cur = await db.execute("SELECT COUNT(*) FROM orders WHERE status='paid'")
            paid = (await cur.fetchone())[0]
            cur = await db.execute("SELECT COUNT(*) FROM orders WHERE status='reserved'")
            reserved = (await cur.fetchone())[0]
            cur = await db.execute("SELECT value FROM settings WHERE key='price_cents'")
            price = int((await cur.fetchone())[0])
            return left, reserved, paid, price

    async def add_promo(self, code: str, discount: int, max_uses: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO promo_codes(code,discount_percent,max_uses) VALUES(?,?,?) "
                "ON CONFLICT(code) DO UPDATE SET discount_percent=excluded.discount_percent, max_uses=excluded.max_uses, active=1",
                (code.upper(), discount, max_uses),
            )
            await db.commit()


db = Database(DB_PATH)


def is_admin(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id in ADMIN_IDS)


def admin_payment_kb(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data=f"confirm:{order_id}"),
        InlineKeyboardButton(text="❌ Отменить заказ", callback_data=f"cancelorder:{order_id}"),
    ]])


def tariff_groups_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=TARIFF_GROUPS["book"][0], callback_data="tariffgroup:book")],
        [InlineKeyboardButton(text=TARIFF_GROUPS["yoga"][0], callback_data="tariffgroup:yoga")],
        [InlineKeyboardButton(text=TARIFF_GROUPS["both"][0], callback_data="tariffgroup:both")],
    ])


async def show_tariff_groups(message: Message, promo_code: Optional[str] = None):
    text = "Выберите направление:"
    if promo_code:
        text = f"Промокод <b>{promo_code}</b> принят.\n\nВыберите направление:"
    await message.answer(text, parse_mode="HTML", reply_markup=tariff_groups_keyboard())


async def show_tariff_options(message: Message, group_code: str, promo_code: Optional[str] = None):
    title, codes = TARIFF_GROUPS[group_code]
    lines = [f"<b>{title}</b>", ""]
    buttons = []
    for code in codes:
        tariff = TARIFFS[code]
        price = await db.get_tariff_price(code)
        lines.append(f"• {tariff['short']}: <b>{cents_to_money(price)}</b>")
        buttons.append([InlineKeyboardButton(
            text=f"{tariff['short']} — {cents_to_money(price)}",
            callback_data=f"tariff:{code}"
        )])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="tariffs")])
    if promo_code:
        lines.extend(["", f"Скидка по промокоду <b>{promo_code}</b> будет применена после выбора тарифа."])
    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


async def create_manual_order(
    message: Message,
    tariff_code: str,
    promo_code: Optional[str] = None,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
):
    if tariff_code not in TARIFFS:
        await message.answer("Не удалось определить тариф. Попробуйте ещё раз.")
        return

    await db.cleanup_expired()
    if await db.get_stock() <= 0:
        await message.answer("К сожалению, все билеты уже закончились. 🥲")
        return

    original = await db.get_tariff_price(tariff_code)
    final = original
    if promo_code:
        promo = await db.promo(promo_code)
        if not promo:
            await message.answer("Этот промокод недействителен или лимит его использований исчерпан.")
            return
        final = original * (100 - promo["discount_percent"]) // 100

    tariff_name = TARIFFS[tariff_code]["name"]

    global RESERVATION_MINUTES
    old_reservation_minutes = RESERVATION_MINUTES
    RESERVATION_MINUTES = MANUAL_RESERVATION_HOURS * 60
    try:
        order_id = await db.reserve_order(
            user_id=user_id if user_id is not None else message.from_user.id,
            username=username if username is not None else message.from_user.username,
            tariff_code=tariff_code,
            tariff_name=tariff_name,
            original_price=original,
            final_price=final,
            promo_code=promo_code.upper() if promo_code else None,
        )
    finally:
        RESERVATION_MINUTES = old_reservation_minutes

    if not order_id:
        await message.answer("Похоже, последний билет только что забрали. Попробуйте ещё раз.")
        return

    payment_text = MANUAL_PAYMENT_TEXT.format(order_id=order_id, price=cents_to_money(final))
    promo_text = f"\nПрименена скидка по промокоду {promo_code.upper()}." if promo_code else ""
    payment_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💳 Я оплатил(а)", callback_data=f"paid:{order_id}")
    ]])

    await message.answer(
        "🎟 <b>Заказ создан</b>\n"
        f"Тариф: {tariff_name}\n"
        f"Заказ: <code>{order_id}</code>\n"
        f"К оплате: <b>{cents_to_money(final)}</b>{promo_text}\n\n"
        + payment_text,
        parse_mode="HTML",
        reply_markup=payment_kb,
    )


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(START_TEXT, reply_markup=MAIN_KB)


@router.callback_query(F.data == "info")
async def info_callback(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(ABOUT_TEXT, reply_markup=INFO_KB)


@router.callback_query(F.data == "buy")
async def buy_callback(callback: CallbackQuery):
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await show_tariff_groups(callback.message)


@router.callback_query(F.data == "tariffs")
async def tariffs_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await show_tariff_groups(callback.message, data.get("promo_code"))


@router.callback_query(F.data.startswith("tariffgroup:"))
async def tariff_group_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    group = callback.data.split(":", 1)[1]
    if group not in TARIFF_GROUPS:
        await callback.message.answer("Раздел не найден.")
        return
    data = await state.get_data()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await show_tariff_options(callback.message, group, data.get("promo_code"))


@router.callback_query(F.data.startswith("tariff:"))
async def tariff_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    tariff_code = callback.data.split(":", 1)[1]
    data = await state.get_data()
    promo_code = data.get("promo_code")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await state.clear()
    await create_manual_order(
        callback.message,
        tariff_code,
        promo_code,
        user_id=callback.from_user.id,
        username=callback.from_user.username,
    )


@router.callback_query(F.data == "promo")
async def promo_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await state.set_state(PromoState.waiting_for_code)
    await callback.message.answer(
        "Введите промокод или нажмите «Отмена»",
        reply_markup=PROMO_KB,)


@router.message(Command("info"))
@router.message(F.text == "О нас")
async def info(message: Message):
    await message.answer(ABOUT_TEXT)


@router.message(Command("buy"))
@router.message(F.text == "Купить билет")
async def buy(message: Message, state: FSMContext):
    await state.clear()
    await show_tariff_groups(message)


@router.message(Command("promo"))
@router.message(F.text == "Применить промокод")
async def promo_start(message: Message, state: FSMContext):
    await state.set_state(PromoState.waiting_for_code)
    await message.answer(
        "Введите промокод или нажмите «Отмена»",
        reply_markup=PROMO_KB,)


@router.message(Command("cancel"), PromoState.waiting_for_code)
@router.message(PromoState.waiting_for_code, F.text == "Отмена")
async def promo_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Ввод промокода отменён",
        reply_markup=MAIN_KB,
    )

@router.message(PromoState.waiting_for_code)
async def promo_entered(message: Message, state: FSMContext):
    code = (message.text or "").strip().upper()
    promo = await db.promo(code)
    if not promo:
        await message.answer(
            "Не получилось найти такой промокод. Проверьте написание и попробуйте ещё раз.",
            reply_markup=PROMO_KB,
        )
        return

    await state.update_data(promo_code=code)
    await show_tariff_groups(message, code)


@router.message(Command("buymanual"))
async def buy_manual(message: Message):
    await message.answer(PAYMENT_HELP_TEXT)


@router.callback_query(F.data.startswith("paid:"))
async def paid_callback(callback: CallbackQuery):
    await callback.answer()
    order_id = callback.data.split(":", 1)[1]
    order, status = await db.report_manual_payment(order_id, callback.from_user.id)

    if status == "not_found":
        await callback.message.answer("Заказ с таким номером не найден.")
        return
    if status == "forbidden":
        await callback.message.answer("Этот заказ принадлежит другому пользователю.")
        return
    if status == "expired":
        await callback.message.answer("Срок резерва истёк. Этот заказ больше нельзя оплатить.")
        return
    if status == "paid":
        await callback.message.answer("Этот заказ уже оплачен и подтверждён. 💗")
        return
    if status == "cancelled":
        await callback.message.answer("Этот заказ уже отменён.")
        return
    if status == "already_reported":
        await callback.message.answer("Вы уже сообщили об оплате. Организаторы проверят её и подтвердят билет.")
        return

    username = f"@{order['username']}" if order["username"] else "без username"
    admin_text = (
        "🔔 <b>Пользователь сообщил об оплате</b>\n\n"
        f"Заказ: <code>{order_id}</code>\n"
        f"Тариф: {order['tariff_name'] or 'старый заказ'}\n"
        f"Сумма: <b>{cents_to_money(order['final_price'])}</b>\n"
        f"Пользователь: {username}\n"
        f"Telegram ID: <code>{order['user_id']}</code>"
    )
    sent = 0
    for admin_id in ADMIN_IDS:
        try:
            await callback.bot.send_message(admin_id, admin_text, parse_mode="HTML", reply_markup=admin_payment_kb(order_id))
            sent += 1
        except Exception:
            logging.exception("Не удалось уведомить администратора %s", admin_id)

    if sent:
        await callback.message.answer("✅ Сообщение об оплате отправлено организаторам. После проверки вы получите подтверждение билета.")
    else:
        await callback.message.answer("Сообщение об оплате отмечено, но организаторам не удалось его доставить. Свяжитесь с ними напрямую.")


@router.callback_query(F.data.startswith("confirm:"))
async def confirm_callback(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Только для организаторов", show_alert=True)
        return
    await callback.answer()
    order_id = callback.data.split(":", 1)[1]
    await confirm_order_logic(callback.message, order_id)


@router.callback_query(F.data.startswith("cancelorder:"))
async def cancel_order_callback(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Только для организаторов", show_alert=True)
        return
    await callback.answer()
    order_id = callback.data.split(":", 1)[1]
    await cancel_order_logic(callback.message, order_id)


async def confirm_order_logic(message: Message, order_id: str):
    order, status = await db.mark_manual_paid(order_id)

    if status == "not_found":
        await message.answer("Заказ с таким номером не найден.")
        return
    if status == "already_paid":
        await message.answer("Этот заказ уже был подтверждён ранее.")
        return
    if status == "not_reserved":
        await message.answer("Этот заказ нельзя подтвердить: он уже отменён или истёк.")
        return
    if status == "expired":
        await message.answer("Срок резерва этого заказа истёк. Билет снова доступен для продажи.")
        return

    await message.answer(
        "✅ Оплата подтверждена.\n"
        f"Заказ: <code>{order_id}</code>\n"
        f"Сумма: <b>{cents_to_money(order['final_price'])}</b>",
        parse_mode="HTML",
    )

    try:
        await message.bot.send_message(
            order["user_id"],
            "🎉 Оплата подтверждена!\n\n"
            f"Ваш заказ на мероприятие «Верита» оплачен.\n"
            f"Тариф: {order['tariff_name'] or 'билет'}\n"
            f"Номер заказа: <code>{order_id}</code>\n"
            f"Сумма: <b>{cents_to_money(order['final_price'])}</b>\n\n"
            "Сохраните это сообщение — оно подтверждает покупку. До встречи! 💗",
            parse_mode="HTML",
        )
    except Exception:
        logging.exception("Не удалось отправить подтверждение пользователю %s", order["user_id"])
        await message.answer("Оплата отмечена, но не удалось отправить пользователю уведомление.")


@router.message(Command("confirm"))
async def confirm_order(message: Message):
    if not is_admin(message):
        await message.answer("Эта команда доступна только организаторам.")
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("Использование: /confirm НОМЕР_ЗАКАЗА")
        return
    await confirm_order_logic(message, parts[1].strip())


async def cancel_order_logic(message: Message, order_id: str):
    order = await db.get_order(order_id)
    if not order:
        await message.answer("Заказ с таким номером не найден.")
        return
    if order["status"] != "reserved":
        await message.answer(f"Заказ нельзя отменить: его статус — {order['status']}.")
        return

    await db.cancel_reservation(order_id)
    await message.answer(f"Заказ <code>{order_id}</code> отменён, билет возвращён в продажу.", parse_mode="HTML")
    try:
        await message.bot.send_message(
            order["user_id"],
            f"Заказ <code>{order_id}</code> отменён организатором. Если это ошибка, свяжитесь с организаторами.",
            parse_mode="HTML",
        )
    except Exception:
        logging.exception("Не удалось отправить сообщение об отмене пользователю %s", order["user_id"])


@router.message(Command("cancelorder"))
async def cancel_order(message: Message):
    if not is_admin(message):
        await message.answer("Эта команда доступна только организаторам.")
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("Использование: /cancelorder НОМЕР_ЗАКАЗА")
        return
    await cancel_order_logic(message, parts[1].strip())


@router.message(Command("stock"))
async def stock(message: Message):
    if not is_admin(message):
        await message.answer("Эта команда доступна только организаторам.")
        return
    left, reserved, paid, price = await db.stats()
    lines = [f"Билеты:\nОсталось: {left}\nВ резерве: {reserved}\nОплачено: {paid}", "", "<b>Цены:</b>"]
    for code, tariff in TARIFFS.items():
        tariff_price = await db.get_tariff_price(code)
        lines.append(f"• {tariff['short']}: {cents_to_money(tariff_price)}")
    lines.append("\nОплата: ручная, переводом по реквизитам организатора.")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("setstock"))
async def setstock(message: Message):
    if not is_admin(message):
        await message.answer("Эта команда доступна только организаторам.")
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: /setstock 50")
        return
    value = int(parts[1])
    await db.set_setting("tickets_left", str(value))
    await message.answer(f"Остаток билетов изменён: {value}.")


@router.message(Command("tariffs"))
async def tariffs_admin(message: Message):
    if not is_admin(message):
        await message.answer("Эта команда доступна только организаторам.")
        return

    lines = ["<b>💳 Тарифы и цены</b>", ""]
    for group_code, (title, codes) in TARIFF_GROUPS.items():
        lines.append(f"<b>{title}</b>")
        for code in codes:
            tariff = TARIFFS[code]
            price = await db.get_tariff_price(code)
            lines.append(
                f"• {tariff['short']} — <b>{cents_to_money(price)}</b>"
            )
            lines.append(f"  Код: <code>{code}</code>")
        lines.append("")

    lines.extend([
        "<b>Как изменить цену:</b>",
        "<code>/settariff КОД ЦЕНА</code>",
        "Например: <code>/settariff book_single 800</code>",
    ])
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("settariff"))
async def settariff(message: Message):
    if not is_admin(message):
        await message.answer("Эта команда доступна только организаторам.")
        return
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer("Использование: /settariff КОД ЦЕНА\nНапример: /settariff book_single 750")
        return
    code = parts[1].lower()
    if code not in TARIFFS:
        await message.answer("Неизвестный код тарифа. Посмотрите /tariffs.")
        return
    try:
        cents = money_to_cents(parts[2])
    except ValueError:
        await message.answer("Цена должна быть числом, например 750 или 1111.")
        return
    await db.set_tariff_price(code, cents)
    await message.answer(f"Цена изменена: {TARIFFS[code]['name']} — {cents_to_money(cents)}")


@router.message(Command("price"))
async def price_legacy(message: Message):
    if not is_admin(message):
        await message.answer("Эта команда доступна только организаторам.")
        return
    await message.answer("Теперь используется несколько тарифов. Откройте /tariffs.")


@router.message(Command("addpromo"))
async def addpromo(message: Message):
    if not is_admin(message):
        await message.answer("Эта команда доступна только организаторам.")
        return
    parts = (message.text or "").split()
    if len(parts) not in (3, 4):
        await message.answer("Использование: /addpromo КНИГА 20 100\n20 = скидка 20%, 100 = максимум использований. 0 = без лимита.")
        return
    code = parts[1].upper()
    try:
        discount = int(parts[2])
        max_uses = int(parts[3]) if len(parts) == 4 else 0
    except ValueError:
        await message.answer("Скидка и лимит должны быть целыми числами.")
        return
    if not 1 <= discount <= 99 or max_uses < 0:
        await message.answer("Скидка: от 1 до 99%. Лимит: 0 или больше.")
        return
    await db.add_promo(code, discount, max_uses)
    await message.answer(f"Промокод {code} создан: скидка {discount}%, лимит: {max_uses or 'без лимита'}.")


@router.message(Command("myid"))
async def myid(message: Message):
    await message.answer(f"Ваш Telegram ID: {message.from_user.id}")


@router.message(Command("admin"))
async def admin_help(message: Message):
    if not is_admin(message):
        await message.answer("Команда не найдена. Используйте /start.")
        return
    await message.answer(
        "Команды организатора:\n"
        "/stock — остаток, резервы и продажи\n"
        "/setstock 50 — установить остаток билетов\n"
        "/tariffs — показать все тарифы и цены\n"
        "/settariff КОД ЦЕНА — изменить цену тарифа\n"
        "/addpromo КНИГА 20 100 — создать/обновить промокод\n"
        "/confirm НОМЕР_ЗАКАЗА — подтвердить оплату\n"
        "/cancelorder НОМЕР_ЗАКАЗА — отменить заказ и вернуть билет\n"
        "/myid — показать свой Telegram ID\n"
        "/admin — эта справка\n\n"
        f"Администраторов подключено: {len(ADMIN_IDS)}"
    )


@router.message()
async def unknown(message: Message):
    await message.answer(
        "Я не совсем понял сообщение. Используйте кнопки ниже или команды /info, /buy, /promo.",
    )


async def main():
    logging.basicConfig(level=logging.INFO)
    await db.init()
    bot = Bot(BOT_TOKEN)
    user_commands = [
        BotCommand(command="start", description="Открыть главное меню"),
        BotCommand(command="info", description="О нас"),
        BotCommand(command="buy", description="Купить билет"),
        BotCommand(command="promo", description="Применить промокод"),
        BotCommand(command="buymanual", description="Куда обратиться при ошибке оплаты"),
    ]
    admin_commands = user_commands + [
        BotCommand(command="confirm", description="Подтвердить оплату"),
        BotCommand(command="cancelorder", description="Отменить заказ"),
        BotCommand(command="stock", description="Статистика и остаток"),
        BotCommand(command="setstock", description="Изменить остаток"),
        BotCommand(command="tariffs", description="Показать тарифы"),
        BotCommand(command="settariff", description="Изменить цену тарифа"),
        BotCommand(command="addpromo", description="Создать промокод"),
        BotCommand(command="myid", description="Показать свой Telegram ID"),
        BotCommand(command="admin", description="Справка организатора"),
    ]
    await bot.set_my_commands(user_commands)
    for admin_id in ADMIN_IDS:
        await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

def _append_edit_content_handler():
    @dp.message(MenuState.broadcast_edit_content_wait)
    @admin_required
    async def process_broadcast_edit_content(message: types.Message, state: FSMContext):
        data = await state.get_data()
        b_id = data.get("edit_broadcast_id") or data.get("manage_broadcast_id")
        if not b_id:
            await message.answer("ID рассылки потерян.")
            await state.clear()
            return
        new_text = message.text
        if not new_text:
            await message.answer("Нужно отправить текстовое сообщение.")
            return

        # Обновляем контент для будущих отправок
        await db.update_broadcast_text_content(b_id, new_text)

        # Пытаемся обновить уже отправленные сообщения
        messages = await db.get_broadcast_messages(b_id)
        updated = 0
        for chat_id, msg_id in messages:
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=new_text, disable_web_page_preview=True)
                updated += 1
            except Exception as e:
                logger.error(f"Не удалось изменить сообщение {msg_id} в {chat_id}: {e}")
        # Определяем статус рассылки
        row = await db.conn.execute("SELECT sent FROM broadcasts WHERE id = ?", (b_id,))
        r = await row.fetchone()
        is_sent = bool(r and r[0])
        if not is_sent:
            await message.answer("✅ Содержимое обновлено.", reply_markup=admin_reply_keyboard())
        else:
            await message.answer(f"✅ Содержимое обновлено в {updated} группах", reply_markup=admin_reply_keyboard())
        await state.clear()
import asyncio
import logging
from typing import List, Optional

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ---- Helpers ---- #

def now_msk_naive() -> datetime:
    """Возвращает текущее время в МСК без tzinfo (naive)"""
    return datetime.now(ZoneInfo("Europe/Moscow")).replace(tzinfo=None)

def to_msk_naive(dt: datetime) -> datetime:
    """Приводит произвольный datetime к МСК и делает его naive (без tzinfo)."""
    if dt.tzinfo is not None:
        return dt.astimezone(ZoneInfo("Europe/Moscow")).replace(tzinfo=None)
    return dt

def utc_str_to_msk_str(dt_str: str) -> str:
    """Парсит строку времени (UTC или без tz) и возвращает строку в МСК в формате DD.MM.YYYY HH:MM.

    Для записей SQLite CURRENT_TIMESTAMP (UTC) без tzinfo принудительно считаем UTC.
    """
    if not dt_str:
        return "-"
    try:
        # fromisoformat поддерживает и ' ' и 'T' как разделитель даты и времени
        dt = datetime.fromisoformat(dt_str)
    except Exception:
        # Последняя попытка: заменить пробел на 'T'
        try:
            dt = datetime.fromisoformat(dt_str.replace(" ", "T"))
        except Exception:
            return dt_str
    # Если tz отсутствует, считаем это UTC (как CURRENT_TIMESTAMP в SQLite)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    msk = dt.astimezone(ZoneInfo("Europe/Moscow"))
    return msk.strftime('%d.%m.%Y %H:%M')

def format_scheduled_str(scheduled_at_str: str) -> str:
    """Красиво форматирует scheduled_at, хранящийся как ISO-строка.
    Если есть tzinfo — приводим к МСК, иначе считаем, что это уже МСК (naive).
    """
    if not scheduled_at_str:
        return "не задано"
    try:
        dt = datetime.fromisoformat(scheduled_at_str)
    except Exception:
        return scheduled_at_str
    if dt.tzinfo is not None:
        dt = dt.astimezone(ZoneInfo("Europe/Moscow")).replace(tzinfo=None)
    # Считаем, что naive уже в МСК
    return dt.strftime('%d.%m.%Y %H:%M')

def extract_hours(user_text: str) -> Optional[int]:
    """Пытается извлечь число часов из текста: '2', '2ч', '2 часа', 'через 2 часа'.
    Возвращает None, если распознать как часы нельзя."""
    import re as _re
    text = user_text.strip().lower()
    m = _re.match(r"^(?:через\s*)?(\d{1,2})(?:\s*(?:ч|час|часа|часов))?\s*$", text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None

def extract_minutes(user_text: str) -> Optional[int]:
    """Извлекает количество минут: '40', '40 мин', '40 минут', 'через 40 минут'."""
    import re as _re
    text = user_text.strip().lower()
    m = _re.match(r"^(?:через\s*)?(\d{1,3})(?:\s*(?:м|мин|минута|минуты|минут))?\s*$", text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None
import dateparser
import re
import aiosqlite
from dotenv import load_dotenv

load_dotenv()

import asyncio
import logging
from typing import List, Optional

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, KeyboardButtonRequestChat
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from config import BOT_TOKEN, ADMIN_IDS, DATABASE_PATH
from database import Database





def parse_segment_instructions(text: str, available_segments: List[str]) -> dict:
    """Парсит инструкции в свободной форме для управления сегментами.
    
    Возвращает словарь с операциями:
    {
        'add': ['сегмент1', 'сегмент2'],
        'remove': ['сегмент3'],
        'errors': ['неизвестный_сегмент']
    }
    """
    text_lower = text.lower()
    result = {'add': [], 'remove': [], 'errors': []}
    
    # Ключевые слова для операций
    add_keywords = ['добав', 'включ', 'присое', '+', 'плюс', 'в ']
    remove_keywords = ['удал', 'убер', 'исключ', 'из ', '-', 'минус']
    
    # Находим все упоминания сегментов в тексте
    mentioned_segments = []
    for segment in available_segments:
        if segment.lower() in text_lower:
            mentioned_segments.append(segment)
    
    # Разбиваем текст на части по запятым и союзам
    parts = re.split(r'[,;]\s*|(?:\s+и\s+)', text_lower)
    
    for part in parts:
        part = part.strip()
        if not part:
            continue
            
        # Определяем операцию для этой части
        is_add = any(keyword in part for keyword in add_keywords)
        is_remove = any(keyword in part for keyword in remove_keywords)
        
        # Находим упомянутые в этой части сегменты
        part_segments = [seg for seg in mentioned_segments if seg.lower() in part]
        
        for segment in part_segments:
            if is_remove and not is_add:  # только удаление
                if segment not in result['remove']:
                    result['remove'].append(segment)
            elif is_add and not is_remove:  # только добавление
                if segment not in result['add']:
                    result['add'].append(segment)
            elif is_remove and is_add:  # неоднозначность
                # По умолчанию считаем добавлением, если не указано "из"
                if 'из ' + segment.lower() in part:
                    if segment not in result['remove']:
                        result['remove'].append(segment)
                else:
                    if segment not in result['add']:
                        result['add'].append(segment)
            else:  # нет явных операций, пытаемся угадать по контексту
                if 'из ' in part and segment.lower() in part:
                    if segment not in result['remove']:
                        result['remove'].append(segment)
                else:
                    if segment not in result['add']:
                        result['add'].append(segment)
    
    # Проверяем несуществующие сегменты
    all_mentioned = set()
    for word in text.split():
        word_clean = word.strip('.,!?;').lower()
        if word_clean not in [seg.lower() for seg in available_segments]:
            # Может быть это опечатка в названии сегмента?
            for seg in available_segments:
                if word_clean in seg.lower() or seg.lower() in word_clean:
                    break
            else:
                # Проверяем, похоже ли на название сегмента
                if len(word_clean) > 3 and not any(kw in word_clean for kw in 
                    ['добав', 'удал', 'включ', 'убер', 'минус', 'плюс']):
                    all_mentioned.add(word_clean)
    
    # Добавляем неопознанные слова как возможные ошибки
    for word in all_mentioned:
        if word not in [seg.lower() for seg in available_segments]:
            result['errors'].append(word)
    
    return result





















logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Отключаем DEBUG логи от Telethon
logging.getLogger('telethon').setLevel(logging.WARNING)
logging.getLogger('asyncio').setLevel(logging.WARNING)
logging.getLogger('aiosqlite').setLevel(logging.WARNING)

# Инициализация бота и диспетчера
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Инициализация БД
db = Database(DATABASE_PATH)

# ---- FSM ---- #
class BroadcastState(StatesGroup):
    waiting_for_message = State()
    waiting_for_list_choice = State()
    waiting_for_schedule_input = State()
    waiting_for_schedule_confirm = State()
    waiting_for_auto_delete = State()
    waiting_for_auto_delete_confirm = State()


class MenuState(StatesGroup):
    main = State()
    broadcast_wait_message = State()
    broadcast_choose_list = State()
    # --- new states for lists & groups ---
    list_create_wait_name = State()
    group_assign_select_group = State()
    group_assign_select_list = State()
    # --- lists menu ---
    lists_menu = State()
    list_delete_select_list = State()
    group_name_wait = State() # Added for manual group naming
    # --- group management ---
    group_move_select_group = State()
    group_move_select_list = State()
    group_delete_select_group = State()
    group_delete_confirm = State()
    group_add_select_group = State()
    group_add_select_list = State()
    # --- list viewing ---
    list_view_select_list = State()

    # управление рассылками
    broadcast_menu = State()
    broadcast_manage_show = State()
    broadcast_manage_edit_time = State()
    broadcast_edit_content_wait = State()

    # --- редактирование группы ---
    edit_search = State()
    edit_confirm = State()
    edit_actions = State()
    edit_add_segment = State()
    edit_remove_segment = State()

    # просмотр сегментов
    segment_view_select_list = State()

    # управление сегментами через ИИ
    edit_ai_input = State()
    edit_ai_confirm = State()

    # управление админами
    settings_menu = State()
    admin_management = State()
    admin_add_wait_user = State()
    admin_delete_select = State()
    admin_delete_confirm = State()
    admin_transfer_select = State()
    admin_transfer_confirm = State()




# ---- Вспомогательные функции ---- #

async def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором в базе данных"""
    return await db.is_admin(user_id)

async def is_super_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь супер-админом"""
    return await db.is_super_admin(user_id)


def admin_required(func):
    """Декоратор для проверки прав администратора и фильтра лишних аргументов."""

    import inspect
    sig = inspect.signature(func)
    allowed_params = set(sig.parameters.keys())

    async def wrapper(message: types.Message, *args, **kwargs):
        if not await is_admin(message.from_user.id):
            await message.answer("⛔️ У вас нет доступа к этой команде.")
            return
        # Оставляем только те kwargs, которые реально есть в целевой функции
        filtered_kwargs = {k: v for k, v in kwargs.items() if k in allowed_params}
        return await func(message, *args, **filtered_kwargs)

    return wrapper


# --- Утилита транслитерации RU → EN (упрощённая) --- #

RU2EN = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
})


def translit_ru(text: str) -> str:
    return text.lower().translate(RU2EN)


async def build_lists_keyboard() -> InlineKeyboardMarkup:
    lists: List[tuple] = await db.get_lists()
    if not lists:
        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Нет списков", callback_data="noop")]])
    keyboard = [
        [InlineKeyboardButton(text=name, callback_data=f"choose_list:{list_id}")]
        for list_id, name in lists
    ]
    keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ---- Отправка запланированной рассылки ---- #
async def send_broadcast_by_id(broadcast_id: int):
    """Отправляет рассылку во все группы и отмечает её как отправленную"""
    # Получаем данные рассылки
    cursor = await db.conn.execute(
        "SELECT list_id, source_chat_id, source_message_id FROM broadcasts WHERE id = ?",
        (broadcast_id,)
    )
    row = await cursor.fetchone()
    if not row:
        logging.error(f"Broadcast {broadcast_id} not found")
        return
    list_id, source_chat_id, source_message_id = row

    # Получаем все группы сегмента
    groups = await db.get_groups_in_list(list_id)
    sent = 0
    for chat_id in groups:
        try:
            sent_message = await bot.copy_message(chat_id, from_chat_id=source_chat_id, message_id=source_message_id)
            await db.record_broadcast_message(broadcast_id, chat_id, sent_message.message_id)
            sent += 1
        except Exception as e:
            logging.error(f"Не удалось отправить в {chat_id}: {e}")
    # Отмечаем как отправленную только если хоть куда-то ушло
    if sent > 0:
        await db.mark_broadcast_as_sent(broadcast_id)
    logging.info(f"Broadcast {broadcast_id} sent to {sent} groups")


async def broadcast_scheduler():
    """Фоновая задача, проверяющая и запускающая запланированные рассылки"""
    while True:
        try:
            now_msk = now_msk_naive()
            due = await db.get_due_broadcasts(now_msk)
            for row in due:
                b_id = row[0]
                await send_broadcast_by_id(b_id)
            # Автоудаление
            to_delete = await db.get_due_auto_deletions(now_msk)
            for (b_id,) in to_delete:
                # удаляем все сообщения рассылки
                messages = await db.get_broadcast_messages(b_id)
                for chat_id, msg_id in messages:
                    try:
                        await bot.delete_message(chat_id, msg_id)
                    except Exception as e:
                        logging.error(f"Auto delete failed for broadcast {b_id} in {chat_id}: {e}")
                await db.mark_broadcast_as_deleted(b_id)
        except Exception as e:
            logging.error(f"Scheduler error: {e}")
        await asyncio.sleep(30)


async def send_long_message_with_keyboard(message: types.Message, text: str, reply_markup: Optional[ReplyKeyboardMarkup] = None, chunk_size: int = 4000):
    """Отправляет длинный текст несколькими сообщениями, чтобы не превышать лимит Telegram.

    Первый фрагмент отправляется с переданной клавиатурой (если она есть),
    остальные уже без неё, чтобы не дублировать клавиатуру.
    """
    # If text already fits – просто отправляем
    if len(text) <= chunk_size:
        await message.answer(text, reply_markup=reply_markup)
        return

    # Разбиваем по строкам, чтобы не обрезать слова
    lines = text.split("\n")
    buffer = ""
    first_chunk = True
    for line in lines:
        # +1 учитывает перевод строки, который будет добавлен при соединении
        if len(buffer) + len(line) + 1 > chunk_size:
            await message.answer(buffer.rstrip(), reply_markup=reply_markup if first_chunk else None)
            first_chunk = False
            buffer = ""
        buffer += line + "\n"
    if buffer:
        await message.answer(buffer.rstrip(), reply_markup=reply_markup if first_chunk else None)


# ---- Команды администратора ---- #

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if await is_admin(message.from_user.id):
        await message.answer(
            "🏠 Добро пожаловать в админ-панель бота!\n\n"
            "Используйте кнопки ниже для управления ⬇️",
            reply_markup=admin_reply_keyboard()
        )
    else:
        await message.answer("Привет! Это бот для рассылки сообщений в группы.")


@dp.message(Command("myid"))
async def cmd_myid(message: types.Message):
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    if await is_admin(message.from_user.id):
        await message.answer(
            (
                "🔧 <b>Команды администратора:</b>\n\n"
                "/create_list &lt;название&gt; — создать список групп\n"
                "/lists — показать все сегменты\n"
                "/groups — показать все группы и их привязки\n"
                "/assign &lt;chat_id&gt; &lt;список&gt; — привязать группу к списку\n"
                "/broadcast — начать рассылку\n"
                "/delete_last — удалить последнюю рассылку\n"
                "/panel — панель управления с кнопками\n\n"
                "📋 <b>Как работать:</b>\n"
                "1. Добавьте бота в группы (он автоматически зарегистрируется)\n"
                "2. Используйте /groups чтобы увидеть все группы\n"
                "3. Привяжите группы к спискам через /assign\n"
                "4. Делайте рассылки через /panel или /broadcast"
            )
        )
    else:
        await message.answer("Привет! Это бот для рассылки сообщений в группы.")


@dp.message(Command("create_list"))
@admin_required
async def cmd_create_list(message: types.Message, command: CommandObject):
    if not command.args:
        await message.answer("Укажите название: /create_list &lt;название&gt;")
        return
    await db.create_list(command.args.strip())
    await message.answer(f"✅ Список <b>{command.args.strip()}</b> создан или уже существовал.")


@dp.message(Command("segments"))
@admin_required
async def cmd_lists(message: types.Message):
    lists = await db.get_lists()
    kb = ReplyKeyboardBuilder()
    kb.button(text="➕ Создать сегмент")
    kb.button(text="⬅️ Назад")
    kb.adjust(1)
    if not lists:
        await message.answer("Пока нет ни одного сегмента. Используйте ➕ Создать сегмент.", reply_markup=kb.as_markup(resize_keyboard=True))
        return
    text = "\n".join([f"<b>{list_id}</b> — {name}" for list_id, name in lists])
    await message.answer(f"📂 Сегменты групп:\n{text}", reply_markup=kb.as_markup(resize_keyboard=True))


@dp.message(Command("broadcast"))
@admin_required
async def cmd_broadcast(message: types.Message, state: FSMContext):
    await state.set_state(BroadcastState.waiting_for_message)
    await message.answer("Отправьте сообщение (текст, фото, видео, документ), которое нужно разослать.")


@dp.message(BroadcastState.waiting_for_message)
async def broadcast_save_message(message: types.Message, state: FSMContext):
    await state.update_data(source_message=message)
    keyboard = await build_lists_keyboard()
    await message.answer("Выберите список, куда отправить сообщение:", reply_markup=keyboard)
    await state.set_state(BroadcastState.waiting_for_list_choice)


@dp.callback_query(F.data.startswith("choose_list"))
async def process_list_choice(callback: types.CallbackQuery, state: FSMContext):
    list_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    source: types.Message = data.get("source_message")
    
    if not source:
        await callback.answer("Источник сообщения не найден", show_alert=True)
        return

    # Создаём запись о рассылке (пока без даты)
    broadcast_id = await db.record_broadcast(
        list_id=list_id,
        content_type=source.content_type,
        content=source.text or None,
        scheduled_at=None,
        source_chat_id=source.chat.id,
        source_message_id=source.message_id,
    )

    # Сохраняем в FSM
    await state.update_data(broadcast_id=broadcast_id)

    await callback.answer()
    await callback.message.answer(
        "Когда отправить рассылку? Укажите дату и время (по МСК).\n\n"
        "Примеры:\n"
        "— 13.08.2025 17:00\n"
        "— 13 августа 17:00\n"
        "— через 2 дня в 17:00\n"
        "— 5 вечера\n"
        "— сейчас\n"
        "— сегодня в 17:00\n"
    )
    await state.set_state(BroadcastState.waiting_for_schedule_input)


@dp.callback_query(F.data == "cancel")
async def cancel_callback(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_reply_markup()
    await callback.message.answer("⛔️ Рассылка отменена.")
    await callback.answer()


# ---- Шаг ввода времени ---- #
@dp.message(BroadcastState.waiting_for_schedule_input)
async def process_schedule_input(message: types.Message, state: FSMContext):
    text = message.text.strip().lower()
    # Обработка "сейчас"
    if text in ["сейчас", "now"]:
        scheduled_dt = datetime.now(ZoneInfo("Europe/Moscow"))
    else:
        scheduled_dt = dateparser.parse(
            text,
            languages=["ru"],
            settings={
                "RELATIVE_BASE": datetime.now(ZoneInfo("Europe/Moscow")),
                "TIMEZONE": "Europe/Moscow",
                "RETURN_AS_TIMEZONE_AWARE": False,
            },
        )
    if not scheduled_dt:
        await message.answer("Не удалось распознать дату/время. Попробуйте ещё раз.")
        return
    # Если дата без года и она уже прошла – добавляем год +1
    if scheduled_dt < now_msk_naive():
        # Оставляем как есть, пользователь возможно хочет прошлое время для немедленного запуска
        pass

    await state.update_data(scheduled_dt=scheduled_dt)

    # Подтверждение
    confirm_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data="schedule_confirm"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"),
            ]
        ]
    )
    await message.answer(
        f"Опубликовать рассылку {scheduled_dt.strftime('%d.%m.%Y %H:%M')} по МСК?",
        reply_markup=confirm_kb,
    )
    await state.set_state(BroadcastState.waiting_for_schedule_confirm)


# ---- Подтверждение времени ---- #
@dp.callback_query(F.data == "schedule_confirm")
async def confirm_schedule_callback(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    broadcast_id = data.get("broadcast_id")
    scheduled_dt: datetime = data.get("scheduled_dt")
    if not broadcast_id or not scheduled_dt:
        await callback.answer("Данные потеряны", show_alert=True)
        await state.clear()
        return
    # Получаем исходные данные для источника сообщения
    row = await db.conn.execute("SELECT source_chat_id, source_message_id FROM broadcasts WHERE id = ?", (broadcast_id,))
    src = await row.fetchone()
    if not src:
        await callback.answer("Рассылка не найдена", show_alert=True)
        await state.clear()
        return
    source_chat_id, source_message_id = src
    await db.set_broadcast_schedule(broadcast_id, scheduled_dt, source_chat_id, source_message_id)

    # Переходим к шагу автоудаления
    await state.update_data(broadcast_id=broadcast_id, scheduled_dt=scheduled_dt)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🚫 Не удалять автоматически", callback_data="auto_del_skip")]]
    )
    # Лимит считаем от времени публикации (фактического или планируемого)
    limit_dt = scheduled_dt + timedelta(hours=48)
    await callback.message.answer(
        "Через сколько часов удалить пост?\n" \
        "— до 48 часов (например: 1, 6, 24)\n" \
        f"— или укажите дату и время (МСК), не позже чем через 48 часов ({limit_dt.strftime('%d.%m.%Y %H:%M')})\n\n" \
        "Нажмите кнопку, если не нужно удалять автоматически.",
        reply_markup=kb,
    )
    await state.set_state(BroadcastState.waiting_for_auto_delete)
    await callback.answer()


@dp.message(BroadcastState.waiting_for_auto_delete)
async def process_auto_delete_input(message: types.Message, state: FSMContext):
    text = message.text.strip().lower()
    data = await state.get_data()
    scheduled_dt: datetime = data.get("scheduled_dt")
    broadcast_id = data.get("broadcast_id")
    if not broadcast_id:
        # Фолбэк: пробуем достать из manage_broadcast_id / edit_broadcast_id
        candidate_id = data.get("manage_broadcast_id") or data.get("edit_broadcast_id")
        if candidate_id:
            broadcast_id = candidate_id
            await state.update_data(broadcast_id=broadcast_id)
            logger.warning("process_auto_delete_input: восстановил broadcast_id из fallback, id=%s", broadcast_id)
        else:
            logger.error("process_auto_delete_input: broadcast_id отсутствует в FSM; data=%s", data)
            await message.answer("Данные потеряны (нет ID). Начните заново /broadcast")
            await state.clear()
            return
    # Если по какой-то причине scheduled_dt отсутствует в FSM, пробуем достать из БД
    if scheduled_dt is None:
        row = await db.conn.execute("SELECT scheduled_at FROM broadcasts WHERE id = ?", (broadcast_id,))
        r = await row.fetchone()
        if r and r[0]:
            try:
                dt = datetime.fromisoformat(r[0])
                if dt.tzinfo is not None:
                    dt = dt.astimezone(ZoneInfo("Europe/Moscow")).replace(tzinfo=None)
                scheduled_dt = dt
            except Exception:
                scheduled_dt = now_msk_naive()
        else:
            scheduled_dt = now_msk_naive()

    max_delta_hours = 48
    # Максимальное время автоудаления отталкивается от времени публикации
    max_deadline = scheduled_dt + timedelta(hours=max_delta_hours)

    auto_delete_dt = None
    hours = extract_hours(text)
    mins = extract_minutes(text)
    if hours is not None or mins is not None:
        total_minutes = (hours or 0) * 60 + (mins or 0)
        if total_minutes <= 0:
            await message.answer("Укажите положительное время. Попробуйте ещё раз или нажмите кнопку не удалять.")
            return
        if total_minutes > max_delta_hours * 60:
            await message.answer("Нельзя указывать больше 48 часов. Попробуйте ещё раз.")
            return
        auto_delete_dt = scheduled_dt + timedelta(minutes=total_minutes)
    else:
        # Относительные формулировки (например, "через час") должны считаться
        # от времени публикации, а не от текущего времени
        dt = dateparser.parse(
            text,
            languages=["ru"],
            settings={
                "RELATIVE_BASE": scheduled_dt or now_msk_naive(),
                "TIMEZONE": "Europe/Moscow",
                "RETURN_AS_TIMEZONE_AWARE": False,
            },
        )
        if not dt:
            await message.answer("Не удалось распознать время. Укажите часы/минуты (напр. '2 часа', '40 минут') или дату/время по МСК.")
            return
        if dt > max_deadline:
            await message.answer("Нельзя указывать больше 48 часов от времени публикации. Попробуйте ещё раз.")
            return
        auto_delete_dt = dt

    await state.update_data(auto_delete_dt=auto_delete_dt)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✅ Подтвердить", callback_data="auto_del_confirm"), InlineKeyboardButton(text="❌ Изменить", callback_data="auto_del_change")]]
    )
    await message.answer(
        f"Удалить пост {auto_delete_dt.strftime('%d.%m.%Y %H:%M')} (МСК)?",
        reply_markup=kb,
    )
    await state.set_state(BroadcastState.waiting_for_auto_delete_confirm)


@dp.callback_query(F.data == "auto_del_skip")
async def auto_delete_skip(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    broadcast_id = data.get("broadcast_id")
    scheduled_dt: datetime = data.get("scheduled_dt")
    if not broadcast_id or not scheduled_dt:
        await callback.answer("Данные потеряны", show_alert=True)
        await state.clear()
        return
    if scheduled_dt <= now_msk_naive():
        await send_broadcast_by_id(broadcast_id)
        await callback.message.answer("✅ Пост отправлена сразу. Автоматическое удаление нет.")
    else:
        await callback.message.answer(
            f"✅ Пост запланирован на {scheduled_dt.strftime('%d.%m.%Y %H:%M')} (МСК) \n🗑️ Автоматическое удаления нет.",
        )
    # Возвращаем пользователя в меню рассылок, где новая рассылка уже доступна в списке
    await state.clear()
    await show_broadcast_menu(callback.message, state)
    await callback.answer()


@dp.callback_query(F.data == "auto_del_confirm")
async def auto_delete_confirm(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    broadcast_id = data.get("broadcast_id") or data.get("manage_broadcast_id") or data.get("edit_broadcast_id")
    scheduled_dt: datetime = data.get("scheduled_dt")
    auto_delete_dt: datetime = data.get("auto_delete_dt")
    if scheduled_dt is None:
        row = await db.conn.execute("SELECT scheduled_at FROM broadcasts WHERE id = ?", (broadcast_id,))
        r = await row.fetchone()
        if r and r[0]:
            try:
                dt = datetime.fromisoformat(r[0])
                if dt.tzinfo is not None:
                    dt = dt.astimezone(ZoneInfo("Europe/Moscow")).replace(tzinfo=None)
                scheduled_dt = dt
            except Exception:
                scheduled_dt = now_msk_naive()
        else:
            scheduled_dt = now_msk_naive()
    if not broadcast_id or not auto_delete_dt:
        logger.error("auto_del_confirm: missing data: %s", data)
        await callback.answer("Данные потеряны", show_alert=True)
        await state.clear()
        return

    await db.set_broadcast_auto_delete(broadcast_id, auto_delete_dt)

    if scheduled_dt <= now_msk_naive():
        await send_broadcast_by_id(broadcast_id)
        await callback.message.answer(
            f"✅ Пост отправлен сразу. Автоудаление в {auto_delete_dt.strftime('%d.%m.%Y %H:%M')} (МСК)."
        )
    else:
        await callback.message.answer(
            f"✅ Пост запланирован на {scheduled_dt.strftime('%d.%m.%Y %H:%M')} (МСК). \n🗑️ Автоудаление в {auto_delete_dt.strftime('%d.%m.%Y %H:%M')} (МСК)."
        )
    # После подтверждения возвращаемся в список рассылок, чтобы сразу была доступна кнопка создания новой
    await state.clear()
    await show_broadcast_menu(callback.message, state)
    await callback.answer()


@dp.callback_query(F.data == "auto_del_change")
async def auto_delete_change(callback: types.CallbackQuery, state: FSMContext):
    # Возвращаем пользователя к повторному вводу времени автоудаления
    limit_dt = now_msk_naive() + timedelta(hours=48)
    await callback.message.answer(
        "Введите время автоудаления ещё раз:\n" \
        "— до 48 часов (например: 1, 6, 24)\n" \
        f"— или дата/время по МСК, не позже чем ({limit_dt.strftime('%d.%m.%Y %H:%M')})",
    )
    await state.set_state(BroadcastState.waiting_for_auto_delete)
    await callback.answer()
@dp.callback_query(F.data == "edit_schedule_confirm")
async def confirm_edit_schedule_callback(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    b_id = data.get("edit_broadcast_id")
    dt: datetime = data.get("edit_new_dt")
    if not b_id or not dt:
        await callback.answer("Данные потеряны", show_alert=True)
        await state.clear()
        return
    row = await db.conn.execute("SELECT source_chat_id, source_message_id FROM broadcasts WHERE id = ?", (b_id,))
    src = await row.fetchone()
    if not src:
        await callback.answer("Рассылка не найдена", show_alert=True)
        await state.clear()
        return
    await db.set_broadcast_schedule(b_id, dt, src[0], src[1])
    await callback.message.answer(f"✅ Время рассылки обновлено на {dt.strftime('%d.%m.%Y %H:%M')} (МСК). Укажите при необходимости автоудаление через /panel → 📢 Рассылка → выбранная рассылка.")
    await state.clear()
    await callback.answer()

@dp.callback_query(F.data.startswith("delete_broadcast"))
async def delete_broadcast_callback(callback: types.CallbackQuery):
    broadcast_id = int(callback.data.split(":")[1])
    messages = await db.get_broadcast_messages(broadcast_id)
    deleted = 0
    for chat_id, message_id in messages:
        try:
            await bot.delete_message(chat_id, message_id)
            deleted += 1
        except Exception as e:
            logger.exception(f"Не удалось удалить сообщение {message_id} в {chat_id}: {e}")
    
    # Помечаем рассылку как удаленную
    await db.mark_broadcast_as_deleted(broadcast_id)
    
    await callback.message.answer(f"🗑 Удалено {deleted} сообщений. Рассылка помечена как удаленная.")
    await callback.answer()


# Команда для повторной отправки рассылки админом
@dp.message(Command("resend"))
@admin_required
async def cmd_resend(message: types.Message, command: CommandObject):
    if not command.args:
        await message.answer("Формат: /resend <id>")
        return
    try:
        b_id = int(command.args.strip())
    except ValueError:
        await message.answer("ID должен быть числом")
        return
    # сбрасываем флаг, чтобы планировщик не игнорировал
    await db.reset_broadcast_sent_flag(b_id)
    await send_broadcast_by_id(b_id)
    await message.answer(f"♻️ Перезапуск рассылки #{b_id} выполнен")


@dp.message(Command("delete_last"))
@admin_required
async def cmd_delete_last(message: types.Message):
    broadcast_id = await db.get_last_broadcast_id()
    if not broadcast_id:
        await message.answer("Нет прошлых рассылок.")
        return
    messages = await db.get_broadcast_messages(broadcast_id)
    deleted = 0
    for chat_id, message_id in messages:
        try:
            await bot.delete_message(chat_id, message_id)
            deleted += 1
        except Exception as e:
            logger.error(f"Не удалось удалить сообщение {message_id} в {chat_id}: {e}")
    
    # Помечаем рассылку как удаленную
    await db.mark_broadcast_as_deleted(broadcast_id)
    
    await message.answer(f"🗑 Удалено {deleted} сообщений последней рассылки. Рассылка помечена как удаленная.")


# ---- Команды управления группами ---- #

@dp.message(Command("groups"))
@admin_required
async def cmd_groups(message: types.Message):
    groups = await db.get_all_groups()
    if not groups:
        await message.answer("Пока нет зарегистрированных групп.")
        return
    
    total = len(groups)
    # берём последние 3 добавленные (по порядку в БД)
    last_three = list(reversed(groups))[:3]

    text = (
        f"🎓 Всего групп: <b>{total}</b>\n\n"
        f"🆕 Последние группы:\n"
    )

    for chat_id, title in last_three:
        text += f"• <b>{title}</b> (ID: <code>{chat_id}</code>)\n"
    
    await message.answer(text)


@dp.message(Command("assign"))
@admin_required
async def cmd_assign_group(message: types.Message, command: CommandObject):
    if not command.args:
        await message.answer("Формат: /assign &lt;chat_id&gt; &lt;название_списка&gt;\nИспользуйте /groups чтобы увидеть ID групп")
        return
    
    args = command.args.split(" ", 1)
    if len(args) != 2:
        await message.answer("Формат: /assign &lt;chat_id&gt; &lt;название_списка&gt;")
        return
    
    try:
        chat_id = int(args[0])
        list_name = args[1].strip()
    except ValueError:
        await message.answer("Chat ID должен быть числом")
        return
    
    # Проверяем существование списка
    list_row = await db.get_list_by_name(list_name)
    if not list_row:
        await message.answer(f"Список '{list_name}' не найден. Создайте его командой /create_list")
        return
    
    list_id = list_row[0]
    await db.assign_group_to_list(chat_id, list_id)
    await message.answer(f"✅ Группа {chat_id} привязана к списку <b>{list_name}</b>")


# ---- Главное меню ---- #

def admin_reply_keyboard() -> ReplyKeyboardMarkup:
    """Фиксированная клавиатура администратора"""
    kb = ReplyKeyboardBuilder()
    kb.button(text="📢 Рассылка")
    kb.button(text="📂 Сегменты")
    kb.button(text="🎓 Группы") 
    kb.button(text="⚙️ Настройки")
    kb.adjust(2, 2)  # два ряда
    return kb.as_markup(resize_keyboard=True, persistent=True)


@dp.message(Command("panel"))
@admin_required
async def cmd_panel(message: types.Message):
    await message.answer(
        "🏠 Админ-панель активирована!\n\n"
        "Используйте кнопки ниже для управления ⬇️",
        reply_markup=admin_reply_keyboard()
    )


# ---- Обработчики кнопок ---- #
























# ---- Глобальная кнопка Назад ---- #

@dp.message(F.text.contains("Назад"))
@admin_required
async def handle_back_button(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    logger.debug("handle_back_button: current state=%s", current_state)
    await state.clear()
    await message.answer("🏠 Вернулись в главное меню", reply_markup=admin_reply_keyboard())


# ---- Обработчики меню (кнопки) ---- #


# Рассылка
async def show_broadcast_menu(message: types.Message, state: FSMContext):
    """Показывает меню рассылок без проверки прав (можно вызывать из callback)."""
    all_broadcasts = await db.get_recent_broadcasts_with_message_count(30)

    kb = ReplyKeyboardBuilder()
    # Сначала кнопка создания новой рассылки
    kb.button(text="➕ Новая рассылка")
    # Затем список всех рассылок
    for b_id, date, seg_name, ctype, content, message_count, deleted in all_broadcasts:
        content_preview = (content or ctype or "")[:30] + "…"
        if deleted:
            msg_info = "удалена"
        elif message_count == 0:
            msg_info = "нет сообщений"
        else:
            msg_info = f"{message_count} сообщений"
        title = f"№{b_id}. {seg_name or 'Без сегмента'}, «{content_preview}» ({msg_info})"
        kb.button(text=title)
    kb.button(text="⬅️ Назад")
    kb.adjust(1)
    txt_lines = ["📢 Управление рассылками"]
    if all_broadcasts:
        txt_lines.append(f"Показаны последние {len(all_broadcasts)} рассылок")
        txt_lines.append("Выберите рассылку для управления или создайте новую.")
    else:
        txt_lines.append("Пока нет рассылок. Создайте первую!")
    await send_long_message_with_keyboard(
        message,
        "\n".join(txt_lines),
        reply_markup=kb.as_markup(resize_keyboard=True),
    )
    await state.set_state(MenuState.broadcast_menu)


@dp.message(F.text == "📢 Рассылка")
@admin_required
async def handle_broadcast_button(message: types.Message, state: FSMContext):
    await show_broadcast_menu(message, state)

# ----- Меню управления рассылками ----- #

@dp.message(MenuState.broadcast_menu)
@admin_required
async def process_broadcast_menu(message: types.Message, state: FSMContext):
    # Если в меню рассылок пользователь сразу присылает сообщение (текст/медиа),
    # воспринимаем это как создание новой рассылки без нажатия кнопки
    if message.content_type != "text":
        await broadcast_save_message(message, state)
        return

    txt = message.text or ""
    if txt not in ("⬅️ Назад", "➕ Новая рассылка") and not txt.startswith("№") and txt.strip():
        # Текст, который не является кнопкой и не выбором рассылки —
        # трактуем как сообщение новой рассылки
        await broadcast_save_message(message, state)
        return
    if txt == "⬅️ Назад":
        await state.clear()
        await message.answer("🏠 Вернулись в главное меню", reply_markup=admin_reply_keyboard())
        return
    if txt == "➕ Новая рассылка":
        await cmd_broadcast(message, state)  # запускаем процесс новой рассылки
        return
    if txt.startswith("№"):
        try:
            b_id = int(txt.split(".", 1)[0][1:])
        except (ValueError, IndexError):
            await message.answer("Неверный формат номера рассылки.")
            return
        
        cursor = await db.conn.execute(
            "SELECT date, scheduled_at, sent, content_type, content, list_id, deleted, auto_delete_at FROM broadcasts WHERE id = ?",
            (b_id,)
        )
        row = await cursor.fetchone()
        if not row:
            await message.answer("Рассылка не найдена.")
            return

        date, scheduled_at, sent_flag, ctype, content, list_id, deleted, auto_delete_at = row
        seg_row = await db.conn.execute("SELECT name FROM lists WHERE id = ?", (list_id,))
        seg = await seg_row.fetchone()
        seg_name = seg[0] if seg else "-"

        preview = (content or "[non-text]")[:200]
        status_text = "🗑 <b>УДАЛЕНА</b>" if deleted else ("✅ <b>Отправлена</b>" if sent_flag else "⏳ <b>Запланирована</b>")
        schedule_info = format_scheduled_str(scheduled_at) if scheduled_at else "не задано"
        auto_del_info = format_scheduled_str(auto_delete_at) if auto_delete_at else "не установлено"
        created_info = utc_str_to_msk_str(date) if isinstance(date, str) else str(date)
        text = (
            f"📰 <b>Рассылка #{b_id}</b>\n"
            f"📅 Создана: {created_info}\n"
            f"⏰ Публикация: {schedule_info}\n"
            f"🧹 Автоудаление: {auto_del_info}\n"
            f"📂 Сегмент: <b>{seg_name}</b>\n"
            f"📊 Статус: {status_text}\n\n"
            f"<i>Содержимое:</i> {preview}"
        )

        kb = ReplyKeyboardBuilder()
        if not deleted and not sent_flag:
            kb.button(text="⏰ Изменить время публикации")
        # Кнопки управления автоудалением
        if not deleted:
            if auto_delete_at:
                kb.button(text="🗑️ Изменить время удаления")
            else:
                kb.button(text="🧹 Установить время удаления")
            kb.button(text="❌ Удалить рассылку")
            kb.button(text="✏️ Изменить содержимое")
        kb.button(text="⬅️ Назад")
        kb.adjust(1)

        await message.answer(text, reply_markup=kb.as_markup(resize_keyboard=True))
        await state.update_data(manage_broadcast_id=b_id)
        await state.set_state(MenuState.broadcast_manage_show)
        return
    
    await message.answer("Пожалуйста, используйте кнопки.")


# ----- Удаление выбранной рассылки ----- #

@dp.message(MenuState.broadcast_manage_show)
@admin_required
async def process_broadcast_manage(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Назад":
        # возвращаемся к списку рассылок
        await handle_broadcast_button(message, state)
        return
    if message.text in ("⏰ Изменить время", "⏰ Изменить время публикации"):
        b_id = (await state.get_data()).get("manage_broadcast_id")
        if not b_id:
            await message.answer("ID рассылки потерян.")
            await state.clear()
            return
        await state.update_data(edit_broadcast_id=b_id)
        await message.answer("Введите новую дату/время для рассылки (МСК):")
        await state.set_state(MenuState.broadcast_manage_edit_time)
        return

    if message.text in ("🧹 Установить время удаления", "🧹 Изменить время удаления", "🗑️ Изменить время удаления"):
        b_id = (await state.get_data()).get("manage_broadcast_id")
        if not b_id:
            await message.answer("ID рассылки потерян.")
            await state.clear()
            return
        # Берём scheduled_at из БД, чтобы считать лимит от времени публикации
        row = await db.conn.execute("SELECT scheduled_at FROM broadcasts WHERE id = ?", (b_id,))
        r = await row.fetchone()
        scheduled_dt = None
        if r and r[0]:
            try:
                dt = datetime.fromisoformat(r[0])
                if dt.tzinfo is not None:
                    dt = dt.astimezone(ZoneInfo("Europe/Moscow")).replace(tzinfo=None)
                scheduled_dt = dt
            except Exception:
                scheduled_dt = None
        await state.update_data(broadcast_id=b_id, scheduled_dt=scheduled_dt)
        limit_dt = (scheduled_dt or now_msk_naive()) + timedelta(hours=48)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🚫 Не удалять автоматически", callback_data="auto_del_skip")]]
        )
        await message.answer(
            "Через сколько часов удалить пост?\n" \
            "— до 48 часов (например: 1, 6, 24)\n" \
            f"— или укажите дату и время (МСК), не позже чем через 48 часов ({limit_dt.strftime('%d.%m.%Y %H:%M')})\n\n" \
            "Нажмите кнопку, если не нужно удалять автоматически.",
            reply_markup=kb,
        )
        await state.set_state(BroadcastState.waiting_for_auto_delete)
        return

    if message.text == "✏️ Изменить содержимое":
        b_id = (await state.get_data()).get("manage_broadcast_id")
        if not b_id:
            await message.answer("ID рассылки потерян.")
            await state.clear()
            return
        await state.update_data(edit_broadcast_id=b_id)
        await message.answer("Отправьте новый текст для замены содержимого поста.")
        await state.set_state(MenuState.broadcast_edit_content_wait)
        return

    if message.text == "🗑 Удалить рассылку":
        data = await state.get_data()
        b_id = data.get("manage_broadcast_id")
        if not b_id:
            await message.answer("ID рассылки потерян.")
            await state.clear()
            return
        
        messages = await db.get_broadcast_messages(b_id)
        deleted = 0
        for chat_id, msg_id in messages:
            try:
                await bot.delete_message(chat_id, msg_id)
                deleted += 1
            except Exception as e:
                logger.error(f"Не удалось удалить сообщение {msg_id} в {chat_id}: {e}")

        # Помечаем рассылку как удаленную
        await db.mark_broadcast_as_deleted(b_id)

        await message.answer(f"🗑 Удалено {deleted} сообщений рассылки #{b_id}. Рассылка помечена как удаленная.", reply_markup=admin_reply_keyboard())
        await state.clear()
        return

    await message.answer("Используйте кнопки управления рассылкой.")


# ---- Изменение времени рассылки ---- #
@dp.message(MenuState.broadcast_manage_edit_time)
@admin_required
async def process_broadcast_edit_time(message: types.Message, state: FSMContext):
    text = message.text.strip()
    dt = dateparser.parse(
        text,
        languages=["ru"],
        settings={
            "RELATIVE_BASE": now_msk_naive(),
            "TIMEZONE": "Europe/Moscow",
            "RETURN_AS_TIMEZONE_AWARE": False,
        },
    )
    if not dt:
        await message.answer("Не удалось распознать дату/время. Попробуйте ещё раз.")
        return
    data = await state.get_data()
    b_id = data.get("edit_broadcast_id")
    if not b_id:
        await message.answer("ID рассылки потерян.")
        await state.clear()
        return

    # Сохраняем выбранное время в FSM и спрашиваем подтверждение
    await state.update_data(edit_new_dt=dt)
    confirm_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✅ Подтвердить", callback_data="edit_schedule_confirm"), InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]]
    )
    await message.answer(
        f"Подтвердить новое время: {dt.strftime('%d.%m.%Y %H:%M')} (МСК)?",
        reply_markup=confirm_kb,
    )


# Сегменты
@dp.message(F.text == "📂 Сегменты")
@admin_required
async def handle_lists_button(message: types.Message, state: FSMContext):
    # выводим список сегментов
    segments = await db.get_lists()
    kb = ReplyKeyboardBuilder()
    kb.button(text="➕ Создать сегмент")
    kb.button(text="⬅️ Назад")
    kb.adjust(1)
    if not segments:
        await message.answer("Пока нет ни одного сегмента. Используйте ➕ Создать сегмент.", reply_markup=kb.as_markup(resize_keyboard=True))
        return

    text = "📂 <b>Сегменты</b>:\n\n"
    text += "\n".join([f"• <b>{name}</b>" for _, name in segments])

    kb = ReplyKeyboardBuilder()
    for _, name in segments:
        kb.button(text=f"📂 {name}")
    kb.button(text="➕ Создать сегмент")
    kb.button(text="⬅️ Назад")
    kb.adjust(2, 1)
    
    await message.answer(text, reply_markup=kb.as_markup(resize_keyboard=True))
    await state.set_state(MenuState.segment_view_select_list)

# --- Кнопка создания сегмента --- #

@dp.message(F.text == "➕ Создать сегмент")
@admin_required
async def handle_create_segment_button(message: types.Message, state: FSMContext):
    await message.answer(
        "Введите название нового сегмента:",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True)
    )
    await state.set_state(MenuState.list_create_wait_name)

@dp.message(MenuState.list_create_wait_name)
async def process_new_segment_name(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("✅ Действие отменено.", reply_markup=admin_reply_keyboard())
        return
    name = message.text.strip()
    await db.create_list(name)
    await message.answer(f"✅ Сегмент <b>{name}</b> создан.", reply_markup=admin_reply_keyboard())
    await state.clear()

# Группы
@dp.message(F.text == "🎓 Группы")
@admin_required
async def handle_groups_button(message: types.Message, state: FSMContext):
    # Показываем укороченную сводку (функция cmd_groups уже выводит короткое сообщение)
    await cmd_groups(message)

    # Клавиатура действий
    kb = ReplyKeyboardBuilder()
    kb.button(text="➕ Добавить группу", request_chat=KeyboardButtonRequestChat(
        request_id=1,
        chat_is_channel=False,
        chat_is_forum=False,
        bot_is_member=True,
    ))
    kb.button(text="✏️ Редактировать группу")
    kb.button(text="⬅️ Назад")
    kb.adjust(1)
    
    await message.answer(
        "Выберите действие:",
        reply_markup=kb.as_markup(resize_keyboard=True)
    )
    await state.set_state(MenuState.group_add_select_group)


# --- Обработка выбранной группы (chat_shared) ---

@dp.message(lambda m: m.chat_shared is not None)
@admin_required
async def handle_chat_shared(message: types.Message, state: FSMContext):
    chat_id = message.chat_shared.chat_id
    try:
        chat = await bot.get_chat(chat_id)
        title = chat.title or "Без названия"
    except Exception:
        title = "Без названия"

    # Регистрируем группу если новой
    await db.conn.execute(
        "INSERT OR IGNORE INTO groups(chat_id, title) VALUES (?, ?)",
        (chat_id, title),
    )
    await db.conn.commit()

    await state.update_data(selected_group_id=chat_id)

    # Показать список сегментов
    lists = await db.get_lists()
    if not lists:
        await message.answer("Сначала создайте сегмент через /segments.", reply_markup=admin_reply_keyboard())
        await state.clear()
        return

    kb = ReplyKeyboardBuilder()
    for list_id, name in lists:
        kb.button(text=f"📂 {name}")
    kb.button(text="❌ Отмена")
    kb.adjust(1)

    await message.answer("Выберите сегмент для этой группы:", reply_markup=kb.as_markup(resize_keyboard=True))
    await state.set_state(MenuState.group_add_select_list)


# --- Привязка группы к выбранному сегменту ---

@dp.message(MenuState.group_add_select_list)
@admin_required
async def process_group_add_list(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена" or message.text == "⬅️ Назад":
        await state.clear()
        await message.answer("✅ Действие отменено.", reply_markup=admin_reply_keyboard())
        return
    
    if not message.text.startswith("📂 "):
        await message.answer("Пожалуйста, выберите сегмент из кнопок.")
        return
    
    segment_name = message.text[2:].strip()
    lists = await db.get_lists()
    list_id = None
    for lid, name in lists:
        if name == segment_name:
            list_id = lid
            break
    
    if not list_id:
        await message.answer("Сегмент не найден.")
        return
    
    data = await state.get_data()
    group_id = data.get('selected_group_id')
    if not group_id:
        await message.answer("Ошибка: группа не выбрана.", reply_markup=admin_reply_keyboard())
        await state.clear()
        return
    
    await db.assign_group_to_list(group_id, list_id)
    await message.answer(
        f"🔗 Группа <code>{group_id}</code> добавлена в сегмент <b>{segment_name}</b>.",
        reply_markup=admin_reply_keyboard()
    )
    await state.clear()


# Настройки
@dp.message(F.text == "⚙️ Настройки")
@admin_required
async def handle_settings_button(message: types.Message, state: FSMContext):
    kb = ReplyKeyboardBuilder()
    kb.button(text="👑 Управление админами")
    kb.button(text="📋 Справка")
    kb.button(text="⬅️ Назад")
    kb.adjust(1)
    
    await message.answer(
        "⚙️ <b>Настройки бота</b>\n\n"
        "Выберите раздел для настройки:",
        reply_markup=kb.as_markup(resize_keyboard=True)
    )
    await state.set_state(MenuState.settings_menu)


# --- Обработчики меню настроек --- #

@dp.message(MenuState.settings_menu)
@admin_required
async def process_settings_menu(message: types.Message, state: FSMContext):
    txt = message.text or ""
    
    if txt == "⬅️ Назад":
        await state.clear()
        await message.answer("🏠 Вернулись в главное меню", reply_markup=admin_reply_keyboard())
        return
    
    if txt == "👑 Управление админами":
        # Получаем список всех админов
        admins = await db.get_all_admins()
        current_id = message.from_user.id
        current_is_super = await is_super_admin(current_id)

        # Обновляем данные о каждом админе, если имя/ник неизвестны
        enriched_admins = []
        for user_id, username, first_name, added_at in admins:
            if not username or username == "from_config" or not first_name or first_name == "Legacy Admin":
                try:
                    user_chat = await bot.get_chat(user_id)
                    username = user_chat.username or username
                    first_name = user_chat.first_name or first_name
                    # сохраняем обновлённые данные
                    super_flag = 1 if await db.is_super_admin(user_id) else 0
                    await db.add_admin(user_id, username, first_name, super_admin=super_flag)
                except Exception:
                    pass
            enriched_admins.append((user_id, username, first_name, added_at))
        admins = enriched_admins

        text = "👑 <b>Управление администраторами</b>\n\n"
        visible_admins = []
        for user_id, username, first_name, added_at in admins:
            # Скрываем супер админа от других обычных администраторов
            if await db.is_super_admin(user_id) and user_id != current_id:
                continue
            visible_admins.append((user_id, username, first_name, added_at))
        
        if visible_admins:
            text += "📋 <b>Текущие администраторы:</b>\n"
            for user_id, username, first_name, added_at in visible_admins:
                if user_id == current_id:
                    name = "Вы"
                else:
                    name = f"{first_name or 'Неизвестно'}"
                    if username and username != "from_config":
                        name += f" (@{username})"
                if await db.is_super_admin(user_id):
                    name += " 🔑"
                text += f"• {name} (ID: <code>{user_id}</code>)\n"
            text += f"\n📊 Всего админов: {len(visible_admins)}"
        else:
            text += "❌ Нет администраторов для отображения"
        
        text += "\n\nВыберите действие:"
        
        kb = ReplyKeyboardBuilder()
        kb.button(text="➕ Добавить админа")
        if len(visible_admins) > 1:
            kb.button(text="❌ Удалить админа")
        if current_is_super:
            kb.button(text="🔑 Передать суперправа")
        kb.button(text="⬅️ Назад")
        kb.adjust(2, 1)
        
        await message.answer(text, reply_markup=kb.as_markup(resize_keyboard=True))
        await state.set_state(MenuState.admin_management)
        return
    
    if txt == "📋 Справка":
        await cmd_help(message)
        return
    
    await message.answer("Пожалуйста, используйте кнопки.")


# --- Управление админами --- #

@dp.message(MenuState.admin_management)
@admin_required
async def process_admin_management(message: types.Message, state: FSMContext):
    txt = message.text or ""
    
    if txt == "⬅️ Назад":
        await handle_settings_button(message, state)
        return
    
    if txt == "➕ Добавить админа":
        await message.answer(
            "👥 Выберите пользователя для назначения администратором:\n\n"
            "• Нажмите кнопку ниже\n"
            "• Выберите пользователя из списка контактов\n"
            "• Убедитесь, что у пользователя есть диалог с ботом",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(
                        text="👤 Выбрать пользователя",
                        request_user=types.KeyboardButtonRequestUser(
                            request_id=1,
                            user_is_bot=False
                        )
                    )],
                    [KeyboardButton(text="❌ Отмена")]
                ],
                resize_keyboard=True
            )
        )
        await state.set_state(MenuState.admin_add_wait_user)
        return
    
    if txt == "🔑 Передать суперправа":
            # список админов без текущего
        admins = await db.get_all_admins()
        selectable = [(uid, uname, fname) for uid, uname, fname, _ in admins if uid != message.from_user.id]
        if not selectable:
            await message.answer("❌ Нет админов для передачи прав.")
            return
        kb = ReplyKeyboardBuilder()
        for uid, uname, fname in selectable:
            name = f"{fname or 'Неизвестно'}"
            if uname:
                name += f" (@{uname})"
            kb.button(text=f"🔑 {name}")
        kb.button(text="❌ Отмена")
        kb.adjust(1)
        await message.answer("Выберите администратора, которому передать суперправа:", reply_markup=kb.as_markup(resize_keyboard=True))
        await state.update_data(selectable_admins=selectable)
        await state.set_state(MenuState.admin_transfer_select)
        return

    if txt == "❌ Удалить админа":
        admins = await db.get_all_admins()
        if not admins:
            await message.answer("❌ Нет админов для удаления.")
            return
        
        # Проверяем, что не остается только один админ
        if len(admins) <= 1:
            await message.answer("⚠️ Нельзя удалить последнего администратора!")
            return
        
        text = "❌ <b>Удаление администратора</b>\n\n"
        text += "Выберите администратора для удаления:\n\n"
        
        kb = ReplyKeyboardBuilder()
        for user_id, username, first_name, added_at in admins:
            # Пропускаем супер админа и самого себя
            if await db.is_super_admin(user_id):
                continue
            if user_id == message.from_user.id:
                continue
                
            name = f"{first_name or 'Неизвестно'}"
            if username:
                name += f" (@{username})"
            kb.button(text=f"🗑 {name}")
        
        kb.button(text="❌ Отмена")
        kb.adjust(1)
        
        if len([admin for admin in admins if admin[0] != message.from_user.id]) == 0:
            await message.answer("⚠️ Вы не можете удалить себя из админов!")
            return
        
        await message.answer(text, reply_markup=kb.as_markup(resize_keyboard=True))
        await state.update_data(available_admins=admins)
        await state.set_state(MenuState.admin_delete_select)
        return
    
    await message.answer("Пожалуйста, используйте кнопки.")


# --- Добавление админа --- #

@dp.message(lambda m: m.user_shared is not None)
@admin_required
async def handle_user_shared(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state != MenuState.admin_add_wait_user:
        return
    
    user_id = message.user_shared.user_id
    
    # Проверяем, не является ли пользователь уже админом
    if await db.is_admin(user_id):
        await message.answer("⚠️ Этот пользователь уже является администратором!")
        return
    
    try:
        # Пытаемся получить информацию о пользователе
        user = await bot.get_chat(user_id)
        username = user.username
        first_name = user.first_name
    except Exception:
        username = None
        first_name = None
    
    # Добавляем админа
    await db.add_admin(user_id, username, first_name, message.from_user.id)
    
    name = f"{first_name or 'Неизвестно'}"
    if username:
        name += f" (@{username})"
    
    await message.answer(
        f"✅ Пользователь {name} (ID: <code>{user_id}</code>) назначен администратором!",
        reply_markup=admin_reply_keyboard()
    )
    await state.clear()


@dp.message(MenuState.admin_add_wait_user)
@admin_required
async def process_admin_add_cancel(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await message.answer("✅ Добавление админа отменено.", reply_markup=admin_reply_keyboard())
        await state.clear()
        return
    
    await message.answer("Пожалуйста, используйте кнопку для выбора пользователя.")


# --- Удаление админа --- #

@dp.message(MenuState.admin_delete_select)
@admin_required
async def process_admin_delete_select(message: types.Message, state: FSMContext):
    txt = message.text or ""
    
    if txt == "❌ Отмена":
        await message.answer("✅ Удаление админа отменено.", reply_markup=admin_reply_keyboard())
        await state.clear()
        return
    
    if not txt.startswith("🗑 "):
        await message.answer("Пожалуйста, выберите админа из списка.")
        return
    
    admin_name = txt[2:].strip()
    data = await state.get_data()
    available_admins = data.get("available_admins", [])
    
    # Находим выбранного админа
    selected_admin = None
    for user_id, username, first_name, added_at in available_admins:
        name = f"{first_name or 'Неизвестно'}"
        if username:
            name += f" (@{username})"
        if name == admin_name:
            selected_admin = (user_id, username, first_name, added_at)
            break
    
    if not selected_admin:
        await message.answer("❌ Админ не найден.")
        return
    
    user_id, username, first_name, _ = selected_admin
    name = f"{first_name or 'Неизвестно'}"
    if username:
        name += f" (@{username})"
    
    await state.update_data(admin_to_delete=selected_admin)
    
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Да, удалить"), KeyboardButton(text="❌ Нет, отмена")]
        ],
        resize_keyboard=True
    )
    
    await message.answer(
        f"⚠️ <b>Подтвердите удаление</b>\n\n"
        f"Вы действительно хотите удалить из администраторов пользователя:\n"
        f"👤 {name} (ID: <code>{user_id}</code>)?",
        reply_markup=kb
    )
    await state.set_state(MenuState.admin_delete_confirm)


@dp.message(MenuState.admin_delete_confirm)
@admin_required
async def process_admin_delete_confirm(message: types.Message, state: FSMContext):
    txt = message.text or ""
    
    if txt == "❌ Нет, отмена":
        await message.answer("✅ Удаление админа отменено.", reply_markup=admin_reply_keyboard())
        await state.clear()
        return
    
    if txt == "✅ Да, удалить":
        data = await state.get_data()
        admin_to_delete = data.get("admin_to_delete")
        
        if not admin_to_delete:
            await message.answer("❌ Ошибка: данные админа потеряны.")
            await state.clear()
            return
        
        user_id, username, first_name, _ = admin_to_delete
        name = f"{first_name or 'Неизвестно'}"
        if username:
            name += f" (@{username})"
        
        # Удаляем админа
        await db.remove_admin(user_id)
        
        await message.answer(
            f"✅ Администратор {name} (ID: <code>{user_id}</code>) успешно удален!",
            reply_markup=admin_reply_keyboard()
        )
        await state.clear()
        return
    
    await message.answer("Пожалуйста, используйте кнопки для ответа.")


# --- Передача суперправ --- #

@dp.message(MenuState.admin_transfer_select)
@admin_required
async def process_admin_transfer_select(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await message.answer("✅ Передача суперправ отменена.", reply_markup=admin_reply_keyboard())
        await state.clear()
        return
    if not message.text.startswith("🔑 "):
        await message.answer("Пожалуйста, выберите админа из списка.")
        return
    admin_name = message.text[2:].strip()
    data = await state.get_data()
    selectable = data.get("selectable_admins", [])
    selected = None
    for uid, uname, fname in selectable:
        name = f"{fname or 'Неизвестно'}"
        if uname:
            name += f" (@{uname})"
        if name == admin_name:
            selected = (uid, uname, fname)
            break
    if not selected:
        await message.answer("❌ Админ не найден.")
        return
    uid, uname, fname = selected
    name = f"{fname or 'Неизвестно'}"
    if uname:
        name += f" (@{uname})"
    await state.update_data(new_super_admin=uid)
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="✅ Да, передать"), KeyboardButton(text="❌ Нет, отмена")]], resize_keyboard=True)
    await message.answer(f"⚠️ Подтвердите передачу суперправ пользователю {name}. Вы потеряете статус супер админа.", reply_markup=kb)
    await state.set_state(MenuState.admin_transfer_confirm)

@dp.message(MenuState.admin_transfer_confirm)
@admin_required
async def process_admin_transfer_confirm(message: types.Message, state: FSMContext):
    if message.text == "❌ Нет, отмена":
        await message.answer("✅ Передача суперправ отменена.", reply_markup=admin_reply_keyboard())
        await state.clear()
        return
    if message.text == "✅ Да, передать":
        data = await state.get_data()
        new_uid = data.get("new_super_admin")
        if not new_uid:
            await message.answer("❌ Ошибка передачи прав.")
            await state.clear()
            return
        await db.set_super_admin(new_uid)
        await message.answer("✅ Суперправа успешно переданы!", reply_markup=admin_reply_keyboard())
        await state.clear()
        return
    await message.answer("Пожалуйста, используйте кнопки.")


# --- Редактирование группы --- #

@dp.message(F.text == "✏️ Редактировать группу")
@admin_required
async def edit_school_start(message: types.Message, state: FSMContext):
    await message.answer(
        "Введите часть названия группы (рус/eng/транслит):",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True)
    )
    await state.set_state(MenuState.edit_search)


@dp.message(MenuState.edit_search)
async def edit_school_search(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("✅ Действие отменено.", reply_markup=admin_reply_keyboard())
        return
    
    query = message.text.strip().lower()

    all_groups = await db.get_all_groups()

    query_t = translit_ru(query)

    def match_score(title: str) -> bool:
        low = title.lower()
        if query in low:
            return True
        if query_t and query_t in translit_ru(low):
            return True
        return False

    matches = [(cid, title) for cid, title in all_groups if match_score(title)]

    if not matches:
        await message.answer("Не нашёл похожих групп. Попробуйте ещё раз.")
        return
    
    # ограничим 5
    matches = matches[:5]
    await state.update_data(search_matches=matches)

    if len(matches) == 1:
        cid, title = matches[0]
        await state.update_data(selected_group_id=cid, selected_group_title=title)
        yes_no_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="✅ Да"), KeyboardButton(text="❌ Нет")]], resize_keyboard=True)
        await message.answer(f"Вы имели ввиду <b>{title}</b>?", reply_markup=yes_no_kb)
        await state.set_state(MenuState.edit_confirm)
    else:
        lines = ["Найдены группы:"]
        for idx, (_, title) in enumerate(matches, 1):
            lines.append(f"{idx}. {title}")
        lines.append("\nНапишите номер нужной группы или ❌ Отмена")
        await message.answer("\n".join(lines))
        await state.set_state(MenuState.edit_confirm)


# ===== Helper: показать меню действий группы ===== #

async def show_edit_actions(message: types.Message, state: FSMContext, group_id: int, title: str):
    """Отобразить карточку группы с действиями."""
    segments = await db.get_group_segments(group_id)
    if segments:
        seg_text = ", ".join(segments)
    else:
        seg_text = "-"

    text = (
        f"🏫 <b>{title}</b> (ID: <code>{group_id}</code>)\n"
        f"📂 Сегменты: <b>{seg_text}</b>\n\n"
        "Выберите действие:"
    )

    kb = ReplyKeyboardBuilder()
    kb.button(text="🗑 Удалить группу")
    kb.button(text="➕ Добавить в сегмент")
    kb.button(text="❌ Удалить из сегмента")
    kb.button(text="🤖 Умное управление")
    kb.button(text="⬅️ Назад")
    kb.adjust(1)
    
    await message.answer(text, reply_markup=kb.as_markup(resize_keyboard=True))
    await state.set_state(MenuState.edit_actions)


@dp.message(MenuState.edit_confirm)
async def edit_school_confirm(message: types.Message, state: FSMContext):
    txt = message.text.strip().lower()
    data = await state.get_data()
    matches = data.get("search_matches", [])

    if txt in ("нет", "❌ нет"):
        # остаёмся в поиске
        await state.set_state(MenuState.edit_search)
        cancel_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True)
        await message.answer("Введите часть названия группы ещё раз:", reply_markup=cancel_kb)
        return
    if txt in ("отмена", "❌ отмена"):
        await state.clear()
        await message.answer("✅ Действие отменено.", reply_markup=admin_reply_keyboard())
        return
    
    # обработка ответа после одного варианта
    if txt in ("✅ да", "да", "✅ Да") and data.get("selected_group_id"):
        pass  # уже выбрано
    elif txt.isdigit():
        idx = int(txt)
        if not (1 <= idx <= len(matches)):
            await message.answer("Неверный номер. Попробуйте снова.")
            return
        cid, title = matches[idx-1]
        await state.update_data(selected_group_id=cid, selected_group_title=title)
    else:
        await message.answer("Ответ не распознан. Напишите номер или 'да'.")
        return

    # переходим к действиям
    data = await state.get_data()
    title = data.get("selected_group_title")
    group_id = data.get("selected_group_id")

    await show_edit_actions(message, state, group_id, title)


@dp.message(MenuState.edit_actions)
@admin_required
async def edit_school_actions(message: types.Message, state: FSMContext):
    txt = message.text or ""
    data = await state.get_data()
    group_id = data.get("selected_group_id")
    title = data.get("selected_group_title")
    
    if txt == "⬅️ Назад":
        await handle_groups_button(message, state)
        return

    if txt == "🗑 Удалить группу":
        # кикаем бота
        try:
            await bot.leave_chat(group_id)
        except Exception:
            pass
        await db.delete_group(group_id)
        await message.answer(f"🗑 Группа <b>{title}</b> удалена.", reply_markup=admin_reply_keyboard())
        await state.clear()
        return
    
    if txt == "➕ Добавить в сегмент":
        lists = await db.get_lists()
        if not lists:
            await message.answer("Сначала создайте сегмент.")
            return
        kb = ReplyKeyboardBuilder()
        for lid, name in lists:
            kb.button(text=f"📂 {name}")
        kb.button(text="❌ Отмена")
        kb.adjust(1)
        await message.answer("Выберите сегмент:", reply_markup=kb.as_markup(resize_keyboard=True))
        await state.set_state(MenuState.edit_add_segment)
        return

    if txt == "❌ Удалить из сегмента":
        seg_names = await db.get_group_segments(group_id)
        if not seg_names:
            await message.answer("Группа не привязана ни к одному сегменту.")
            return
        kb = ReplyKeyboardBuilder()
        for name in seg_names:
            kb.button(text=f"📂 {name}")
        kb.button(text="❌ Отмена")
        kb.adjust(1)
        await message.answer("Выберите сегмент для отвязки:", reply_markup=kb.as_markup(resize_keyboard=True))
        await state.set_state(MenuState.edit_remove_segment)
        return

    if txt == "🤖 Умное управление":
        await message.answer(
            "🤖 Напишите в свободной форме, что нужно сделать с сегментами.\n\n"
            "Примеры:\n"
            "• «Удали из Все группы, добавь в Календарь и Тестовый»\n"
            "• «Добавить в VIP сегмент»\n"
            "• «Исключить из Архива»",
            reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True)
        )
        await state.set_state(MenuState.edit_ai_input)
        return
    
    await message.answer("Используйте кнопки для действий.")


@dp.message(MenuState.edit_add_segment)
@admin_required
async def edit_add_segment(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.set_state(MenuState.edit_actions)
        await message.answer("↩️ Возврат к действиям.")
        return
    if not message.text.startswith("📂 "):
        await message.answer("Пожалуйста, выберите сегмент из кнопок.")
        return
    seg_name = message.text[2:].strip()
    lists = await db.get_lists()
    seg_id = None
    for lid, name in lists:
        if name == seg_name:
            seg_id = lid
            break
    if not seg_id:
        await message.answer("Сегмент не найден.")
        return
    data = await state.get_data()
    group_id = data.get("selected_group_id")
    await db.assign_group_to_list(group_id, seg_id)
    await message.answer(f"🔗 Группа добавлена в сегмент <b>{seg_name}</b>.")
    title = (await state.get_data()).get("selected_group_title")
    await show_edit_actions(message, state, group_id, title)


@dp.message(MenuState.edit_remove_segment)
@admin_required
async def edit_remove_segment(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.set_state(MenuState.edit_actions)
        await message.answer("↩️ Возврат к действиям.")
        return
    if not message.text.startswith("📂 "):
        await message.answer("Выберите сегмент.")
        return
    seg_name = message.text[2:].strip()
    lists = await db.get_lists()
    seg_id = None
    for lid, name in lists:
        if name == seg_name:
            seg_id = lid
            break
    if not seg_id:
        await message.answer("Сегмент не найден.")
        return
    data = await state.get_data()
    group_id = data.get("selected_group_id")
    # проверим принадлежит ли
    seg_names_current = await db.get_group_segments(group_id)
    if seg_name not in seg_names_current:
        await message.answer("Группа уже не состоит в этом сегменте.")
        return
    await db.remove_group_from_list(group_id, seg_id)
    await message.answer(f"❌ Группа отвязана от сегмента <b>{seg_name}</b>.")
    title = (await state.get_data()).get("selected_group_title")
    await show_edit_actions(message, state, group_id, title)


# ======= Просмотр групп сегмента ======= #
@dp.message(MenuState.segment_view_select_list)
@admin_required
async def process_segment_view_selection(message: types.Message, state: FSMContext):
    txt = message.text or ""
    if txt == "⬅️ Назад":
        await state.clear()
        await message.answer("🏠 Вернулись в главное меню", reply_markup=admin_reply_keyboard())
        return
    if txt == "📂 Выбрать другой сегмент":
        await handle_lists_button(message, state)
        return
    if txt == "➕ Создать сегмент":
        await handle_create_segment_button(message, state)
        return
    if not txt.startswith("📂 "):
        await message.answer("Пожалуйста, выберите сегмент из кнопок.")
        return
    seg_name = txt[2:].strip()
    segments = await db.get_lists()
    seg_id = None
    for lid, name in segments:
        if name == seg_name:
            seg_id = lid
            break
    if not seg_id:
        await message.answer("Сегмент не найден.")
        return
    
    groups_info = await db.get_groups_in_list_detailed(seg_id)
    total = len(groups_info)

    text_header = (
        f"📂 <b>Сегмент: {seg_name}</b>\n"
        f"👥 Групп в сегменте: <b>{total}</b>\n\n"
    )

    body_lines = []
    for i, (_, title) in enumerate(groups_info, 1):
        short_title = title if len(title) <= 25 else title[:25] + "…"
        body_lines.append(f"{i}. {short_title}")

    kb = ReplyKeyboardBuilder()
    kb.button(text="📂 Выбрать другой сегмент")
    kb.button(text="⬅️ Назад")
    kb.adjust(1)
    
    await send_long_message_with_keyboard(
        message,
        text_header + "\n".join(body_lines),
        reply_markup=kb.as_markup(resize_keyboard=True),
    )


# ======= ИИ управление сегментами ======= #

@dp.message(MenuState.edit_ai_input)
@admin_required
async def edit_ai_input_handler(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        data = await state.get_data()
        group_id = data.get("selected_group_id")
        title = data.get("selected_group_title")
        await show_edit_actions(message, state, group_id, title)
        return
    
    text = message.text.strip()
    if not text:
        await message.answer("Пожалуйста, введите инструкции.")
        return
    
    # Получаем все доступные сегменты
    all_segments = await db.get_lists()
    segment_names = [name for _, name in all_segments]
    
    # Парсим инструкции
    instructions = parse_segment_instructions(text, segment_names)
    
    if not instructions['add'] and not instructions['remove']:
        error_text = "🤔 Не удалось распознать операции.\n\n"
        if instructions['errors']:
            error_text += f"Возможно, вы имели ввиду сегменты: {', '.join(instructions['errors'])}\n\n"
        error_text += "Попробуйте ещё раз, используя слова: добавить, удалить, включить, исключить."
        await message.answer(error_text)
        return
    
    # Формируем подтверждение
    confirm_lines = ["🤖 Понял! Выполню следующие операции:\n"]
    
    if instructions['add']:
        confirm_lines.append(f"➕ Добавить в сегменты: {', '.join(instructions['add'])}")
    
    if instructions['remove']:
        confirm_lines.append(f"❌ Удалить из сегментов: {', '.join(instructions['remove'])}")
    
    if instructions['errors']:
        confirm_lines.append(f"\n⚠️ Не найдены сегменты: {', '.join(instructions['errors'])}")
    
    confirm_lines.append("\nВсё правильно?")
    
    # Сохраняем операции в state
    await state.update_data(ai_operations=instructions)
    
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="✅ Да, выполнить"), KeyboardButton(text="❌ Нет, исправить")]
    ], resize_keyboard=True)
    
    await message.answer("\n".join(confirm_lines), reply_markup=kb)
    await state.set_state(MenuState.edit_ai_confirm)


@dp.message(MenuState.edit_ai_confirm)
@admin_required
async def edit_ai_confirm_handler(message: types.Message, state: FSMContext):
    data = await state.get_data()
    group_id = data.get("selected_group_id")
    title = data.get("selected_group_title")
    
    if message.text == "❌ Нет, исправить":
        await message.answer(
            "🤖 Введите инструкции ещё раз:",
            reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True)
        )
        await state.set_state(MenuState.edit_ai_input)
        return
    
    if message.text == "✅ Да, выполнить":
        operations = data.get("ai_operations", {})
        
        results = []
        
        # Выполняем удаления
        for segment_name in operations.get('remove', []):
            segments = await db.get_lists()
            seg_id = None
            for lid, name in segments:
                if name == segment_name:
                    seg_id = lid
                    break
            
            if seg_id:
                current_segments = await db.get_group_segments(group_id)
                if segment_name in current_segments:
                    await db.remove_group_from_list(group_id, seg_id)
                    results.append(f"❌ Удалена из «{segment_name}»")
                else:
                    results.append(f"⚠️ Не была в «{segment_name}»")
        
        # Выполняем добавления
        for segment_name in operations.get('add', []):
            segments = await db.get_lists()
            seg_id = None
            for lid, name in segments:
                if name == segment_name:
                    seg_id = lid
                    break
            
            if seg_id:
                current_segments = await db.get_group_segments(group_id)
                if segment_name not in current_segments:
                    await db.assign_group_to_list(group_id, seg_id)
                    results.append(f"➕ Добавлена в «{segment_name}»")
                else:
                    results.append(f"⚠️ Уже была в «{segment_name}»")
        
        if results:
            await message.answer("✅ Операции выполнены:\n\n" + "\n".join(results))
        else:
            await message.answer("🤔 Нечего было изменить.")
        
        # Возвращаемся к меню действий
        await show_edit_actions(message, state, group_id, title)
        return
    
    await message.answer("Используйте кнопки для ответа.")

# ---- Запуск ---- #

async def main():
    try:
        # Проверяем основные переменные окружения
        if not BOT_TOKEN:
            logger.error("❌ BOT_TOKEN не задан в переменных окружения")
            return
        if not ADMIN_IDS:
            logger.warning("⚠️ ADMIN_IDS не заданы, никто не сможет управлять ботом")

        # Инициализация БД
        await db.init()
        logger.info("База данных инициализирована")

        # Миграция админов из конфига в базу данных
        if ADMIN_IDS:
            await db.migrate_admins_from_config(ADMIN_IDS)
            logger.info(f"Перенесено {len(ADMIN_IDS)} админов из конфига в базу данных")



        # Запускаем планировщик рассылок
        asyncio.create_task(broadcast_scheduler())

        logger.info("🚀 Бот запускается...")
        await dp.start_polling(bot)

    except (KeyboardInterrupt, SystemExit):
        logger.info("🛑 Бот остановлен!")
    except Exception as e:
        logger.error(f"❌ Фатальная ошибка: {e}")
        raise


if __name__ == "__main__":
    try:
        # зарегистрируем динамический обработчик
        _append_edit_content_handler()
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("🛑 Бот остановлен!")
    except Exception as e:
        logger.error(f"❌ Фатальная ошибка: {e}")
        raise 
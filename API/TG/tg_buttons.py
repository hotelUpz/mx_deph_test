import asyncio
from pprint import pformat
import copy
from typing import *
from a_config import *
from b_context import BotContext
from c_log import ErrorHandler, log_time
from c_utils import validate_init_sl, validate_tp_cap_dep_levels
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)


# ===== Проверка и нормализация конфига пользователя =====
def validate_user_config(user_cfg: dict) -> bool:
    """
    Проверяет корректность конфигурации пользователя.
    Обновляет tp_levels вторым значением tp_order_volume.
    Гарантирует, что SL отрицательный или None.
    """

    config = user_cfg.setdefault("config", {})
    fin = config.setdefault("fin_settings", {})

    # === MEXC ===
    mexc = config.get("MEXC", {})
    if mexc.get("proxy_url") == 0:
        return False
    if not mexc.get("api_key") or not mexc.get("api_secret") or not mexc.get("u_id"):
        return False

    # === FIN SETTINGS ===
    if fin.get("margin_size") is None:
        return False
    if fin.get("margin_mode") is None:
        return False
    if fin.get("leverage") is None:
        return False

    # Формируем SL: None или отрицательное
    sl_val = fin.get("sl")
    if sl_val is not None:
        try:
            sl_val = float(sl_val)
            fin["sl"] = sl_val if sl_val < 0 else -abs(sl_val)
        except:
            fin["sl"] = None
        if not validate_init_sl(fin.get("sl")):
            return False
    else:
        fin["sl"] = None

    if fin.get("sl_type") is None:
        return False
    if not fin.get("tp_order_volume") or fin.get("tp_order_volume") > 100:
        return False

    tp_cap_dep = fin.get("tp_levels", {})
    # Проверяем, что все диапазоны присутствуют и их длины одинаковы
    if any(rk not in tp_cap_dep for rk in RANGE_KEYS):
        return False
    # lengths = [len(tp_cap_dep[rk]) for rk in RANGE_KEYS]
    # if lengths and len(set(lengths)) > 1:
    #     return False
    # if lengths and any(l == 0 for l in lengths):
    #     return False

    # Перезаписываем второй элемент каждого кортежа tp_levels
    tp_volume = fin.get("tp_order_volume")
    if not tp_volume or tp_volume > 100:
        return False

    return True

def format_config(
    cfg: dict,
    indent: int = 0,
    target_key: str = None,
    alt_key: str = None,
    ex_key: str = None,
) -> str:
    lines = []
    pad = "  " * indent

    for k, v in cfg.items():
        # исключаем ключ
        if k == ex_key:
            continue

        # заменяем имя ключа
        display_key = alt_key if k == target_key else k

        if isinstance(v, dict):
            lines.append(f"{pad}• {display_key}:")
            lines.append(format_config(v, indent + 1, target_key, alt_key, ex_key))
        else:
            lines.append(f"{pad}• {display_key}: {v}")

    return "\n".join(lines)


class TelegramUserInterface:
    def __init__(self, bot: Bot, dp: Dispatcher,  context: BotContext, info_handler: ErrorHandler):
        self.bot = bot
        self.dp = dp  # aiogram v3
        self.context = context
        self.info_handler = info_handler
        self._polling_task: asyncio.Task | None = None
        self._stop_flag = False
        self.bot_iteration_lock = asyncio.Lock()

        # ===== Главное меню =====
        self.main_menu = types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="🛠 Настройки"), types.KeyboardButton(text="📊 Статус")],
                [types.KeyboardButton(text="▶️ Старт"), types.KeyboardButton(text="⏹ Стоп")]
            ],
            resize_keyboard=True,
            input_field_placeholder="Выберите действие…"
        )

        # ===== Регистрация хендлеров =====
        self.dp.message.register(self.start_handler, Command("start"))
        self.dp.message.register(self.settings_cmd, self._text_contains(["настройки"]))
        self.dp.message.register(self.status_cmd, self._text_contains(["статус"]))
        self.dp.message.register(self.start_cmd, self._text_contains(["старт"]))
        self.dp.message.register(self.stop_cmd, self._text_contains(["стоп"]))
        self.dp.message.register(self.text_message_handler, self._awaiting_input)

        # ===== Inline Callbacks =====
        self.dp.callback_query.register(self.settings_handler, F.data == "SETTINGS")
        self.dp.callback_query.register(self.mexc_settings_handler, F.data == "SET_MEXC")
        self.dp.callback_query.register(self.api_key_input, F.data == "SET_API_KEY")
        self.dp.callback_query.register(self.secret_key_input, F.data == "SET_SECRET_KEY")
        self.dp.callback_query.register(self.proxy_input, F.data == "SET_PROXY")
        self.dp.callback_query.register(self.uid_input, F.data == "SET_UID")
        self.dp.callback_query.register(self.fin_settings_handler, F.data == "SET_FIN")
        self.dp.callback_query.register(self.margin_size_input, F.data == "SET_MARGIN")
        self.dp.callback_query.register(self.margin_mode_input, F.data == "SET_MARGIN_MODE")
        self.dp.callback_query.register(self.leverage_input, F.data == "SET_LEVERAGE")
        self.dp.callback_query.register(self.sl_input, F.data == "SET_SL")
        self.dp.callback_query.register(self.sl_type_input, F.data == "SET_SL_TYPE")
        self.dp.callback_query.register(self.tp_levels_input, F.data == "SET_TP_LEVELS")
        self.dp.callback_query.register(self.tp_order_volume_input, F.data == "SET_TP_ORDER_VOLUME")
        for rk in RANGE_KEYS:
            self.dp.callback_query.register(self.tp_range_select, F.data == f"SET_TP_RANGE_{rk}")

        self.dp.callback_query.register(self.start_button, F.data == "START")
        self.dp.callback_query.register(self.stop_button, F.data == "STOP")

    # ===== Вспомогательные =====
    def _text_contains(self, keys: list[str]):
        def _f(message: types.Message) -> bool:
            if not message.text:
                return False
            txt = message.text.strip().lower()
            return any(k in txt for k in keys)
        return _f

    def _awaiting_input(self, message: types.Message) -> bool:
        chat_id = message.chat.id
        cfg = self.context.users_configs.get(chat_id)
        return bool(cfg and cfg.get("_await_field"))

    # ===== Keyboards =====
    def _settings_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔑 MEXC", callback_data="SET_MEXC")],
            [InlineKeyboardButton(text="💰 FIN SETTINGS", callback_data="SET_FIN")]
        ])

    def _mexc_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="API Key", callback_data="SET_API_KEY")],
            [InlineKeyboardButton(text="Secret Key", callback_data="SET_SECRET_KEY")],
            [InlineKeyboardButton(text="Proxy URL", callback_data="SET_PROXY")],
            [InlineKeyboardButton(text="User ID", callback_data="SET_UID")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="SETTINGS")]
        ])

    def _fin_keyboard(self) -> InlineKeyboardMarkup:
        kb = [
            [InlineKeyboardButton(text="Margin Size", callback_data="SET_MARGIN")],
            [InlineKeyboardButton(text="Margin Mode", callback_data="SET_MARGIN_MODE")],
            [InlineKeyboardButton(text="Leverage", callback_data="SET_LEVERAGE")],
            [InlineKeyboardButton(text="Stop Loss", callback_data="SET_SL")],
            [InlineKeyboardButton(text="SL Type", callback_data="SET_SL_TYPE")],
            [InlineKeyboardButton(text="TP Levels", callback_data="SET_TP_LEVELS")],
            [InlineKeyboardButton(text="TP Order Volume", callback_data="SET_TP_ORDER_VOLUME")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="SETTINGS")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=kb)    
    
    # ===== Вспомогательный метод =====
    def ensure_user_config(self, user_id: int):
        """Создаёт дефолтный конфиг для юзера, если его ещё нет в кэше."""
        if user_id not in self.context.users_configs:
            self.context.users_configs[user_id] = copy.deepcopy(INIT_USER_CONFIG)
            self.context.queues_msg[user_id] = []

    # ===== START / STATUS / STOP =====
    async def start_handler(self, message: types.Message):
        chat_id = message.chat.id
        self.ensure_user_config(chat_id)  # 🔹 гарантируем, что конфиг есть
        await message.answer("Добро пожаловать! Главное меню снизу 👇", reply_markup=self.main_menu)

    async def settings_cmd(self, message: types.Message):
        chat_id = message.chat.id
        self.ensure_user_config(chat_id)
        await message.answer("Выберите раздел настроек:", reply_markup=self._settings_keyboard())

    async def status_cmd(self, message: types.Message):
        chat_id = message.chat.id
        self.ensure_user_config(chat_id)
        cfg = self.context.users_configs[chat_id]

        status = "В работе" if getattr(self.context, "start_bot_iteration", False) else "Не активен"

        # Получаем конфиг
        full_cfg = cfg.get("config", {})

        # Фильтрация, сохраняя структуру
        filtered_cfg = {}
        for section, section_data in full_cfg.items():
            if isinstance(section_data, dict):
                filtered_section = {}
                for k, v in section_data.items():
                    # Убираем tp_levels_ только из fin_settings
                    if section == "fin_settings" and k.startswith("tp_levels_"):
                        continue
                    filtered_section[k] = v
                filtered_cfg[section] = filtered_section
            else:
                filtered_cfg[section] = section_data

        # Генерация текста настроек с сохранением структуры
        pretty_cfg = format_config(
            cfg=filtered_cfg,
            indent=0,
            target_key=None,
            alt_key=None,
            ex_key="tp_levels_gen"
        )

        await message.answer(
            f"📊 Текущий статус: {status}\n\n⚙ Настройки:\n{pretty_cfg}",
            reply_markup=self.main_menu
        )

    async def start_cmd(self, message: types.Message):
        chat_id = message.chat.id
        self.ensure_user_config(chat_id)

        async with self.bot_iteration_lock:
            # Если уже идёт итерация или есть открытые позиции
            if self.context.start_bot_iteration or any(
                pos.get("in_position", False)
                for symbol_data in self.context.position_vars.values()
                for side, pos in symbol_data.items()
                if side != "spec"
            ):
                await message.answer("Бот уже работает либо есть открытые позиции", reply_markup=self.main_menu)
                return

            cfg = self.context.users_configs[chat_id]
            if validate_user_config(cfg):
                self.context.start_bot_iteration = True
                self.context.stop_bot_iteration = False  # на всякий случай
                await message.answer("✅ Начало работы", reply_markup=self.main_menu)
            else:
                await message.answer("❗ Сначала настройте конфиг полностью", reply_markup=self.main_menu)

    async def stop_cmd(self, message: types.Message):
        chat_id = message.chat.id
        self.ensure_user_config(chat_id)

        async with self.bot_iteration_lock:
            # Если есть открытые позиции — стоп невозможен
            if any(
                pos.get("in_position", False)
                for symbol_data in self.context.position_vars.values()
                for side, pos in symbol_data.items()
                if side != "spec"
            ):
                await message.answer("Сперва закройте все позиции.", reply_markup=self.main_menu)
                return

            if self.context.start_bot_iteration:
                self.context.start_bot_iteration = False
                self.context.stop_bot_iteration = True
                # self.context.users_configs = {}  # сброс конфигов
                await message.answer("⛔ Торговля остановлена", reply_markup=self.main_menu)
            else:
                await message.answer("Данная опция недоступна, поскольку торговля еще не начата.", reply_markup=self.main_menu)

    # ===== HANDLERS ==========
    async def settings_handler(self, callback: types.CallbackQuery):
        user_id = callback.from_user.id
        self.ensure_user_config(user_id)
        await callback.answer()
        await callback.message.edit_text(
            "Выберите раздел настроек:", reply_markup=self._settings_keyboard()
        )

    async def mexc_settings_handler(self, callback: types.CallbackQuery):
        user_id = callback.from_user.id
        self.ensure_user_config(user_id)
        await callback.answer()
        await callback.message.edit_text(
            "Настройки MEXC:", reply_markup=self._mexc_keyboard()
        )

    async def fin_settings_handler(self, callback: types.CallbackQuery):
        user_id = callback.from_user.id
        self.ensure_user_config(user_id)
        await callback.answer()
        await callback.message.edit_text(
            "FIN SETTINGS:", reply_markup=self._fin_keyboard()
        )

    # ===== Обработка текстового ввода =====
    async def text_message_handler(self, message: types.Message):
        chat_id = message.chat.id
        self.ensure_user_config(chat_id)
        cfg = self.context.users_configs.get(chat_id)
        if not cfg or not cfg.get("_await_field"):
            return

        field_info = cfg["_await_field"]
        section, field = field_info["section"], field_info["field"]
        raw = (message.text or "").strip()
        fs = cfg["config"].setdefault(section, {})

        try:
            if section == "fin_settings":
                # TP Ranges
                if field.startswith("tp_levels_"):  # ключ для диапазона: tp_levels_0-500
                    range_key = field.replace("tp_levels_", "")
                    pairs = raw.split()
                    if not 1 <= len(pairs) <= 5:
                        await message.answer("Максимум 5 уровней!")
                        return

                    levels = []
                    for p in pairs:
                        if ":" not in p:
                            await message.answer("Неверный формат. Используйте 1:3 2:5 …")
                            return
                        k, v = p.split(":")
                        try:
                            levels.append(float(v))
                        except Exception:
                            await message.answer("Неверные числа. Используйте дробные значения.")
                            return

                    if not validate_tp_cap_dep_levels(levels):
                        await message.answer(
                            f"Ошибка: вводимые данные невалидные! "
                            f"Сейчас: {levels}"
                        )
                        return False

                    # === сохраняем в конфиг ===
                    fs["tp_levels"][range_key] = levels

                    # 🔑 тут сразу же обновляем дефолт динамически
                    cfg["config"]["fin_settings"]["tp_levels"][range_key] = levels

                    # # Проверка равенства длин
                    # lengths = [len(v) for v in fs["tp_levels"].values() if v]
                    # if lengths and len(set(lengths)) > 1:
                    #     await message.answer(
                    #         f"Ошибка: количество уровней должно быть одинаковым во всех диапазонах! "
                    #         f"Сейчас: {len(levels)}"
                    #     )
                    #     return

                # Margin / Leverage / TP Order Volume
                elif field in {"margin_size", "margin_mode", "leverage", "tp_order_volume"}:
                    try:
                        if field == "leverage":
                            val = int(raw)
                            if not val:
                                await message.answer("❗ Leverage должно быть положительным целым числом")
                                return
                            fs[field] = val
                        else:
                            fs[field] = float(raw.replace(",", "."))
                    except:
                        await message.answer("❗ Введите корректное число")
                        return

                # Stop Loss
                elif field == "sl":
                    # await message.answer("Введите основной Stop Loss в процентах.\nВведите 0 для отключения SL (None).")
                    try:
                        val = float(raw.replace(",", "."))
                        fs["sl"] = None if val == 0 else val
                    except:
                        await message.answer("❗ Введите корректное число для SL")
                        return

                # Stop Loss Type
                elif field == "sl_type":
                    if raw not in ("1", "2"):
                        await message.answer("Введите 1 для фиксированного, 2 для динамического SL")
                        return
                    fs["sl_type"] = int(raw)

            elif section == "MEXC":
                # API / Secret / Proxy / UID
                if field in {"api_key", "api_secret", "proxy_url", "u_id"}:
                    if field == "proxy_url":
                        raw_val = raw.strip()
                        if raw_val == "0":
                            fs["proxy_url"] = None
                        else:
                            fs["proxy_url"] = raw_val
                        # return  # не продолжаем дальше, чтобы значение не перезаписалось
                    else:
                        if not raw:
                            await message.answer("❗ Значение не может быть пустым")
                            return
                        fs[field] = raw

        except Exception as e:
            await message.answer(f"Ошибка: {e}")
            return

        cfg["_await_field"] = None
        await message.answer(f"✅ Значение для {field} сохранено!", reply_markup=self.main_menu)

        # Авто-запуск если конфиг валиден
        if validate_user_config(cfg):
            # self.context.start_bot_iteration = True
            await message.answer("✅ Конфиг полностью заполнен! Торговлю можно запускать.", reply_markup=self.main_menu)

    # ===== Callback для TP Ranges (0-500, 500-1000, 1000+) =====
    async def tp_range_select(self, callback: types.CallbackQuery):
        await callback.answer()
        user_id = callback.from_user.id
        self.ensure_user_config(user_id)

        rk = callback.data.replace("SET_TP_RANGE_", "")
        cfg = self.context.users_configs[user_id]

        # Просто указываем диапазон напрямую
        cfg["_await_field"] = {"section": "fin_settings", "field": f"tp_levels_{rk}"}
        await callback.message.answer(
            f"Введите уровни для диапазона {rk} в формате 1:3 2:5 3:7 … "
            "(максимум 5 уровней, пробел между парами, двоеточие внутри)."
        )

    # ========= INPUT PROMPTS =========
    async def api_key_input(self, callback: types.CallbackQuery):
        await callback.answer()
        user_id = callback.from_user.id
        self.ensure_user_config(user_id)

        if getattr(self.context, "start_bot_iteration", False):
            await callback.message.answer(
                "❗ Для замены API Key сначала остановите бота (СТОП), затем можно ввести новые ключи и запустить снова (СТАРТ).",
                reply_markup=self.main_menu
            )
            return

        self.context.users_configs[user_id]["_await_field"] = {"section": "MEXC", "field": "api_key"}
        await callback.message.answer("Введите API Key:")

    async def secret_key_input(self, callback: types.CallbackQuery):
        await callback.answer()
        user_id = callback.from_user.id
        self.ensure_user_config(user_id)

        if getattr(self.context, "start_bot_iteration", False):
            await callback.message.answer(
                "❗ Для замены Secret Key сначала остановите бота (СТОП), затем можно ввести новые ключи и запустить снова (СТАРТ).",
                reply_markup=self.main_menu
            )
            return

        self.context.users_configs[user_id]["_await_field"] = {"section": "MEXC", "field": "api_secret"}
        await callback.message.answer("Введите Secret Key:")

    async def proxy_input(self, callback: types.CallbackQuery):
        await callback.answer()
        user_id = callback.from_user.id
        self.ensure_user_config(user_id)

        if getattr(self.context, "start_bot_iteration", False):
            await callback.message.answer(
                "❗ Для замены Proxy URL сначала остановите бота (СТОП), затем можно ввести новые настройки и запустить снова (СТАРТ).",
                reply_markup=self.main_menu
            )
            return

        self.context.users_configs[user_id]["_await_field"] = {"section": "MEXC", "field": "proxy_url"}
        await callback.message.answer(
            "Введите Proxy URL в формате:\n"
            "http://PROXY_LOGIN:PROXY_PASSWORD@PROXY_ADDRESS:PROXY_PORT\n"
            "Или 0 для отключения прокси."
        )

    async def uid_input(self, callback: types.CallbackQuery):
        await callback.answer()
        user_id = callback.from_user.id
        self.ensure_user_config(user_id)

        if getattr(self.context, "start_bot_iteration", False):
            await callback.message.answer(
                "❗ Для замены User ID сначала остановите бота (СТОП), затем можно ввести новые настройки и запустить снова (СТАРТ).",
                reply_markup=self.main_menu
            )
            return

        self.context.users_configs[user_id]["_await_field"] = {"section": "MEXC", "field": "u_id"}
        await callback.message.answer("Введите User ID:")



    async def margin_size_input(self, callback: types.CallbackQuery):
        await callback.answer()
        user_id = callback.from_user.id
        self.ensure_user_config(user_id)

        self.context.users_configs[user_id]["_await_field"] = {"section": "fin_settings", "field": "margin_size"}
        await callback.message.answer("Введите Margin Size (число):")

    async def margin_mode_input(self, callback: types.CallbackQuery):
        await callback.answer()
        user_id = callback.from_user.id
        self.ensure_user_config(user_id)

        self.context.users_configs[user_id]["_await_field"] = {"section": "fin_settings", "field": "margin_mode"}
        await callback.message.answer("Введите Margin Mode (1 -- Изолированная, 2 -- Кросс):")

    async def leverage_input(self, callback: types.CallbackQuery):
        await callback.answer()
        user_id = callback.from_user.id
        self.ensure_user_config(user_id)

        self.context.users_configs[user_id]["_await_field"] = {"section": "fin_settings", "field": "leverage"}
        await callback.message.answer("Введите Leverage (число):")

    async def sl_input(self, callback: types.CallbackQuery):
        await callback.answer()
        user_id = callback.from_user.id
        self.ensure_user_config(user_id)

        self.context.users_configs[user_id]["_await_field"] = {"section": "fin_settings", "field": "sl"}
        await callback.message.answer("Введите основной Stop Loss в процентах (0 для отключения):")

    async def sl_type_input(self, callback: types.CallbackQuery):
        await callback.answer()
        user_id = callback.from_user.id
        self.ensure_user_config(user_id)

        self.context.users_configs[user_id]["_await_field"] = {"section": "fin_settings", "field": "sl_type"}
        await callback.message.answer("Введите тип Stop Loss: 1 – фиксированный, 2 – динамический:")

    async def tp_levels_input(self, callback: types.CallbackQuery):
        await callback.answer()
        user_id = callback.from_user.id
        self.ensure_user_config(user_id)

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=r, callback_data=f"SET_TP_RANGE_{r}")] for r in RANGE_KEYS
            ] + [[InlineKeyboardButton(text="⬅️ Назад", callback_data="SET_FIN")]]
        )
        await callback.message.edit_text("Выберите диапазон TP Levels:", reply_markup=keyboard)

    async def tp_order_volume_input(self, callback: types.CallbackQuery):
        await callback.answer()
        user_id = callback.from_user.id
        self.ensure_user_config(user_id)

        self.context.users_configs[user_id]["_await_field"] = {"section": "fin_settings", "field": "tp_order_volume"}
        await callback.message.answer("Введите TP Order Volume (число):")

    # ===== Совместимость inline START/STOP =====
    async def start_button(self, callback: types.CallbackQuery):
        await callback.answer()
        user_id = callback.from_user.id
        self.ensure_user_config(user_id)

        cfg = self.context.users_configs[user_id]
        if validate_user_config(cfg):
            self.context.start_bot_iteration = True
            await callback.message.answer("✅ Торговля запущена", reply_markup=self.main_menu)
        else:
            await callback.message.answer("❗ Сначала настройте конфиг полностью", reply_markup=self.main_menu)

    async def stop_button(self, callback: types.CallbackQuery):
        if any(
            pos.get("in_position", False)
            for symbol_data in self.context.position_vars.values()
            for side, pos in symbol_data.items()
            if side != "spec"
        ):
            await callback.message.answer("Сперва закройте все позиции.", reply_markup=self.main_menu)
            return
        user_id = callback.from_user.id
        self.ensure_user_config(user_id)

        if self.context.start_bot_iteration:
            self.context.start_bot_iteration = False
            self.context.stop_bot_iteration = True
            await callback.message.answer("⛔ Торговля остановлена", reply_markup=self.main_menu)
        else:
            await callback.message.answer("Это действие невозможно, так как торговля ещё не начата.", reply_markup=self.main_menu)

    # ===== Run / Stop =====
    async def run(self):
        self._polling_task = asyncio.create_task(
            self.dp.start_polling(self.bot, stop_signal=lambda: self._stop_flag)
        )
        await asyncio.sleep(0.1)

    async def stop(self):
        pass
        # self._stop_flag = True
        # if hasattr(self.bot, "session") and self.bot.session:
        #     await self.bot.session.close()
        # if self._polling_task:
        #     try:
        #         await self._polling_task
        #     except asyncio.CancelledError:
        #         pass
        #     self._polling_task = None
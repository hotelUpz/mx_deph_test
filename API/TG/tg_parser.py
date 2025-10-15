from a_config import *
from b_context import BotContext
from c_log import ErrorHandler, log_time
from typing import *
import re
from aiogram import Dispatcher, types


# Базовый словарь: пара символов (латиница, кириллица)
CHAR_PAIRS = {
    "a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "y": "у", "x": "х",
    "A": "А", "B": "В", "E": "Е", "K": "К", "M": "М", "H": "Н", "O": "О",
    "P": "Р", "C": "С", "T": "Т", "X": "Х",
}

LATIN_TO_CYR = CHAR_PAIRS
CYR_TO_LATIN = {v: k for k, v in CHAR_PAIRS.items()}


def normalize_text(text: str) -> str:
    """Приводим всё к латинице и lowercase"""
    res = []
    for ch in text:
        if ch in CYR_TO_LATIN:
            res.append(CYR_TO_LATIN[ch])  # кириллицу в латиницу
        else:
            res.append(ch)
    normalized = "".join(res).lower()
    # нормализуем скобки (китайские, корейские и пр.)
    normalized = normalized.replace("（", "(").replace("）", ")")
    return normalized


class TgParser:
    def __init__(self, info_handler: ErrorHandler):    
        info_handler.wrap_foreign_methods(self)
        self.info_handler = info_handler

    @staticmethod
    def to_float(raw_num: str) -> Optional[float]:
        raw_num = raw_num.strip().replace(" ", "")
        if not raw_num:
            return None

        if "," in raw_num and "." in raw_num:
            if raw_num.rfind(",") > raw_num.rfind("."):
                raw_num = raw_num.replace(".", "").replace(",", ".")
            else:
                raw_num = raw_num.replace(",", "")
        elif "," in raw_num:
            raw_num = raw_num.replace(",", ".")

        try:
            return float(raw_num)
        except ValueError:
            return None

    def parse_marketcap(self, line: str) -> Optional[float]:
        line_norm = normalize_text(line)

        # m_cap = re.search(
        #     r"marketcap\s*[:\-–]?\s*\$?\s*([\d.,\s]+)\s*([kmbм]?)",
        #     line_norm
        # )
        m_cap = re.search(
            r"(?:market\s*cap|marketcap|mcap|cap)\s*[:\-–]?\s*\$?\s*([\d.,\s]+)\s*([kmbм])?",
            line_norm
        )

        if not m_cap:
            return None

        num_str = m_cap.group(1)
        suffix = m_cap.group(2).lower()

        num = self.to_float(num_str)
        if num is None:
            return None

        if suffix == "k":
            num *= 1_000
        elif suffix in ("m", "м"):  # поддержим лат и кир
            num *= 1_000_000
        elif suffix == "b":
            num *= 1_000_000_000

        return num

    def parse_tg_message(self, message: str) -> Tuple[dict, bool]:
        text = normalize_text(message.strip())
        lines = [l.strip() for l in text.split("\n") if l.strip()]

        result = {"symbol": "", "cap": ""}

        for line in lines:
            # символ токена
            if not result["symbol"]:
                # m_symbol = re.search(r"\$([a-z0-9]+)\b", line)
                m_symbol = re.search(r"\$([a-z0-9]+)", line)
                if m_symbol:
                    result["symbol"] = m_symbol.group(1).upper() + "_USDT"

            # MarketCap
            if not result["cap"]:
                cap = self.parse_marketcap(line)
                if cap is not None:
                    result["cap"] = cap

            if all(v for v in result.values()):
                break

        all_present = all(v for v in result.values())
        return result, all_present


class TgBotWatcherAiogram(TgParser):
    """
    Отслеживает сообщения из канала через aiogram хендлеры.
    """

    def __init__(self, dp: Dispatcher, channel_id: int, context: BotContext, info_handler: ErrorHandler):
        super().__init__(info_handler)
        self.dp = dp
        self.channel_id = channel_id
        self.message_cache = context.message_cache
        self.stop_bot = context.stop_bot
        self._seen_messages: Set[int] = set()

    def register_handler(self, tag: str, max_cache: int = 20):
        """
        Регистрирует хендлер для прослушивания канала и фильтрации по тегу.
        """

        @self.dp.channel_post()
        async def channel_post_handler(message: types.Message):
            # print("Получено сообщение:", message.chat.id, message.text)
            try:
                # # # Проверяем ID канала
                # if message.chat.id != self.channel_id:
                #     return
                # print(message)

                # Проверяем, есть ли текст
                if not message.text:
                    print(f"Нет сообщений для парсигна либо права доступа ограничены. (Возможно апи ограничения). {log_time()}")
                    return

                # Проверяем тег
                if tag.lower() not in message.text.lower():
                    return

                ts_ms = int(message.date.timestamp() * 1000)
                # print(ts_ms)

                # Уникальность
                if ts_ms in self._seen_messages:
                    return

                self._seen_messages.add(ts_ms)
                self.message_cache.append((message.text, ts_ms))

                # Обрезаем кэш
                if len(self.message_cache) > max_cache:
                    self.message_cache = self.message_cache[-max_cache:]
                    self._seen_messages.clear()

                # print(f"[WATCHER] Новое сообщение с тегом {tag}: {message.text}")

            except Exception as e:
                self.info_handler.debug_error_notes(f"[watch_channel error] {e}", is_print=True)
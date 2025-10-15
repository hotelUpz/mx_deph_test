from typing import *
from a_config import *
from b_context import BotContext
from c_log import ErrorHandler, TZ_LOCATION
# import math
import random
from datetime import datetime
from decimal import Decimal, getcontext
import time
import asyncio
import pickle
import os
# import json

getcontext().prec = 28  # точность Decimal

def format_duration(ms: int) -> str:
    """
    Конвертирует миллисекундную разницу в формат "Xh Ym" или "Xm" или "Xs".
    :param ms: длительность в миллисекундах
    """
    if ms is None:
        return ""
    
    total_seconds = ms // 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    if hours > 0 and minutes > 0:
        return f"{hours}h {minutes}m"
    elif minutes > 0 and seconds > 0:
        return f"{minutes}m {seconds}s"
    elif minutes > 0:
        return f"{minutes}m"
    else:
        return f"{seconds}s"
    
def apply_slippage(price: float, slippage_pct: float, pos_side: str) -> float:
    """
    Корректирует цену закрытия с учётом проскальзывания.
    
    price: float - цена закрытия/текущая
    slippage_pct: float - проскальзывание в процентах (например 0.1 для 0.1%)
    pos_side: 'LONG' или 'SHORT'
    """
    if not (price and slippage_pct and pos_side):
        return price
    
    sign = 1 if pos_side.upper() == "LONG" else -1
    return price * (1 - sign * slippage_pct / 100)

def milliseconds_to_datetime(milliseconds):
    if milliseconds is None:
        return "N/A"
    try:
        ms = int(milliseconds)   # <-- приведение к int
        if milliseconds < 0: return "N/A"
    except (ValueError, TypeError):
        return "N/A"

    if ms > 1e10:  # похоже на миллисекунды
        seconds = ms / 1000
    else:
        seconds = ms

    dt = datetime.fromtimestamp(seconds, TZ_LOCATION)
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def to_human_digit(value):
    if value is None:
        return "N/A"
    getcontext().prec = PRECISION
    dec_value = Decimal(str(value)).normalize()
    if dec_value == dec_value.to_integral():
        return format(dec_value, 'f')
    else:
        return format(dec_value, 'f').rstrip('0').rstrip('.')  

def validate_tp_levels(levels: List[Tuple[float, float]]) -> None:
    percentages = [lvl[0] for lvl in levels]
    if any(p1 >= p2 for p1, p2 in zip(percentages, percentages[1:])):
        print(
            f"❌ Ошибка в TP_LEVELS: уровни должны идти строго по возрастанию.\n"
            f"   Сейчас: {percentages}\n"
            f"   👉 Проверь порядок процентов в настройках."
        )
        return False

    volumes_sum = sum(lvl[1] for lvl in levels)
    if volumes_sum != 100:
        print(
            f"❌ Ошибка в TP_LEVELS: сумма объёмов должна быть ровно 100%.\n"
            f"   Сейчас получилось: {volumes_sum}%\n"
            f"   👉 Поправь распределение, например: (50, 50) или (20, 30, 50)."
        )
        return False 
    return True

def validate_tp_cap_dep_levels(levels: list[float]) -> bool:
    """
    Проверяет, что уровни TP введены в порядке строгого возрастания.
    Один уровень всегда корректен.
    """
    if len(levels) <= 1:
        return True
    return all(levels[i] < levels[i + 1] for i in range(len(levels) - 1))


def validate_init_sl(value: Optional[Union[int, float]]) -> None:
    if value is None:
        return True

    if not isinstance(value, (int, float)):
        print(
            f"❌ Ошибка в INIT_SL: ожидается число (int/float) или None, "
            f"получено {type(value).__name__}"
        )
        return False

    if value >= 0:
        print(
            f"❌ Ошибка в INIT_SL: значение должно быть отрицательным числом.\n"
            f"   Сейчас: {value}"
        )
        return False
    return True

def validate_direction(direction: str) -> bool:
    if direction.upper() not in {"LONG", "SHORT"}:
        print(
            f"❌ Ошибка в DIRECTION: значение должно быть 'LONG' или 'SHORT'.\n"
            f"   Сейчас: {direction}"
        )
        return False
    return True

def calc_next_sl(
    entry_price: float,
    progress: int,
    base_sl: Optional[float],
    sl_type: int,
    tp_prices: List[float] | List[str],
    sign: int,
    price_precision: int
) -> float | None:
    if base_sl is None:
        return None
    
    # конвертируем строки в float
    tp_prices_float = [float(x) for x in tp_prices]
    levels = [entry_price] + tp_prices_float  # берем все TP уровни

    if sl_type == 1 or progress == 0:
        idx = min(progress, len(levels)-1)
        cur_entry = levels[idx]
        next_sl_price = cur_entry * (1 - (sign * abs(base_sl) / 100))
    elif sl_type == 2:
        idx = max(progress-1, 0)
        idx = min(idx, len(levels)-1)
        cur_entry = levels[idx]
        next_sl_price = cur_entry      
    else:
        return None

    return round(next_sl_price, price_precision)

def sleep_generator(tp_len):
    sleep_list = []

    for idx in range(1, tp_len + 1):
        # сколько раз уже прошло «2 ордера»
        steps = (idx - 1) // 2   # начиная с 3-го ордера будет 1, потом 2 и т.д.

        pause = BASE_PAUSE + INCREMENT * steps
        pause += random.uniform(0, NOISE)   # чуть шумим сверху
        sleep_list.append(pause)
        # print(f"#{idx}: sleep {pause:.2f} сек")

    return sorted(sleep_list)
        
def parse_range_key(rk: str) -> tuple[int, float]:
    """Преобразует строку диапазона в кортеж чисел (min, max)"""
    if '+' in rk:  # "1000+"
        min_val = int(rk.replace('+', '').strip()) * 1_000_000
        max_val = float('inf')
    else:
        parts = rk.split('-')
        min_val = int(parts[0].strip()) * 1_000_000
        max_val = int(parts[1].strip()) * 1_000_000
    return (min_val, max_val)

def tp_levels_generator(
        cap: float,
        tp_order_volume: float,
        tp_cap_dep: Dict[str, list[int]]
    ) -> list[tuple[float, float]]:

    for rk, percentages in tp_cap_dep.items():
        try:
            (min_cap, max_cap) = parse_range_key(rk)
        except:
            continue
        if min_cap <= cap <= max_cap:
            # print(f"min_cap <= cap <= max_cap: {min_cap}_{cap}_{max_cap}")
            return [(p, tp_order_volume) for p in percentages]
        
    return TP_LEVELS_DEFAULT

def safe_float(v, default=0.0, abs_val=False):
    try:
        val = float(v)
        return abs(val) if abs_val else val
    except (TypeError, ValueError):
        return default

def safe_int(v, default=0, abs_val=False):
    try:
        val = int(v)
        return abs(val) if abs_val else val
    except (TypeError, ValueError):
        return default

def safe_round(v, ndigits=2, default=0.0):
    try:
        return round(float(v), ndigits)
    except (TypeError, ValueError):
        return default


class Utils:
    def __init__(
            self,
            context: BotContext,
            info_handler: ErrorHandler,
            preform_message: Callable,
            get_realized_pnl: Callable,
            chat_id: str
        ):    
        info_handler.wrap_foreign_methods(self)

        self.context = context
        self.info_handler = info_handler    
        self.preform_message = preform_message   
        self.get_realized_pnl = get_realized_pnl
        self.chat_id = chat_id

    @staticmethod
    def parse_precision(symbols_info: list[dict], symbol: str) -> dict | None:
        """
        Возвращает настройки для qty, price и макс. плеча в виде словаря:
        {
            "contract_precision": int,
            "price_precision": int,
            "contract_size": float,
            "price_unit": float,
            "vol_unit": float,
            "max_leverage": int | None
        }
        Если символ не найден или данные пустые → None.
        """
        symbol_data = next((item for item in symbols_info if item.get("symbol") == symbol or item.get("baseCoinName") + f"_{QUOTE_ASSET}" == symbol), None)
        if not symbol_data:
            return None

        # обработка maxLeverage
        raw_leverage = symbol_data.get("maxLeverage")
        try:
            max_leverage = int(float(raw_leverage)) if raw_leverage is not None else None
        except (ValueError, TypeError):
            max_leverage = None

        return {
            "contract_precision": symbol_data.get("volScale", 3),
            "price_precision": symbol_data.get("priceScale", 2),
            "contract_size": float(symbol_data.get("contractSize", 1)),
            "price_unit": float(symbol_data.get("priceUnit", 0.01)),
            "vol_unit": float(symbol_data.get("volUnit", 1)),
            "max_leverage": max_leverage
        }
        
    def contract_calc(
        self,
        spec: dict,
        margin_size: float,
        entry_price: float,
        leverage: float,
        volume_rate: float,
        debug_label: str = None
    ) -> Optional[float]:
        """
        Рассчитывает количество контрактов (vol), которое надо передать в API MEXC.
        """
        contract_size = spec.get("contract_size")
        vol_unit = spec.get("vol_unit")
        contract_precision = spec.get("contract_precision")

        # проверка на валидность
        if any(not isinstance(x, (int, float)) for x in [margin_size, entry_price, leverage, contract_size]):
            self.info_handler.debug_error_notes(f"{debug_label}: Invalid input parameters in contract_calc")
            return None

        try:
            # сколько денег реально задействуем
            deal_amount = margin_size * volume_rate / 100

            # считаем объём в базовой валюте
            base_qty = (deal_amount * leverage) / entry_price

            # переводим в контракты
            raw_contracts = base_qty / contract_size

            # округляем по vol_unit
            contracts = round(raw_contracts / vol_unit) * vol_unit

            # окончательно ограничиваем precision
            contracts = round(contracts, contract_precision)

            return contracts
        except Exception as e:
            self.info_handler.debug_error_notes(f"{debug_label}: Error in contract_calc: {e}")
            return None        

    def contracts_template(
        self,
        symbol: str,
        pos_side: str,
        margin_size: float,   
        leverage: int,
        entry_price: float,
        symbol_data: dict,
        volume_rate: float,
        debug_label: str,
        key_label: str, # tp | sl
    ):
        """
        Темплейт под установку плеча, расчёт контрактов и размещение лимитного ордера с TP/SL.
        Имена параметров сохранены без изменений.
        """

        # === 2. Расчёт контрактов ===
        spec = symbol_data.get("spec", {})
        contracts = self.contract_calc(
            spec=spec,
            margin_size=margin_size,
            entry_price=float(entry_price),
            leverage=leverage,
            volume_rate=volume_rate,        
            debug_label=debug_label
        )

        if not contracts or contracts <= 0:
            failed_reason = f"{debug_label}: Invalid contracts calculated: {contracts}"
            order_failed_body = {
                "symbol": symbol,
                "pos_side": pos_side,
                "reason": failed_reason,
                "cur_time": int(time.time() * 1000),
            }
            self.preform_message(
                chat_id=self.chat_id,
                marker=f"{key_label}_order_failed",
                body=order_failed_body,
                is_print=True
            )
            return
        
        return contracts
    
# //////////////////////////        
    async def pnl_report(
        self,
        symbol: str,
        pos_side: str,
        pos_data: dict,
        cur_price: float,
        label: str
    ):
        cur_time = int(time.time() * 1000)
        start_time = pos_data.get("c_time")

        realized_pnl = await self.get_realized_pnl(
            symbol=symbol,
            direction=1 if pos_side == "LONG" else 2,
            start_time=start_time,
            end_time=cur_time
        )

        if realized_pnl is None:
            return
        
        pnl_usdt = realized_pnl.get("pnl_usdt")
        pnl_pct = realized_pnl.get("pnl_pct")
        time_in_deal = None
        if start_time:
            time_in_deal = cur_time - start_time

        body = {
            "symbol": symbol,
            "pnl_usdt": pnl_usdt,
            "pnl_pct": pnl_pct,
            "cur_time": cur_time,
            "time_in_deal": format_duration(time_in_deal),
        }

        self.preform_message(
            chat_id=self.chat_id,
            marker="report",
            body=body,
            is_print=True
        )


class FileManager:
    def __init__(self, info_handler: ErrorHandler):   
        info_handler.wrap_foreign_methods(self)
        self.info_handler = info_handler

    async def cache_exists(self, file_name="cache.pkl"):
        """Проверяет, существует ли файл и не пустой ли он."""
        return await asyncio.to_thread(lambda: os.path.isfile(file_name) and os.path.getsize(file_name) > 0)

    async def load_cache(self, file_name="cache.pkl"):
        """Читает данные из pickle-файла."""
        def _load():
            with open(file_name, "rb") as file:
                return pickle.load(file)
        try:
            return await asyncio.to_thread(_load)
        except (FileNotFoundError, EOFError):
            return {}
        except Exception as e:
            self.info_handler.debug_error_notes(f"Unexpected error while reading {file_name}: {e}")
            return {}        

    def _write_pickle(self, data, file_name):
        with open(file_name, "wb") as file:
            pickle.dump(data, file, protocol=pickle.HIGHEST_PROTOCOL)

    async def write_cache(self, data_dict, file_name="cache.pkl"):
        """Сохраняет данные в pickle-файл."""
        try:
            await asyncio.to_thread(self._write_pickle, data_dict, file_name)
        except Exception as e:
            self.info_handler.debug_error_notes(f"Error while caching data: {e}")
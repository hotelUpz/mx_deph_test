import aiohttp
import asyncio
import time
from typing import *
from b_context import BotContext
from c_log import ErrorHandler
from API.MX.mx import MexcClient
from c_utils import FileManager, to_human_digit, safe_float, safe_int, safe_round
from TRADING.exit import ExitControl
from copy import deepcopy   


class Synchronizer:
    def __init__(
        self,
        context: BotContext,
        info_handler: ErrorHandler,
        set_pos_defaults: Callable,
        pnl_report: Callable,
        mx_client: MexcClient,
        preform_message: Callable,
        positions_update_frequency: float,
        exit: ExitControl,
        use_cache: bool,
        chat_id: str
    ):
        self.info_handler = info_handler
        self.context = context
        self.set_pos_defaults = set_pos_defaults
        self.pnl_report = pnl_report
        self.mx_client = mx_client
        self.preform_message = preform_message
        self.positions_update_frequency = positions_update_frequency
        self.exit = exit
        self.use_cache = use_cache
        self._update_lock = asyncio.Lock()
        
        self.chat_id = chat_id        
        self._first_update_done = False

        info_handler.wrap_foreign_methods(self)

    async def reset_if_needed(self, pos_data: dict, symbol: str, pos_side: str):
        """Сбрасывает позицию, если она закрыта на бирже или форс-флаг"""
        # guard: если уже идет сброс — пропускаем
        if pos_data.get("_reset_in_progress"):
            self.info_handler.debug_error_notes(f"[reset_if_needed] already in progress for {symbol}/{pos_side}")
            return

        pos_data["_reset_in_progress"] = True
        try:
            label = f"{symbol}_{pos_side}"
            # cur_price = await self.mx_client.get_fair_price(symbol)

            if pos_data.get("in_position"):
                # debounce PnL: не слать дважды в короткий промежуток
                last_pnl_ts = pos_data.get("_last_pnl_ts", 0)
                now_ts = int(time.time() * 1000)
                # если последний отчет был менее 3 секунд назад — пропустить
                if now_ts - last_pnl_ts > 3000:
                    await asyncio.sleep(1)
                    await self.pnl_report(
                        symbol=symbol,
                        pos_side=pos_side,
                        pos_data=pos_data,
                        cur_price=None,
                        label=label
                    )
                    pos_data["_last_pnl_ts"] = now_ts
                else:
                    self.info_handler.debug_info_notes(f"[reset_if_needed] skip duplicate pnl for {symbol}/{pos_side}")

                # попытка закрыть остаток позиции
                await self.exit.exit_template(
                    symbol=symbol,
                    cur_price=None,
                    debug_label=label
                )

        finally:
            # сброс локальных данных
            self.set_pos_defaults(
                symbol=symbol,
                pos_side=pos_side,
                instruments_data=None,
                reset_flag=True
            )
            pos_data["_reset_in_progress"] = False

    @staticmethod
    def unpack_position_info(position: dict) -> dict:
        """Преобразует raw-позицию MEXC в нормализованный словарь"""
        if not isinstance(position, dict):
            return {
                "symbol": "",
                "pos_side": "",
                "contracts": 0.0,
                "hold_price": 0.0,
                "leverage": 1,
                # "c_time": 0.0
            }

        pos_type = safe_int(position.get("positionType"))
        pos_side = "LONG" if pos_type == 1 else "SHORT" if pos_type == 2 else ""
        symbol = str(position.get("symbol", "")).upper()

        return {
            "symbol": symbol,
            "pos_side": pos_side,
            "contracts": safe_float(position.get("holdVol"), 0.0, abs_val=True),
            "hold_price": safe_float(position.get("holdAvgPrice"), 0.0),
            "leverage": safe_int(position.get("leverage"), 1, abs_val=True),
            # "c_time": safe_float(position.get("cTime"), 0.0)
        }

    def update_active_position(
            self,
            symbol: str,
            symbol_data: dict,
            pos_side: str,
            info: dict
    ):
        """Обновляет локальные данные для активной позиции"""
        hold_price = safe_float(info.get("hold_price"))
        contracts = safe_float(info.get("contracts"))
        leverage = safe_int(info.get("leverage"), 1)

        pos_data = symbol_data.get(pos_side, {})

        if not pos_data.get("in_position"):
            cur_time = int(time.time() * 1000)
            pos_data["c_time"] = cur_time

            spec = symbol_data.get("spec", {})
            price_precision = safe_int(spec.get("price_precision"), 2)
            contract_size = safe_float(spec.get("contract_size"), 1.0)

            pos_data["entry_price"] = hold_price
            pos_data["vol_assets"] = contracts * contract_size

            sign = -1 if pos_side == "SHORT" else 1
            self.fin_settings = self.context.users_configs[self.chat_id]["config"]["fin_settings"]
            tp_price_levels = [
                hold_price * (1 + sign * safe_float(x[0]) / 100)
                for x in self.fin_settings.get("tp_levels_gen")
                if isinstance(x, (list, tuple)) and len(x) > 0
            ]

            sl_price = None
            
            if self.fin_settings.get("sl") is not None:
                sl_raw = hold_price * (1 - sign * abs(safe_float(self.fin_settings.get("sl"))) / 100)
                sl_price = to_human_digit(safe_round(sl_raw, price_precision))

            body = {
                "symbol": symbol,
                "leverage": leverage,
                "cur_time": cur_time,
                "entry_price": to_human_digit(safe_round(hold_price, price_precision)),
                "tp_price_levels": [to_human_digit(safe_round(x, price_precision)) for x in tp_price_levels],
                "cur_sl": sl_price
            }
            self.preform_message(
                chat_id=self.chat_id,
                marker="market_order_filled",
                body=body,
                is_print=True
            )

        pos_data.update({
            "hold_price": hold_price,
            "contracts": contracts,
            "in_position": True,
            "leverage": leverage
        })

    async def update_positions(self, target_symbols: Set[str], positions: List[Dict]):
        """Обновляет локальные позиции по данным с биржи"""
        # предотвращаем параллельный вход
        async with self._update_lock:
            try:
                active_positions = {}
                for position in positions or []:
                    if not position:
                        continue
                    info = self.unpack_position_info(position)
                    if info["symbol"] in target_symbols:
                        active_positions[(info["symbol"], info["pos_side"])] = info

                # проход по всем символам и позициям LONG/SHORT
                for symbol in target_symbols:
                    symbol_data = self.context.position_vars.get(symbol, {})
                    for pos_side in ("LONG", "SHORT"):
                        pos_data = symbol_data.get(pos_side, {})
                        if not pos_data:
                            continue

                        active_pos = active_positions.get((symbol, pos_side))
                        contracts = active_pos.get("contracts", 0.0) if active_pos else 0.0
                        force_reset_flag = pos_data.get("force_reset_flag", False)

                        if contracts > 0:
                            self.update_active_position(
                                symbol=symbol,
                                symbol_data=symbol_data,
                                pos_side=pos_side,
                                info=active_pos
                            )

                        if not contracts or force_reset_flag:
                            await self.reset_if_needed(
                                pos_data=pos_data,
                                symbol=symbol,
                                pos_side=pos_side
                            )

                if not self._first_update_done:
                    self._first_update_done = True
                    self.info_handler.debug_info_notes("[update_positions] First update done, flag set")

            except Exception as e:
                self.info_handler.debug_error_notes(f"[update_positions Error]: {e}")

    async def refresh_positions_state(self):
        """Обновляет позиции для всех стратегий"""
        try:
            symbols_set = set(self.context.position_vars.keys())
            if not symbols_set or not self.context.session or self.context.session.closed:
                return

            positions = await self.mx_client.fetch_positions()

            # print(positions)
            if positions is None or not isinstance(positions, list):
                self.info_handler.debug_error_notes("Empty positions response.")
                positions = []

            await self.update_positions(symbols_set, positions)

        except aiohttp.ClientError as e:
            self.info_handler.debug_error_notes(f"[HTTP Error] Failed to fetch positions: {e}")
        except Exception as e:
            self.info_handler.debug_error_notes(f"[Unexpected Error] Failed to refresh positions: {e}")

    async def positions_flow_manager(self, cache_file_manager: FileManager):
        """Цикл обновления позиций и синхронизации кэша"""
        # print("Цикл обновления позиций и синхронизации кэша")

        if self.use_cache and self.context.pos_loaded_cache:
            for symbol, data in self.context.pos_loaded_cache.items():
                self.context.position_vars[symbol] = deepcopy(data)
            self.context.pos_loaded_cache = None

        cache_update_interval = 5.0
        last_cache_time = time.monotonic()

        cycle = 0
        while not self.context.stop_bot and not self.context.stop_bot_iteration:
            cycle += 1
            await asyncio.sleep(self.positions_update_frequency)

            try:
                # print(f"[SYNC] → цикл {cycle}, refresh_positions_state() вызов")
                await self.refresh_positions_state()
                # print(f"[SYNC] ← цикл {cycle}, position_vars.keys: {list(self.context.position_vars.keys())}")

            except Exception as e:
                print(f"[SYNC][ERROR] refresh_positions_state: {e}")

            now = time.monotonic()

            # обновление кэша
            if self.use_cache and (now - last_cache_time >= cache_update_interval):
                try:
                    await cache_file_manager.write_cache(
                        data_dict=self.context.position_vars,
                        file_name="pos_cache.pkl"
                    )
                    # print(f"[SYNC] cache_file_manager.write_cache вызван (цикл {cycle})")
                except Exception as e:
                    print(f"[SYNC][ERROR] write_cache: {e}")
                last_cache_time = now

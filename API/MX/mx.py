import aiohttp
import functools
from aiohttp import ClientConnectionError, ClientConnectorError, ClientPayloadError
import asyncio
import time
from typing import *
from b_context import BotContext
from b_network import NetworkManager
from c_log import ErrorHandler
from .mx_bypass.mexcTypes import CreateOrderRequest, OpenType, OrderSide, OrderType, PositionMode, ExecuteCycle, TriggerPriceType, TriggerType, TriggerOrderRequest
from .mx_bypass.api import MexcFuturesAPI, ApiResponse


# BASE_URL_MEXC = "https://contract.mexc.com"
RETRY_DELAY = 1.5  # фиксированная пауза между попытками в секундах

# ----------------------------
def async_reconnector(debug: bool = True, stop_attr: str = None, stop_iter_attr: str = None, retry_exceptions=None):
    """Декоратор для методов MexcClient.
    retry_exceptions — список исключений, при которых делаем retry (по умолчанию сетевые ошибки)
    """
    if retry_exceptions is None:
        retry_exceptions = (
            ClientConnectionError,
            # ClientConnectorError,
            # ClientPayloadError,
            asyncio.TimeoutError,
            # aiohttp.ClientError
        )

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            self: MexcClient = args[0]
            attempt = 0
            while not getattr(self, stop_attr, False) and not getattr(self, stop_iter_attr, False):
                attempt += 1
                try:
                    result = await func(*args, **kwargs)
                    return result  # любая нормальная отдача или None вернется сразу
                except retry_exceptions as e:
                    if debug:
                        print(f"❌ [{func.__name__}] Network error: {e} → retry in {RETRY_DELAY}s (attempt {attempt})")
                    # пересоздаём сессию при сетевой ошибке
                    if not self.session or self.session.closed:
                        await self.connector.initialize_session()

                    await asyncio.sleep(RETRY_DELAY)
                    continue
                except Exception as e:
                    if debug:
                        print(f"⚠️ [{func.__name__}] Other error: {e} → returning result without retry")
                    # Не ретраим, возвращаем None или как было
                    return None
        return wrapper
    return decorator


# ----------------------------
class MexcClient:
    def __init__(
            self,
            context: BotContext,
            connector: NetworkManager,
            info_handler: ErrorHandler,
            api_key: str = None,
            api_secret: str = None,
            token: str = None,
        ):      
        self.session: Optional[aiohttp.ClientSession] = context.session
        self.info_handler = info_handler

        self.api_key, self.api_secret = api_key, api_secret 
        self.stop_bot = context.stop_bot
        self.stop_bot_iteration = context.stop_bot_iteration
        self.bloc_async = context.bloc_async
        self.connector = connector
        # info_handler.wrap_foreign_methods(self) # - eval так как перебьет декоратор реконекта

        self.api = MexcFuturesAPI(token, testnet=False)

    # ----------------------------
    # Публичные методы
    @async_reconnector(debug=True, stop_attr="stop_bot", stop_iter_attr="stop_bot_iteration")
    async def get_instruments(self, session: Optional[aiohttp.ClientSession] = None) -> Optional[List[Dict]]:
        response = await self.api.get_instruments(session)
        if response and response.success and response.data:
            return response.data  # возвращаем сразу список инструментов
        return None

    @async_reconnector(debug=True, stop_attr="stop_bot", stop_iter_attr="stop_bot_iteration")
    async def get_fair_price(self, symbol: str, session: Optional[aiohttp.ClientSession] = None) -> Optional[float]:
        response = await self.api.get_fair_price(symbol, session)
        if response and response.success and response.data and "fairPrice" in response.data:
            return float(response.data["fairPrice"])
        return None

    # ----------------------------
    # Приватные методы
    @async_reconnector(debug=True, stop_attr="stop_bot", stop_iter_attr="stop_bot_iteration")
    async def fetch_positions(self):
        # path = "/private/position/open_positions"
        response = await self.api.get_open_positions(symbol=None, session=self.session)
        if response and getattr(response, "success", False) and response.data:
            return response.data
        return []

    # /////
    async def get_realized_pnl(
        self,
        symbol: str,
        start_time: Optional[int],
        end_time: Optional[int],
        direction: Optional[int] = None  # 1=LONG, 2=SHORT
    ) -> dict:
        """
        Считает реализованный PnL за период по символу.
        Возвращает словарь:
            {"pnl_usdt": float, "pnl_pct": float}
        """
        try:
            rows = await self.get_futures_statement(symbol=symbol)
            if not rows:
                return {"pnl_usdt": 0.0, "pnl_pct": 0.0}
        except Exception as e:
            self.info_handler.debug_error_notes(
                f"[get_realized_pnl] error fetching data: {e}", is_print=True
            )
            return {"pnl_usdt": 0.0, "pnl_pct": 0.0}

        pnl_usdt = 0.0
        pnl_pct = 0.0

        filter_flag = False

        try:

            for row in rows:
                try:
                    # фильтрация по времени
                    ts = int(row.get("updateTime", 0))  # используем updateTime
                    # print(ts - start_time)
                    # print(ts > start_time)
                    if start_time and ts < start_time:
                        continue

                    # фильтрация по направлению
                    if direction and row.get("positionType") != direction:
                        continue

                    # суммируем реализованный PnL
                    pnl_usdt += float(row.get("realised", 0.0))
                    # print(f"pnl_usdt: {pnl_usdt}")

                    # суммируем проценты
                    if row.get("profitRatio") is not None:
                        pnl_pct += float(row["profitRatio"]) * 100

                    filter_flag = True

                except Exception:
                    continue

        
        finally:
            if not filter_flag:
                return {
                    "pnl_usdt": None,
                    "pnl_pct": None,  # уже %
                }
        
            return {
                "pnl_usdt": round(pnl_usdt, 6),
                "pnl_pct": round(pnl_pct, 4),  # уже %
            }

    
    @async_reconnector(debug=True, stop_attr="stop_bot", stop_iter_attr="stop_bot_iteration")
    async def get_futures_statement(
        self,
        symbol: Optional[str] = None,
        # page_num: int = 1,
        # page_size: int = 20
    ):
        response = await self.api.get_historical_orders_report(
            symbol=symbol,
            session=self.session
        )
        # print(response)
        if response and getattr(response, "success", False) and response.data:
            return response.data
        return []      

    ########################## BYPASS WAY ###############################      
    @async_reconnector(debug=True, stop_attr="stop_bot", stop_iter_attr="stop_bot_iteration")        
    async def set_hedge_mode(
            self,
            pos_mode: int = 1
        ):
        # Hedge = 1
        # OneWay = 2       
        await self.api.change_position_mode(position_mode=PositionMode(pos_mode), session=self.session)

    @async_reconnector(debug=True, stop_attr="stop_bot", stop_iter_attr="stop_bot_iteration")
    async def make_order(
        self,
        symbol: str,
        contract: float,
        side: str,
        position_side: str,
        leverage: int,
        open_type: str,
        debug_price: float = None,
        price: Optional[float] = None,
        stopLossPrice: Optional[float] = None,
        takeProfitPrice: Optional[float] = None,
        market_type: str = "MARKET",        
        debug: bool = True   # флаг дебага
    ) -> ApiResponse[int]:

        if market_type == "MARKET":
            market_type = OrderType.MarketOrder 
        elif market_type == "LIMIT":
            market_type = OrderType.PriceLimited
        else:
            print(f"Параметр market_type должен быть MARKET или LIMIT")
            return
        
        if position_side.upper() == "LONG":
            side = OrderSide.OpenLong if side.upper() == "BUY" else OrderSide.CloseLong
        elif position_side.upper() == "SHORT":
            side = OrderSide.OpenShort if side.upper() == "BUY" else OrderSide.CloseShort  
        else:
            print(f"Параметр position_side not in [LONG, SHORT]")
            return

        if open_type == 1:
            open_type = OpenType.Isolated
        elif open_type == 2:
            open_type = OpenType.Cross
        else:
            print(f"Параметр open_type not in [1, 2]")
            return
        
        if open_type == OpenType.Isolated and not leverage:
            print(f"Параметр leverage обязателен при ISOLATED open_type")
            return
        
        # if debug:
        #     print(
        #         f"--- INFO: make_order ---\n"
        #         f"symbol:        {symbol}\n"
        #         f"approximate price:  {debug_price}\n"
        #         f"contract:      {contract}\n"
        #         f"side:          {side}\n"
        #         f"position_side: {position_side}\n"
        #         f"leverage:      {leverage}\n"
        #         f"open_type:     {open_type}\n"
        #         f"market_type:   {market_type}\n"
        #         f"-------------------------\n"
        #     )

        return await self.api.create_order(
            order_request=CreateOrderRequest(
                symbol=symbol,
                side=side,
                vol=contract,
                leverage=leverage,
                openType=open_type,
                type=market_type,
                price=price,
                stopLossPrice=stopLossPrice,
                takeProfitPrice=takeProfitPrice
            ),
            session=self.session
        )

    @async_reconnector(debug=True, stop_attr="stop_bot", stop_iter_attr="stop_bot_iteration")        
    async def cancel_all_orders(
        self,
        symbol: str,
        debug: bool = True
    ) -> ApiResponse[int]:
        """
        Отмена триггерных ордеров по их orderId.
        :param order_id_list: список ID ордеров
        :param symbol:        символ ордера (обязательно)
        :param debug:         печатать отладочную информацию
        """
        # if debug:
        #     print(
        #         f"--- INFO: cancel_all_order ---\n"
        #         f"symbol:     {symbol}\n"
        #         f"----------------------------\n"
        #     )

        # формируем список словарей для API
        return await self.api.cancel_all_orders(symbol=symbol, session=self.session) 
    
    @async_reconnector(debug=True, stop_attr="stop_bot", stop_iter_attr="stop_bot_iteration")       
    async def cancel_order(
        self,
        order_id_list: List[str],
        symbol: str,
        debug: bool = True
    ) -> ApiResponse[int]:
        """
        Отмена триггерных ордеров по их orderId.
        :param order_id_list: список ID ордеров
        :param symbol:        символ ордера (обязательно)
        :param debug:         печатать отладочную информацию
        """
        # if debug:
        #     print(
        #         f"--- INFO: cancel_order ---\n"
        #         f"symbol:     {symbol}\n"
        #         f"order_ids:  {order_id_list}\n"
        #         f"----------------------------\n"
        #     )

        # формируем список словарей для API
        order_list = [{"orderId": oid, "symbol": symbol} for oid in order_id_list]
        return await self.api.cancel_trigger_orders(orders=order_list, session=self.session) 

    async def cancel_order_template(
            self,
            symbol: str,
            pos_data: Dict,
            key_list: List
        ) -> None:
        """
        Отменяет текущий алгоритмический ордер, если он существует (order_id).
        После попытки отмены сбрасывает pos_data['order_id'] в None.
        """
        order_id_list = []
        for order_label in key_list:      # order_label in ["tp", "sl"]
            order_id = pos_data.get(f"{order_label}_id", None)
            if order_id:
                # print(f"if order_id: {order_id[0]}")
                order_id_list.append(order_id[0])

        try:
            if order_id_list:
                cancel_resp = await self.cancel_order(
                    order_id_list=order_id_list,
                    symbol=symbol
                )
                self.info_handler.debug_info_notes(
                    f"[INFO] Order {order_id_list} cancelled for {symbol}: {cancel_resp}", is_print=True
                )
        except Exception as e:
            self.info_handler.debug_error_notes(
                f"[ERROR] Failed to cancel order {order_id_list} for {symbol}: {e}", is_print=True
            )
        finally:
            for order_label in key_list:
                try:
                    # async with self.bloc_async:
                    pos_data[f"{order_label}_id"] = None
                except Exception:
                    pass

    @async_reconnector(debug=True, stop_attr="stop_bot", stop_iter_attr="stop_bot_iteration")
    async def create_stop_loss_take_profit(
        self,
        symbol: str,
        position_side: str,
        contract: float,
        price: float,
        leverage: int,
        open_type: int,
        close_order_type: str, # "tp" | "sl"
        order_type: int = 1,      # 1 -- по маркету, 2 -- лимиткой
        debug: bool = False
    ) -> ApiResponse[int]:
        
        """
        Универсальный выход (SL/TP) по рынку.
        - is_take_profit=False -> стоп-лосс
        - is_take_profit=True  -> тейк-профит

        Логика условий:
        CloseLong:
            SL -> price <= trigger  (LessThanOrEqual)
            TP -> price >= trigger  (GreaterThanOrEqual)
        CloseShort:
            SL -> price >= trigger  (GreaterThanOrEqual)
            TP -> price <= trigger  (LessThanOrEqual)
        """

        if position_side.upper() == "LONG":
            side = OrderSide.CloseLong
        elif position_side.upper() == "SHORT":
            side = OrderSide.CloseShort
        else:
            print(f"Параметр position_side not in [LONG, SHORT]")
            return
        
        if close_order_type not in ("tp", "sl"):
            print("close_order_type must be 'tp' or 'sl' for exit order")
            return

        if close_order_type == "tp":
            trigger_type = (
                TriggerType.GreaterThanOrEqual if side == OrderSide.CloseLong
                else TriggerType.LessThanOrEqual
            )
        else:
            trigger_type = (
                TriggerType.LessThanOrEqual if side == OrderSide.CloseLong
                else TriggerType.GreaterThanOrEqual
            )

        if open_type == 1:
            open_type = OpenType.Isolated
        elif open_type == 2:
            open_type = OpenType.Cross
        else:
            print(f"Параметр open_type not in [1, 2]")
            return
        
        if open_type == OpenType.Isolated and not leverage:
            print(f"Параметр leverage обязателен при ISOLATED open_type")
            return
        
        if order_type == 1:
            orderType = OrderType.MarketOrder
        else:
            print(f"Параметр order_type != 1 (create_stop_loss_take_profit).")
            return

        # if debug:
        #     print(
        #         f"--- INFO: create_stop_loss_take_profit ---\n"
        #         f"symbol:          {symbol}\n"
        #         f"position_side:   {position_side}\n"
        #         f"close_order_type:{close_order_type}\n"
        #         f"side:            {side}\n"
        #         f"contract:        {contract}\n"
        #         f"price:           {price}\n"
        #         f"trigger_type:    {trigger_type}\n"
        #         f"open_type:       {open_type}\n"
        #         f"leverage:        {leverage}\n"
        #         f"------------------------------------------\n"
        #     )

        trigger_request = TriggerOrderRequest(
            symbol=symbol,
            side=side,
            vol=contract,                      # количество (в контрактах)
            leverage=leverage,
            openType=open_type,
            orderType=orderType,
            executeCycle=ExecuteCycle.UntilCanceled,
            trend=TriggerPriceType.LatestPrice,
            triggerPrice=price,
            triggerType=trigger_type,
        )
        return await self.api.create_trigger_order(trigger_order_request=trigger_request, session=self.session)
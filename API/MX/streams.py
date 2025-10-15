import asyncio
from typing import *
import aiohttp
import hmac
import hashlib
import time
import json
import random
import warnings
warnings.filterwarnings("ignore", category=UserWarning)
from a_config import QUOTE_ASSET
from c_log import ErrorHandler
from b_context import BotContext


# --------------------------------------------------
class MxFuturesOrderWS:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        context: BotContext,
        info_handler: ErrorHandler,
        proxy_url: str = None,
        ws_url: str = "wss://contract.mexc.com/edge"
    ):
        """
        Класс для стрима ордеров MEXC Futures с твоими ключами
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.context = context
        self.info_handler = info_handler

        self.ws_url = ws_url
        self.proxy_url = proxy_url
        self.websocket = None
        self.session = None
        self.is_connected = False
        self.is_running = False
        self.callback = None
        self.reconnect_attempts = 0
        self.ping_interval = 10
        self.ping_task = None
        self.stop_bot = getattr(context, "stop_bot", False)

        self.state_map = {1: "Uninformed", 2: "Uncompleted", 3: "Completed", 4: "Cancelled", 5: "Invalid"}
        self.side_map = {1: "Open Long", 2: "Close Short", 3: "Open Short", 4: "Close Long"}

        info_handler.wrap_foreign_methods(self) if hasattr(info_handler, "wrap_foreign_methods") else None

    def generate_signature(self, timestamp: int) -> str:
        signature_string = f"{self.api_key}{timestamp}"
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            signature_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature

    async def send_ping(self):
        if self.websocket and self.is_connected:
            try:
                await self.websocket.send_json({"method": "ping"})
            except Exception as e:
                self.info_handler.debug_error_notes(f"Ping failed: {e}")

    async def ping_loop(self):
        while self.is_running and self.is_connected and not self.stop_bot and not self.context.stop_bot_iteration:
            await self.send_ping()
            await asyncio.sleep(self.ping_interval)

    async def connect_websocket(self) -> bool:
        try:
            if not self.session or self.session.closed:
                self.session = aiohttp.ClientSession()

            self.websocket = await self.session.ws_connect(
                self.ws_url,
                proxy=self.proxy_url,
                autoping=False
            )
            self.is_connected = True
            self.info_handler.debug_error_notes("WebSocket connected successfully")
            return True
        except Exception as e:
            self.info_handler.debug_error_notes(f"WebSocket connection failed: {e}")
            self.is_connected = False
            return False

    async def login(self) -> bool:
        timestamp = int(time.time() * 1000)
        signature = self.generate_signature(timestamp)
        login_payload = {
            "method": "login",
            "param": {
                "apiKey": self.api_key,
                "reqTime": timestamp,
                "signature": signature
            }
        }
        try:
            await self.websocket.send_json(login_payload)
            response = await asyncio.wait_for(self.websocket.receive(), timeout=10.0)
            data = json.loads(response.data)
            if data.get("channel") == "rs.login" and data.get("data") == "success":
                self.info_handler.debug_info_notes("Login successful")
                return True
            else:
                self.info_handler.debug_error_notes(f"Login failed: {data}")
                return False
        except asyncio.TimeoutError:
            self.info_handler.debug_error_notes("Login timeout - no response received")
            return False

    async def subscribe_to_orders(self) -> bool:
        subscribe_payload = {
            "method": "sub.personal",
            "param": {"channel": "push.personal.order"}
        }
        await self.websocket.send_json(subscribe_payload)
        self.info_handler.debug_info_notes("Subscribed to order channel")
        return True

    async def authenticate_and_subscribe(self) -> bool:
        if not await self.login():
            self.info_handler.debug_error_notes("Authentication failed")
            return False
        if not await self.subscribe_to_orders():
            self.info_handler.debug_error_notes("Subscription failed")
            return False
        return True

    async def handle_messages(self):
        async for msg in self.websocket:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except Exception as e:
                    self.info_handler.debug_error_notes(f"JSON parse error: {e}")
                    continue

                if data.get("method") == "ping":
                    await self.websocket.send_json({"method": "pong"})
                    continue

                if data.get("channel") == "push.personal.order":
                    await self.parse_msg(data.get("data", {}))
                    continue

                if data.get("channel") == "rs.error":
                    self.info_handler.debug_error_notes(f"Error: {data}")
                    continue

            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                self.info_handler.debug_error_notes("WebSocket closed")
                self.is_connected = False
                return

    async def reconnect(self):
        self.reconnect_attempts += 1
        delay = random.uniform(0.8, 1.2)
        self.info_handler.debug_error_notes(f"Reconnecting attempt {self.reconnect_attempts} in {delay:.1f}s")
        await asyncio.sleep(delay)

    async def disconnect(self):
        try:
            if self.websocket:
                await self.websocket.close()
                self.info_handler.debug_error_notes("WebSocket disconnected")
            if self.session:
                await self.session.close()
                self.info_handler.debug_error_notes("Client session closed")
        except Exception as e:
            self.info_handler.debug_error_notes(f"Error during disconnect: {e}")
        finally:
            self.is_connected = False
            self.websocket = None
            self.session = None

    def stop(self):
        self.is_running = False
        self.info_handler.debug_error_notes("Stopping stream...")

    async def start(self, debug: bool = True):
        self.is_running = True
        if debug:
            self.info_handler.debug_info_notes("🚀 Starting MEXC Futures order stream...")

        while self.is_running and not self.stop_bot and not self.context.stop_bot_iteration:
            try:
                if not await self.connect_websocket():
                    await asyncio.sleep(1)
                    continue

                if not await self.authenticate_and_subscribe():
                    await self.disconnect()
                    await asyncio.sleep(1)
                    continue

                # старт фонового ping_task
                if self.ping_task and not self.ping_task.done():
                    self.ping_task.cancel()
                self.ping_task = asyncio.create_task(self.ping_loop())

                self.context.orders_updated_event.set()
                await self.handle_messages()

            except Exception as e:
                self.info_handler.debug_error_notes(f"Loop error: {e}")

            # отменяем ping_task при разрыве
            if self.ping_task:
                self.ping_task.cancel()

            if self.is_running:
                await self.reconnect()


    async def parse_msg(
            self,
            msg: Dict[str, Any],
            debug: bool = True
        ):
        """
        Обработчик ордеров - выводим только активные лимитные ордера
        """  
        def normalize_symbol(raw_symbol: str) -> str:
            if not raw_symbol:
                return ""
            cleaned = raw_symbol.upper().replace("_", "").replace("-", "").replace(" ", "")
            return cleaned.replace(QUOTE_ASSET, f"_{QUOTE_ASSET}")            

        order_symbol = normalize_symbol(msg.get("symbol", ""))
        order_pos_side = (
            "LONG" if msg.get("side") in {1, 4}
            else "SHORT" if msg.get("side") in {2, 3}
            else "UNKNOWN"
        )

        if (
            order_symbol not in self.context.position_vars or
            order_pos_side not in self.context.position_vars[order_symbol]
        ):
            return

        order_id = msg.get("orderId")
        category = msg.get("category")

        pos_data = self.context.position_vars[order_symbol][order_pos_side]
        order_data = pos_data["order_stream_data"]

        if category == 1:  # лимитные ордера                         
            # async with self.context.bloc_async:
            order_data.setdefault(order_id, {})
            order_data[order_id].update({"state": msg.get('state')})
            pos_data["pending"] = True
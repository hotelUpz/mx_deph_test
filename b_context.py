import asyncio
import aiohttp
from typing import *

class BotContext:
    def __init__(self):
        """ Инициализируем глобальные структуры"""
        # //
        self.message_cache: list = []  # основной кеш сообщений
        self.tg_timing_cache: set = set()
        self.stop_bot: bool = False
        self.start_bot_iteration = False 
        self.stop_bot_iteration = False
        self.config_ready = False
        # //
        self.pos_loaded_cache = None
        self.instruments_data: dict = None
        self.position_vars: dict = {}
        self.order_stream_data: dict = {}
        self.users_configs: Dict = {} # --
        self.queues_msg: Dict = {}
        self.session: Optional[aiohttp.ClientSession] = None
        self.position_updated_event = asyncio.Event()
        self.orders_updated_event = asyncio.Event()
        self.bloc_async = asyncio.Lock()
        self.signal_locks: dict = {}
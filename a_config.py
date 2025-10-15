from typing import *

# --- CORE ---   
SYMBOL: str = None   # "OP" | другой токен | None -- будет брать из сообщения  

# --------- RISK ---------------
QUOTE_ASSET: str = "USDT"
DIRECTION: str = "LONG"
TEG_ANCHOR: str = "UPBIT LISTING"              

# TG_BOT_TOKEN: str = "7976740718:AAE1xBujUM26JfvefRr1hkcA12yfUC9e9qk" # bot token
# TG_GROUP_ID: str = "-1002653345160" # id группы откуда парсить сигнал
# TG_BOT_TOKEN: str = "8320057395:AAFD1Zing02A9q5RKrnUw3SEaQbyg8UPU7c" # -- токен бота (real my)
TG_BOT_TOKEN: str = "8112036801:AAHOVza_DoL7gFwJiITpXIdZRi8yOMtmihI" # -- токен бота (test)
TG_GROUP_ID: str = "-1003053085303" # -- id группы откуда парсить сигнал (test)
# 
# //
CAP_MULTIPLITER_TRUE: bool = False           # задействуем механизм зависомостей капы от множителя размера
MULTIPLITER_TYPE: int = 1                    # 1-- увеличиваем за счет маржи; 2 -- за счет плеча
CAP_DEP: dict = {                        
    (0, 500_000): 1.0,   
    (500_000, 1_000_000): 2.0,
    (1_000_000, float('inf')): 3.0,
}

# -- UTILS ---
TIME_ZONE: str = "UTC"
SLIPPAGE_PCT: float = 0.05 # % -------------------- поправка для расчетов PnL
SIGNAL_TIMEOUT: float = 10 # sec ------------------ время в течение которого сиглал актуален
PRECISION: int = 28 # ------------------------------точность расчетов decimal (нужно для особо малых чисел)

# --- SYSTEM ---
TG_UPDATE_FREQUENCY: float = 1.0 # sec
POSITIONS_UPDATE_FREQUENCY: float = 1.0 # sec
MAIN_CYCLE_FREQUENCY: float = 1.0 # sec
TP_CONTROL_FREQUENCY: float = 0.25 # sec
SIGNAL_PROCESSING_LIMIT: int = 5 # ----------------- ограничивает количество одновременной обработки сигналов
PING_URL = "https://contract.mexc.com/api/v1/contract/ping"
PING_INTERVAL = 10  # сек # -- ping сессии. Дергаем для контроля и оживления
USE_CACHE = False  # для деплоя лучше False

# /
# ----- параметры сонной паузы между установкой лимитных ордеров ------------
BASE_PAUSE = 1.0           # стартовая пауза
NOISE = 0.5                # рандомная добавка от 0 до NOISE
INCREMENT = 0.5            # прибавка каждые 2 ордера

# --- STYLES ---
HEAD_WIDTH = 35
HEAD_LINE_TYPE = "" #  либо "_"
FOOTER_WIDTH = 35
FOOTER_LINE_TYPE = "" #  либо "_"
EMO_SUCCESS = "🟢"
EMO_LOSE = "🔴"
EMO_ZERO = "⚪"
EMO_ORDER_FILLED = "🤞"


# ------- BUTTON DEFAULT SETTINGS ------
RANGE_KEYS = ["0-500", "500-1000", "1000+"]
TP_LEVELS_DEFAULT: List[Tuple[float, float]] = [  # дефолтная линейка тейк-профитов (процент , объём в %)
    (3, 20),
    (7, 20),
    (10, 20),
    (15, 20),
    (20, 20)
]
INIT_USER_CONFIG = {
    "config": {
        "MEXC": {
            # "proxy_url": "http://zmEnP8Af:F7i34xHB@45.10.108.116:64762", # формат: http://zmEnP8Af:F7i34xHB@45.10.108.116:64762  (логин-пароль-адрес-порт)
            "proxy_url": None,
            # "api_key": "",
            # "api_secret": "",
            # "u_id": "",
            "api_key": "mx0vgl1CFdYPrISo5p",
            "api_secret": "e84e7fdd1ffd46cf92f901e46dd3f1d2",
            "u_id": "WEB695592e91afa7437c737c52830a5395f82622852ea60c68d5d88fb644f43940b",
        },
        "fin_settings": {
            "margin_size": 11,
            "margin_mode": 2,
            "leverage": 5,
            "sl": -20,
            "sl_type": 2,                                   
            "tp_levels": {"0-500": [10, 25, 50, 75, 100],
                "500-1000": [5, 10, 15, 20, 30],
                "1000+": [3, 7, 10, 15, 20],
            },
            "tp_levels_gen": [x for x in TP_LEVELS_DEFAULT.copy() if x],
            "tp_order_volume": 20
    }
    },
    "_await_field": None # system
}
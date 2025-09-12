from a_config import MULTIPLITER_TYPE, CAP_DEP, CAP_MULTIPLITER_TRUE
from .valide import OrderValidator
from b_context import BotContext
from c_log import ErrorHandler
from c_utils import Utils
import time
from typing import Callable
from API.MX.mx import MexcClient



class EntryControl:
    def __init__(
        self,
        context: BotContext,
        info_handler: ErrorHandler,
        mx_client: MexcClient,
        preform_message: Callable,
        utils: Utils,
        direction: str,
        chat_id: str
    ):
        info_handler.wrap_foreign_methods(self)
        self.info_handler = info_handler

        self.context = context
        self.mx_client = mx_client
        self.preform_message = preform_message
        self.contracts_template = utils.contracts_template
        self.direction = direction
        self.chat_id = chat_id
        self.fin_settings = self.context.users_configs[chat_id].get("config").get("fin_settings")
        self.margin_size = self.fin_settings.get("margin_size")
        self.leverage = self.fin_settings.get("leverage")

    async def entry_template(
        self,
        symbol: str,
        cap: float,
        debug_label: str
    ):
        def get_cap_multiplier(cap: float, cap_dep: dict) -> float:
            """
            Ищет диапазон, в который попадает значение cap, и возвращает соответствующий множитель.
            """
            for (low, high), multiplier in cap_dep.items():
                if low <= cap <= high:
                    print(f"No multiplier found for cap={cap}")
                    return multiplier            
            return 1.0

        symbol_data = self.context.position_vars[symbol]
        margin_size = self.margin_size 
        max_leverage = symbol_data.get("spec", {}).get("max_leverage", 20)
        leverage = min(self.leverage, max_leverage)

        if CAP_MULTIPLITER_TRUE:  
            cap_multipliter = get_cap_multiplier(cap=cap, cap_dep=CAP_DEP)
            if MULTIPLITER_TYPE == 1:
                margin_size *= cap_multipliter
            else:
                leverage *= cap_multipliter
                leverage = int(leverage)

        pos_data = symbol_data[self.direction]
        pos_data["margin_size"] = margin_size
        pos_data["nominal_vol"] = margin_size * leverage
        pos_data["leverage"] = leverage

        cur_price = await self.mx_client.get_fair_price(symbol)
        # print(cur_price)
        # return
        contracts = self.contracts_template(
            symbol=symbol, 
            pos_side=self.direction,
            margin_size=margin_size,   
            leverage=leverage,
            entry_price=cur_price,
            symbol_data=symbol_data,
            volume_rate=100,
            debug_label=debug_label,
            key_label="market"
        )

        place_order_resp = await self.mx_client.make_order(
            symbol=symbol,
            contract=contracts,
            side="BUY",         # -- всегда для отрытия
            position_side=self.direction,
            leverage=leverage,
            debug_price=cur_price,
            price=None,
            stopLossPrice=None,
            takeProfitPrice=None,
            open_type=self.context.users_configs[self.chat_id]["config"]["fin_settings"].get("margin_mode", 2),            
            market_type="MARKET",
            debug=True
        )

        valid_resp = OrderValidator.validate_and_log(place_order_resp, debug_label)
        if isinstance(valid_resp, dict):
            success = valid_resp.get("success", False)
            cur_time = valid_resp.get("ts", int(time.time() * 1000))
            reason = valid_resp.get("reason", "N/A")
        else:
            success = False
            cur_time = int(time.time() * 1000)
            reason = "N/A"

        # Проверка успешности размещения ордера
        if not success:
            order_failed_body = {
                "symbol": symbol,
                "reason": reason,
                "cur_time": cur_time,
            }
            self.preform_message(
                chat_id=self.chat_id,
                marker=f"market_order_failed",
                body=order_failed_body,
                is_print=True
            )
            return False

        # Логируем успешное размещение ордера
        order_sent_body = {
            "symbol": symbol,
            "cur_time": cur_time,
        }
        self.preform_message(
            chat_id=self.chat_id,
            marker=f"market_order_sent",
            body=order_sent_body,
            is_print=True
        )

        return True 
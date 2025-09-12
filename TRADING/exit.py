from .valide import OrderValidator
from b_context import BotContext
from c_log import ErrorHandler
import time
from typing import Callable
from API.MX.mx import MexcClient


class ExitControl:
    def __init__(
        self,
        context: BotContext,
        info_handler: ErrorHandler,
        mx_client: MexcClient,
        preform_message: Callable,
        direction: str,
        chat_id: str
    ):
        info_handler.wrap_foreign_methods(self)
        self.info_handler = info_handler

        self.context = context
        self.mx_client = mx_client
        self.preform_message = preform_message
        self.direction = direction
        self.chat_id = chat_id

    async def exit_template(
        self,
        symbol: str,
        cur_price: float,
        debug_label: str
    ):
        
        try:
            pos_data = self.context.position_vars[symbol][self.direction]
            # /
            leverage = pos_data.get("leverage")
            contracts = pos_data.get("contracts")

            place_order_resp = await self.mx_client.make_order(
                symbol=symbol,
                contract=contracts,
                side="SELL",                 # -- всегда для закрытия
                position_side=self.direction,
                leverage=leverage,
                debug_price=cur_price,
                price=None,
                stopLossPrice=None,
                takeProfitPrice=None,
                open_type=self.context.users_configs[self.chat_id]["config"]["fin_settings"].get("margin_mode", 2),            
                market_type="MARKET",
                debug=False
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
                # order_failed_body = {
                #     "symbol": symbol,
                #     "reason": reason + "-- \nпри попытке закрыть остаток позиции.",
                #     "cur_time": cur_time,
                # }
                # self.preform_message(
                #     chat_id=self.chat_id,
                #     marker=f"market_order_failed",
                #     body=order_failed_body,
                #     is_print=True
                # )
                return False
            
            return True 

        finally:            
            await self.mx_client.cancel_order_template(
                symbol=symbol,
                pos_data=pos_data,
                key_list=["sl"]
            )
            await self.mx_client.cancel_all_orders(symbol=symbol)
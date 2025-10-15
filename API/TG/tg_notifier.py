import asyncio
from aiogram import Bot
import asyncio, random
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramRetryAfter,
    TelegramForbiddenError,
    TelegramNetworkError
)
from a_config import *
from b_context import BotContext
from c_log import ErrorHandler
from c_utils import milliseconds_to_datetime, to_human_digit
from typing import *
import random
import traceback


# === Утилита для форматирования сообщений ===
class MessageFormatter:
    def __init__(self, context: BotContext, info_handler: ErrorHandler):
        self.context = context
        self.info_handler = info_handler

    def preform_message(
        self,
        chat_id: str,
        marker: str,
        body: dict,
        is_print: bool = True
    ) -> None:
        # === ВСЯ ВАША ФУНКЦИЯ ===
        msg = ""
        try:          
            head = f"{HEAD_LINE_TYPE}" * HEAD_WIDTH
            footer = f"{FOOTER_LINE_TYPE}" * FOOTER_WIDTH

            symbol = body.get("symbol")
            if symbol:
                symbol = symbol.replace("-USDT-SWAP", "")
            leverage = body.get("leverage")

            cur_time = milliseconds_to_datetime(body.get("cur_time"))
            
            if marker == "signal":
                msg = (
                    f"{head}\n"
                    f"SIGNAL RECEIVED |{TEG_ANCHOR}|[{symbol}]\n\n"
                    f"[{cur_time}]\n\n"
                    f"{footer}\n"
                )
            elif marker == "market_order_sent":
                msg = (
                    f"{head}\n\n"
                    f"MARKET ORDER SENT [{symbol}]\n"
                    f"[{cur_time}]\n\n"
                    f"{footer}\n"
                )
            elif marker == "market_order_filled":
                entry_price = to_human_digit(body.get("entry_price"))
                tp_price_levels = body.get("tp_price_levels")
                tp_msg_templete = ""
                delimiter = "|"
                to_next_line = ""
                for ind, tp in enumerate(tp_price_levels, start=1):
                    delimiter = "|" if (ind % 2 != 0 and ind != len(tp_price_levels)) else ""
                    to_next_line = "\n" if (ind % 2 == 0 or ind == len(tp_price_levels)) else ""
                    tp_msg_templete += f"TP{ind}: {to_human_digit(tp)} {delimiter}{to_next_line}"

                sl = to_human_digit(body.get("cur_sl"))

                msg = (
                    f"{head}\n\n"
                    f"MARKET ORDER FILLED [{symbol}]\n\n"
                    f"[{cur_time}]\n\n"
                    f"LEVERAGE - {leverage} | ENTRY - {entry_price}\n\n"
                    f"{tp_msg_templete}\n"
                    f"SL: {sl}\n\n"
                    f"{footer}\n"
                )
            elif marker == "progress":
                progress = body.get("progress")
                sl = body.get("cur_sl")
                msg = (
                    f"{head}\n\n"                                  
                    f"[{symbol}] | TP{progress} SUCCESS\n"
                    f"NEW_SL - {sl}\n\n"
                    f"{footer}\n"
                )
            elif marker in {"market_order_failed", "tp_order_failed", "sl_order_failed"}:
                reason = body.get("reason")
                msg = (
                    f"{head}\n\n"                                  
                    f"MARKET ORDER FAILED [{symbol}]\n"
                    f"[{cur_time}]\n"
                    f"REASON - {reason}\n\n"
                    f"{footer}\n"
                )
            elif marker == "report":
                pnl_pct = body.get("pnl_pct")
                pnl_usdt = body.get("pnl_usdt")
                time_in_deal = body.get("time_in_deal", "N/A")

                if pnl_pct is None:
                    emo = "N/A"
                elif pnl_pct > 0:
                    emo = f"{EMO_SUCCESS} SUCCESS"
                elif pnl_pct < 0:
                    emo = f"{EMO_LOSE} LOSE"
                else:
                    emo = f"{EMO_ZERO} 0 P&L"

                pnl_pct_str = f"{pnl_pct:.2f}%" if pnl_pct is not None else "N/A"
                if pnl_usdt is not None:
                    sign = "+" if pnl_usdt > 0 else "-" if pnl_usdt < 0 else ""
                    pnl_usdt_str = f"{sign} {abs(pnl_usdt):.4f}"
                else:
                    pnl_usdt_str = "N/A"

                msg = (
                    f"{head}\n\n"
                    f"[{symbol}] | {TEG_ANCHOR} | {emo}\n"
                    f"PNL {pnl_pct_str} | PNL {pnl_usdt_str} USDT\n"
                    f"CLOSING TIME - [{cur_time}]\n"
                    f"TIME IN DEAL - {time_in_deal}\n"
                    f"{footer}\n"
                )
            else:
                print(f"Неизвестный тип сообщения в preform_message. Marker: {marker}")

            self.context.queues_msg[chat_id].append(msg)
            if is_print:
                print(msg)

        except Exception as e:
            err_msg = f"[ERROR] preform_message: {e}\n"
            err_msg += traceback.format_exc()
            self.info_handler.debug_error_notes(err_msg, is_print=True)


# === Основной TelegramNotifier ===
class TelegramNotifier(MessageFormatter):
    def __init__(self, bot: Bot, context: BotContext, info_handler: ErrorHandler):
        super().__init__(context, info_handler)
        self.bot = bot

    async def send_report_batches(self, chat_id: int, batch_size: int = 1):
        queue = self.context.queues_msg[chat_id]
        while queue:
            batch = queue[:batch_size]
            text_block = "\n\n".join(batch)
            await self._send_message(chat_id, text_block)
            del queue[:len(batch)]
            await asyncio.sleep(0.25)

    async def _send_message(self, chat_id: int, text: str):
        while not self.context.stop_bot and not self.context.stop_bot_iteration:
            try:
                msg = await self.bot.send_message(chat_id, text, parse_mode="HTML")
                return msg
            except TelegramNetworkError as e:
                wait = random.uniform(1, 3)
                self.info_handler.debug_error_notes(
                    f"[TG SEND][{chat_id}] Network error: {e}. Retrying in {wait:.1f}s", is_print=True
                )
                await asyncio.sleep(wait)
            except TelegramRetryAfter as e:
                wait = int(getattr(e, "retry_after", 5))
                self.info_handler.debug_error_notes(
                    f"[TG SEND][{chat_id}] Rate limit. Waiting {wait}s", is_print=True
                )
                await asyncio.sleep(wait)
            except TelegramForbiddenError:
                self.info_handler.debug_error_notes(
                    f"[TG SEND][{chat_id}] Bot is blocked by user. Stopping sending.", is_print=True
                )
                return None
            except TelegramAPIError as e:
                self.info_handler.debug_error_notes(
                    f"[TG SEND][{chat_id}] API error: {e}. Exit loop.", is_print=True
                )
                return None
            except Exception as e:
                self.info_handler.debug_error_notes(
                    f"[TG SEND][{chat_id}] Unexpected error: {e}. Exit loop.", is_print=True
                )
                return None
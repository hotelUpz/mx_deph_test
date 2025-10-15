import time
from API.MX.mx_bypass.api import ApiResponse


class OrderValidator:
    @staticmethod
    def validate_and_log(
        result: ApiResponse,
        debug_label: str,
        debug: bool =True       
    ) -> dict:
        """
        Проверяет результат ответа биржи при создании ордера.

        Возвращает словарь:
        {
            "success": bool,
            "order_id": str | None,
            "ts": int,
            "reason": str | None,   # текст ошибки если есть
            "msg": str              # строка для логов
        }
        """
        ts = int(time.time() * 1000)
        order_id, reason = None, None
        # print(result)

        if result and result.success and result.code == 0:
            order_id = getattr(result.data, "orderId", None) or result.data
            ts = getattr(result.data, "ts", ts)
            # if debug:
            #     msg = f"[{debug_label}] ✅ Ордер создан: orderId={order_id}, ts={ts}"
                # print(msg)
            return {
                "success": True,
                "order_id": order_id,
                "ts": ts,
                "reason": None
            }

        # если что-то пошло не так
        reason = getattr(result, "message", None) or "Неизвестная ошибка"
        code = getattr(result, "code", None) or "N/A"
        # if debug:
        #     msg = f"[{debug_label}] ❌ Ошибка при создании ордера: code={code}, reason={reason}"
        #     print(msg)

        return {
            "success": False,
            "order_id": None,
            "ts": ts,
            "reason": reason,
        }

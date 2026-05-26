import logging
import time
from pathlib import Path
from typing import Any

import requests

import config


def build_main_keyboard() -> dict[str, Any]:
    return build_main_menu_keyboard()


def build_main_menu_keyboard() -> dict[str, Any]:
    return {
        "keyboard": [
            ["📡 Радар", "📒 Сетапы"],
            ["⚙️ Настройки", "❓ Help"],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
        "selective": False,
    }


def build_radar_menu_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Status", "callback_data": "cmd:/status"},
                {"text": "🔥 Top OI", "callback_data": "cmd:/top"},
            ],
            [
                {"text": "🕘 Last Alerts", "callback_data": "cmd:/last"},
                {"text": "🧪 Debug", "callback_data": "cmd:/debug_state"},
            ],
            [{"text": "⬅️ Назад", "callback_data": "menu:main"}],
            [{"text": "❌ Закрыть меню", "callback_data": "menu:close"}],
        ]
    }


def build_setups_menu_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "📒 Сетапы", "callback_data": "menu:setups_core"}],
            [{"text": "🧠 Аналитика", "callback_data": "menu:setups_analytics"}],
            [{"text": "📤 Экспорт", "callback_data": "menu:setups_export"}],
            [{"text": "💰 Trading", "callback_data": "menu:setups_trading"}],
            [{"text": "⬅️ Назад", "callback_data": "menu:main"}],
            [{"text": "❌ Закрыть меню", "callback_data": "menu:close"}],
        ]
    }


def build_setups_core_menu_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "📒 Активные сетапы", "callback_data": "cmd:/setups"}],
            [{"text": "📘 Журнал", "callback_data": "cmd:/journal"}],
            [{"text": "📈 Стата", "callback_data": "cmd:/stats"}],
            [{"text": "⬅️ Назад", "callback_data": "menu:setups"}],
            [{"text": "❌ Закрыть меню", "callback_data": "menu:close"}],
        ]
    }


def build_setups_analytics_menu_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "🧠 Аналитика", "callback_data": "cmd:/analytics"}],
            [{"text": "📊 Daily Report Now", "callback_data": "cmd:/daily_report_now"}],
            [{"text": "🧪 Рекомендации", "callback_data": "cmd:/recommend_filters"}],
            [{"text": "🧪 Backtest", "callback_data": "menu:backtest_help"}],
            [{"text": "📊 Backtest Report", "callback_data": "cmd:/backtest_report"}],
            [{"text": "⬅️ Назад", "callback_data": "menu:setups"}],
            [{"text": "❌ Закрыть меню", "callback_data": "menu:close"}],
        ]
    }


def build_setups_export_menu_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "📤 Экспорт журнала", "callback_data": "cmd:/export_journal"}],
            [{"text": "📤 Экспорт статистики", "callback_data": "cmd:/export_stats"}],
            [{"text": "📤 Экспорт ордеров", "callback_data": "cmd:/export_orders"}],
            [{"text": "📤 Export Backtest", "callback_data": "cmd:/export_backtest"}],
            [{"text": "⬅️ Назад", "callback_data": "menu:setups"}],
            [{"text": "❌ Закрыть меню", "callback_data": "menu:close"}],
        ]
    }


def build_setups_trading_menu_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "📋 Orders", "callback_data": "menu:trading_orders"}],
            [{"text": "🔑 API", "callback_data": "menu:trading_api"}],
            [{"text": "🐺 Real Gate", "callback_data": "menu:trading_real"}],
            [{"text": "⬅️ Назад", "callback_data": "menu:setups"}],
            [{"text": "❌ Закрыть меню", "callback_data": "menu:close"}],
        ]
    }


def build_trading_orders_menu_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "📋 Ордера", "callback_data": "cmd:/orders"}],
            [{"text": "📊 Позиции", "callback_data": "cmd:/positions"}],
            [{"text": "💰 Баланс", "callback_data": "cmd:/balance"}],
            [{"text": "🚫 Cancel All TESTNET", "callback_data": "cmd:/cancel_all_testnet"}],
            [{"text": "⬅️ Назад", "callback_data": "menu:setups_trading"}],
            [{"text": "❌ Закрыть меню", "callback_data": "menu:close"}],
        ]
    }


def build_trading_api_menu_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "🔑 API Status", "callback_data": "cmd:/api_status"}],
            [{"text": "🧪 API Test", "callback_data": "cmd:/api_test"}],
            [{"text": "📘 API Help", "callback_data": "cmd:/api_help"}],
            [{"text": "🔐 API Set", "callback_data": "cmd:/api_set"}],
            [{"text": "🧹 API Clear", "callback_data": "cmd:/api_clear"}],
            [{"text": "🔁 API Mode Testnet", "callback_data": "cmd:/api_mode testnet"}],
            [{"text": "🔁 API Mode Mainnet", "callback_data": "cmd:/api_mode mainnet"}],
            [{"text": "🔄 API Reload", "callback_data": "cmd:/api_reload"}],
            [{"text": "⬅️ Назад", "callback_data": "menu:setups_trading"}],
            [{"text": "❌ Закрыть меню", "callback_data": "menu:close"}],
        ]
    }


def build_trading_real_menu_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "🐺 Real Status", "callback_data": "cmd:/real_status"}],
            [{"text": "🔓 Enable Real", "callback_data": "cmd:/enable_real"}],
            [{"text": "🔒 Disable Real", "callback_data": "cmd:/disable_real"}],
            [{"text": "🚨 Panic", "callback_data": "cmd:/panic"}],
            [{"text": "⬅️ Назад", "callback_data": "menu:setups_trading"}],
            [{"text": "❌ Закрыть меню", "callback_data": "menu:close"}],
        ]
    }


def build_settings_menu_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "⚙️ Config", "callback_data": "cmd:/config"}],
            [
                {"text": "⏸ Pause", "callback_data": "cmd:/pause"},
                {"text": "▶️ Resume", "callback_data": "cmd:/resume"},
            ],
            [{"text": "💾 Backup DB", "callback_data": "cmd:/backup_db"}],
            [{"text": "⬅️ Назад", "callback_data": "menu:main"}],
            [{"text": "❌ Закрыть меню", "callback_data": "menu:close"}],
        ]
    }


def build_main_inline_menu_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "📡 Радар", "callback_data": "menu:radar"},
                {"text": "📒 Сетапы", "callback_data": "menu:setups"},
            ],
            [
                {"text": "⚙️ Настройки", "callback_data": "menu:settings"},
                {"text": "❓ Help", "callback_data": "cmd:/help"},
            ],
            [{"text": "❌ Закрыть меню", "callback_data": "menu:close"}],
        ]
    }


def build_bybit_chart_keyboard(symbol: str) -> dict[str, Any]:
    return build_alert_inline_keyboard(symbol)


def build_alert_inline_keyboard(
    symbol: str,
    alert_id: str | None = None,
    show_order_buttons: bool = False,
) -> dict[str, Any]:
    rows = [
        [
            {
                "text": "Open Bybit Chart",
                "url": f"https://www.bybit.com/trade/usdt/{symbol}",
            }
        ]
    ]
    if show_order_buttons and alert_id:
        rows.extend(
            [
                [{"text": "🧮 Рассчитать ордер", "callback_data": f"order_calc:{alert_id}"}],
                [{"text": "🚫 Пропустить", "callback_data": f"order_skip:{alert_id}"}],
            ]
        )
    return {"inline_keyboard": rows}


def build_order_confirmation_keyboard(plan_id: str, trading_enabled: bool) -> dict[str, Any]:
    submit_text = "✅ Поставить лимитку" if trading_enabled else "🧪 Paper order only"
    return {
        "inline_keyboard": [
            [{"text": submit_text, "callback_data": f"order_submit:{plan_id}"}],
            [{"text": "❌ Отмена", "callback_data": f"order_cancel:{plan_id}"}],
        ]
    }


class TelegramAPIError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token
        self.chat_id = str(chat_id)
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.session = requests.Session()
        self.logger = logging.getLogger(self.__class__.__name__)
        self.last_send_ts = 0.0

    def _request(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{method}"
        last_error: Exception | None = None

        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                response = self.session.post(
                    url,
                    json=payload or {},
                    timeout=timeout or config.REQUEST_TIMEOUT_SECONDS,
                )

                if response.status_code == 429:
                    retry_after = 1
                    try:
                        retry_after = int(
                            response.json()
                            .get("parameters", {})
                            .get("retry_after", retry_after)
                        )
                    except (ValueError, TypeError):
                        pass
                    raise requests.HTTPError(
                        f"Telegram rate limit, retry_after={retry_after}",
                        response=response,
                    )

                if response.status_code in {408, 425, 500, 502, 503, 504}:
                    raise requests.HTTPError(
                        f"temporary HTTP {response.status_code}: {response.text[:200]}",
                        response=response,
                    )

                response.raise_for_status()
                data = response.json()
                if not data.get("ok"):
                    raise TelegramAPIError(data.get("description", "unknown error"))
                return data

            except (requests.RequestException, ValueError, TelegramAPIError) as exc:
                last_error = exc
                if attempt >= config.MAX_RETRIES:
                    break

                sleep_for = self._retry_delay(exc, attempt)
                self.logger.warning(
                    "Telegram request failed (%s/%s) method=%s error=%s; retrying in %ss",
                    attempt,
                    config.MAX_RETRIES,
                    method,
                    exc,
                    sleep_for,
                )
                time.sleep(sleep_for)

        raise TelegramAPIError(f"Telegram request failed after retries: {last_error}")

    def _retry_delay(self, error: Exception, attempt: int) -> int:
        response = getattr(error, "response", None)
        if response is not None and response.status_code == 429:
            try:
                return int(response.json().get("parameters", {}).get("retry_after", 1))
            except (ValueError, TypeError):
                return 1
        return config.RETRY_BACKOFF_SECONDS * attempt

    def send_message(
        self,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        chat_id: Any | None = None,
    ) -> None:
        now = time.time()
        elapsed = now - self.last_send_ts
        if elapsed < config.TELEGRAM_MIN_SECONDS_BETWEEN_MESSAGES:
            time.sleep(config.TELEGRAM_MIN_SECONDS_BETWEEN_MESSAGES - elapsed)

        payload: dict[str, Any] = {
            "chat_id": self.chat_id if chat_id is None else str(chat_id),
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        self._request(
            "sendMessage",
            payload,
        )
        self.last_send_ts = time.time()

    def send_document(
        self,
        path: str | Path,
        caption: str | None = None,
    ) -> None:
        file_path = Path(path)
        url = f"{self.base_url}/sendDocument"
        payload = {"chat_id": self.chat_id}
        if caption:
            payload["caption"] = caption

        last_error: Exception | None = None
        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                with file_path.open("rb") as file_obj:
                    response = self.session.post(
                        url,
                        data=payload,
                        files={"document": (file_path.name, file_obj)},
                        timeout=config.REQUEST_TIMEOUT_SECONDS,
                    )
                response.raise_for_status()
                data = response.json()
                if not data.get("ok"):
                    raise TelegramAPIError(data.get("description", "unknown error"))
                return
            except (OSError, requests.RequestException, ValueError, TelegramAPIError) as exc:
                last_error = exc
                if attempt >= config.MAX_RETRIES:
                    break
                time.sleep(config.RETRY_BACKOFF_SECONDS * attempt)
        raise TelegramAPIError(f"Telegram sendDocument failed after retries: {last_error}")

    def edit_message_text(
        self,
        chat_id: Any,
        message_id: Any,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self._request("editMessageText", payload)

    def delete_message(self, chat_id: Any, message_id: Any) -> None:
        self._request(
            "deleteMessage",
            {
                "chat_id": str(chat_id),
                "message_id": message_id,
            },
        )

    def get_updates(
        self,
        offset: int | None = None,
        timeout: int = config.TELEGRAM_POLL_TIMEOUT_SECONDS,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset

        data = self._request(
            "getUpdates",
            payload,
            timeout=timeout + config.REQUEST_TIMEOUT_SECONDS,
        )
        result = data.get("result", [])
        if isinstance(result, list):
            return result
        return []

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
            payload["show_alert"] = False
        self._request("answerCallbackQuery", payload)

    def is_authorized_chat(self, chat_id: Any) -> bool:
        return str(chat_id) == self.chat_id

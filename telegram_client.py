import logging
import time
from typing import Any

import requests

import config


def build_main_keyboard() -> dict[str, Any]:
    return {
        "keyboard": [
            ["📊 Status", "🔥 Top OI"],
            ["🕘 Last Alerts", "⚙️ Config"],
            ["⏸ Pause", "▶️ Resume"],
            ["❓ Help"],
            ["📒 Сетапы", "📘 Журнал"],
            ["📈 Стата"],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
        "selective": False,
    }


def build_bybit_chart_keyboard(symbol: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Open Bybit Chart",
                    "url": f"https://www.bybit.com/trade/usdt/{symbol}",
                }
            ]
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
    ) -> None:
        now = time.time()
        elapsed = now - self.last_send_ts
        if elapsed < config.TELEGRAM_MIN_SECONDS_BETWEEN_MESSAGES:
            time.sleep(config.TELEGRAM_MIN_SECONDS_BETWEEN_MESSAGES - elapsed)

        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
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

    def get_updates(
        self,
        offset: int | None = None,
        timeout: int = config.TELEGRAM_POLL_TIMEOUT_SECONDS,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message"],
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

    def is_authorized_chat(self, chat_id: Any) -> bool:
        return str(chat_id) == self.chat_id

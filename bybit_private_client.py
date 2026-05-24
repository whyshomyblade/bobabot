import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any
from urllib.parse import urlencode

import requests

import config


class BybitPrivateAPIError(RuntimeError):
    pass


class BybitPrivateClient:
    def __init__(self) -> None:
        self.api_key = config.BYBIT_API_KEY
        self.api_secret = config.BYBIT_API_SECRET
        self.testnet = config.BYBIT_TESTNET
        self.trading_enabled = config.BYBIT_TRADING_ENABLED
        self.base_url = "https://api-testnet.bybit.com" if self.testnet else config.BYBIT_BASE_URL
        self.recv_window = "5000"
        self.session = requests.Session()
        self.logger = logging.getLogger(self.__class__.__name__)

        mode = self.mode_label()
        self.logger.info("Execution assistant started in %s mode", mode)
        if not self.has_api_keys:
            self.logger.warning("Bybit API keys are not configured; private trading actions are disabled")
        if config.HOSTING_MODE and not self.trading_enabled:
            self.logger.info("Hosting mode uses paper mode unless BYBIT_TRADING_ENABLED=true")

    @property
    def has_api_keys(self) -> bool:
        return bool(self.api_key and self.api_secret)

    @property
    def can_trade_real(self) -> bool:
        return self.has_api_keys and self.trading_enabled

    @property
    def can_call_private(self) -> bool:
        return self.has_api_keys

    def mode_label(self) -> str:
        if not self.trading_enabled:
            return "PAPER"
        if self.testnet:
            return "TESTNET"
        return "REAL"

    def reload_from_environment(self) -> None:
        self.api_key = os.getenv("BYBIT_API_KEY", "").strip()
        self.api_secret = os.getenv("BYBIT_API_SECRET", "").strip()
        self.testnet = os.getenv("BYBIT_TESTNET", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.trading_enabled = os.getenv("BYBIT_TRADING_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        self.base_url = "https://api-testnet.bybit.com" if self.testnet else config.BYBIT_BASE_URL
        config.BYBIT_API_KEY = self.api_key
        config.BYBIT_API_SECRET = self.api_secret
        config.BYBIT_TESTNET = self.testnet
        config.BYBIT_TRADING_ENABLED = self.trading_enabled
        self.logger.info("Bybit API config reloaded from environment; mode=%s", self.mode_label())

    def get_account_balance(self) -> dict[str, Any]:
        return self._signed_request(
            "GET",
            "/v5/account/wallet-balance",
            {"accountType": "UNIFIED"},
        )

    def get_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"category": "linear"}
        if symbol:
            params["symbol"] = symbol
        else:
            params["settleCoin"] = "USDT"
        payload = self._signed_request("GET", "/v5/position/list", params)
        rows = payload.get("result", {}).get("list", [])
        return rows if isinstance(rows, list) else []

    def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"category": "linear"}
        if symbol:
            params["symbol"] = symbol
        else:
            params["settleCoin"] = "USDT"
        payload = self._signed_request("GET", "/v5/order/realtime", params)
        rows = payload.get("result", {}).get("list", [])
        return rows if isinstance(rows, list) else []

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        qty: float | str,
        price: float | str,
        reduce_only: bool = False,
    ) -> dict[str, Any]:
        if not self.trading_enabled:
            return {
                "retCode": 0,
                "retMsg": "Paper mode: ордер НЕ отправлен на Bybit",
                "result": {
                    "paper": True,
                    "symbol": symbol,
                    "side": side,
                    "qty": str(qty),
                    "price": str(price),
                },
            }
        payload = {
            "category": "linear",
            "symbol": symbol,
            "side": side,
            "orderType": "Limit",
            "qty": str(qty),
            "price": str(price),
            "timeInForce": "GTC",
            "reduceOnly": reduce_only,
        }
        return self._signed_request("POST", "/v5/order/create", payload)

    def place_stop_loss(
        self,
        symbol: str,
        side: str,
        qty: float | str,
        stop_price: float | str,
    ) -> dict[str, Any]:
        if not self.trading_enabled:
            return {
                "retCode": 0,
                "retMsg": "Paper mode: stop loss НЕ отправлен на Bybit",
                "result": {
                    "paper": True,
                    "symbol": symbol,
                    "side": side,
                    "qty": str(qty),
                    "stopPrice": str(stop_price),
                },
            }
        payload = {
            "category": "linear",
            "symbol": symbol,
            "side": side,
            "orderType": "Market",
            "qty": str(qty),
            "triggerPrice": str(stop_price),
            "triggerDirection": 2 if side == "Sell" else 1,
            "reduceOnly": True,
        }
        return self._signed_request("POST", "/v5/order/create", payload)

    def place_take_profit(
        self,
        symbol: str,
        side: str,
        qty: float | str,
        take_profit_price: float | str,
    ) -> dict[str, Any]:
        if not self.trading_enabled:
            return {
                "retCode": 0,
                "retMsg": "Paper mode: take profit НЕ отправлен на Bybit",
                "result": {
                    "paper": True,
                    "symbol": symbol,
                    "side": side,
                    "qty": str(qty),
                    "takeProfitPrice": str(take_profit_price),
                },
            }
        payload = {
            "category": "linear",
            "symbol": symbol,
            "side": side,
            "orderType": "Market",
            "qty": str(qty),
            "triggerPrice": str(take_profit_price),
            "triggerDirection": 1 if side == "Sell" else 2,
            "reduceOnly": True,
        }
        return self._signed_request("POST", "/v5/order/create", payload)

    def cancel_order(self, symbol: str, order_id: str) -> dict[str, Any]:
        if not self.trading_enabled:
            return {
                "retCode": 0,
                "retMsg": "Paper mode: cancel НЕ отправлен на Bybit",
                "result": {
                    "paper": True,
                    "symbol": symbol,
                    "orderId": order_id,
                },
            }
        return self._signed_request(
            "POST",
            "/v5/order/cancel",
            {
                "category": "linear",
                "symbol": symbol,
                "orderId": order_id,
            },
        )

    def get_instrument_info(self, symbol: str) -> dict[str, Any] | None:
        base_url = config.BYBIT_BASE_URL if not self.trading_enabled else self.base_url
        payload = self._public_get(
            "/v5/market/instruments-info",
            {
                "category": "linear",
                "symbol": symbol,
            },
            base_url=base_url,
        )
        rows = payload.get("result", {}).get("list", [])
        if isinstance(rows, list) and rows:
            return rows[0]
        return None

    def _public_get(
        self,
        path: str,
        params: dict[str, Any],
        base_url: str | None = None,
    ) -> dict[str, Any]:
        url = f"{base_url or self.base_url}{path}"
        response = self.session.get(
            url,
            params=params,
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        ret_code = payload.get("retCode")
        if ret_code != 0:
            raise BybitPrivateAPIError(f"Bybit retCode={ret_code}: {payload.get('retMsg')}")
        return payload

    def _signed_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.has_api_keys:
            raise BybitPrivateAPIError("Bybit API keys не настроены.")

        payload = payload or {}
        method = method.upper()
        last_error: Exception | None = None

        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                timestamp = str(int(time.time() * 1000))
                body = ""
                query = ""
                if method == "GET":
                    query = urlencode(sorted(payload.items()))
                    sign_payload = f"{timestamp}{self.api_key}{self.recv_window}{query}"
                else:
                    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
                    sign_payload = f"{timestamp}{self.api_key}{self.recv_window}{body}"

                signature = hmac.new(
                    self.api_secret.encode("utf-8"),
                    sign_payload.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()

                headers = {
                    "X-BAPI-API-KEY": self.api_key,
                    "X-BAPI-TIMESTAMP": timestamp,
                    "X-BAPI-RECV-WINDOW": self.recv_window,
                    "X-BAPI-SIGN": signature,
                }
                url = f"{self.base_url}{path}"
                if method == "GET" and query:
                    url = f"{url}?{query}"
                    response = self.session.get(
                        url,
                        headers=headers,
                        timeout=config.REQUEST_TIMEOUT_SECONDS,
                    )
                else:
                    headers["Content-Type"] = "application/json"
                    response = self.session.post(
                        url,
                        headers=headers,
                        data=body,
                        timeout=config.REQUEST_TIMEOUT_SECONDS,
                    )

                if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
                    raise requests.HTTPError(
                        f"temporary HTTP {response.status_code}: {response.text[:200]}",
                        response=response,
                    )
                response.raise_for_status()
                data = response.json()
                ret_code = data.get("retCode")
                if ret_code != 0:
                    raise BybitPrivateAPIError(f"Bybit retCode={ret_code}: {data.get('retMsg')}")
                return data
            except (requests.RequestException, ValueError, BybitPrivateAPIError) as exc:
                last_error = exc
                if attempt >= config.MAX_RETRIES:
                    break
                sleep_for = config.RETRY_BACKOFF_SECONDS * attempt
                self.logger.warning(
                    "Bybit private request failed (%s/%s) method=%s path=%s error=%s; retrying in %ss",
                    attempt,
                    config.MAX_RETRIES,
                    method,
                    path,
                    exc,
                    sleep_for,
                )
                time.sleep(sleep_for)

        raise BybitPrivateAPIError(f"Bybit private request failed after retries: {last_error}")

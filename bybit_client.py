import logging
import time
from typing import Any

import requests

import config


# Bybit public REST v5 base URL. Override with BYBIT_BASE_URL for hosting if needed.
BYBIT_BASE_URL = config.BYBIT_BASE_URL


class BybitAPIError(RuntimeError):
    pass


class BybitClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.logger = logging.getLogger(self.__class__.__name__)

    def check_connectivity(self) -> bool:
        url = f"{BYBIT_BASE_URL}/v5/market/time"
        try:
            response = self.session.get(url, timeout=config.REQUEST_TIMEOUT_SECONDS)
            if response.status_code == 403:
                self.logger.error("Bybit API is blocked from this hosting provider/region.")
                return False
            response.raise_for_status()
            payload = response.json()
            if payload.get("retCode") != 0:
                self.logger.error("Bybit connectivity check failed: %s", payload)
                return False
            self.logger.info("Bybit connectivity check passed")
            return True
        except requests.RequestException as exc:
            self.logger.error("Bybit connectivity check failed: %s", exc)
            return False
        except ValueError as exc:
            self.logger.error("Bybit connectivity response parse failed: %s", exc)
            return False

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{BYBIT_BASE_URL}{path}"
        last_error: Exception | None = None

        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=config.REQUEST_TIMEOUT_SECONDS,
                )

                if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
                    raise requests.HTTPError(
                        f"temporary HTTP {response.status_code}: {response.text[:200]}",
                        response=response,
                    )

                response.raise_for_status()
                payload = response.json()

                ret_code = payload.get("retCode")
                if ret_code != 0:
                    ret_msg = payload.get("retMsg", "unknown error")
                    raise BybitAPIError(f"Bybit retCode={ret_code}: {ret_msg}")

                return payload

            except (requests.RequestException, ValueError, BybitAPIError) as exc:
                last_error = exc
                if attempt >= config.MAX_RETRIES:
                    break

                sleep_for = config.RETRY_BACKOFF_SECONDS * attempt
                self.logger.warning(
                    "Bybit request failed (%s/%s) path=%s params=%s error=%s; retrying in %ss",
                    attempt,
                    config.MAX_RETRIES,
                    path,
                    params,
                    exc,
                    sleep_for,
                )
                time.sleep(sleep_for)

        raise BybitAPIError(f"Bybit request failed after retries: {last_error}")

    def get_usdt_perpetual_symbols(self) -> list[str]:
        symbols: list[str] = []
        cursor: str | None = None

        while True:
            params: dict[str, Any] = {
                "category": "linear",
                "quoteCoin": "USDT",
                "status": "Trading",
                "limit": 1000,
            }
            if cursor:
                params["cursor"] = cursor

            payload = self._get("/v5/market/instruments-info", params)
            result = payload.get("result", {})
            instruments = result.get("list", [])

            for item in instruments:
                symbol = item.get("symbol")
                quote_coin = item.get("quoteCoin")
                status = item.get("status")
                contract_type = item.get("contractType", "")

                if not symbol or quote_coin != "USDT" or status != "Trading":
                    continue
                if contract_type and "Perpetual" not in contract_type:
                    continue

                symbols.append(symbol)

            cursor = result.get("nextPageCursor")
            if not cursor:
                break

        return sorted(set(symbols))

    def get_linear_tickers(self) -> tuple[list[dict[str, Any]], int]:
        payload = self._get("/v5/market/tickers", {"category": "linear"})
        result = payload.get("result", {})
        tickers = result.get("list", [])
        timestamp_ms = int(payload.get("time") or time.time() * 1000)
        return tickers, timestamp_ms

    def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[Any]:
        params: dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if start_ms is not None:
            params["start"] = start_ms
        if end_ms is not None:
            params["end"] = end_ms

        payload = self._get("/v5/market/kline", params)
        result = payload.get("result", {})
        klines = result.get("list", [])
        if not isinstance(klines, list):
            return []
        return klines

    def get_open_interest_history(
        self,
        symbol: str,
        interval_time: str = "5min",
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
            "intervalTime": interval_time,
            "limit": limit,
        }
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms

        payload = self._get("/v5/market/open-interest", params)
        result = payload.get("result", {})
        items = result.get("list", [])
        return items if isinstance(items, list) else []

    def get_funding_history(
        self,
        symbol: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
            "limit": limit,
        }
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms

        payload = self._get("/v5/market/funding/history", params)
        result = payload.get("result", {})
        items = result.get("list", [])
        return items if isinstance(items, list) else []

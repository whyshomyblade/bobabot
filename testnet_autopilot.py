import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import config
from risk_manager import build_order_plan_from_alert


LOGGER = logging.getLogger("TestnetAutopilot")

ACTIVE_ORDER_STATUSES = {
    "PLANNED",
    "SUBMITTED",
    "OPEN",
    "PARTIALLY_FILLED",
    "FILLED",
    "POSITION_OPENED",
    "UNKNOWN",
}


class TestnetAutopilot:
    def __init__(self, storage: Any, private_client: Any, telegram: Any) -> None:
        self.storage = storage
        self.private_client = private_client
        self.telegram = telegram

    def handle_alert(self, alert: dict[str, Any]) -> None:
        if not autopilot_enabled(self.storage):
            return

        alert_record = self.storage.get_alert_record(str(alert.get("alert_record_id") or ""))
        if alert_record is None:
            alert_record = flatten_alert(alert)

        try:
            plan_or_reason = self._build_allowed_plan(alert_record)
            if isinstance(plan_or_reason, str):
                self._record_decision(alert_record, "REJECTED", plan_or_reason)
                LOGGER.info(
                    "Autopilot rejected symbol=%s reason=%s",
                    alert_record.get("symbol"),
                    plan_or_reason,
                )
                return

            plan = plan_or_reason
            order_link_id = f"bobabot-auto-{uuid.uuid4().hex[:18]}"
            response = self.private_client.place_limit_order(
                symbol=str(plan.get("symbol")),
                side=str(plan.get("bybit_side")),
                qty=str(plan.get("qty")),
                price=str(plan.get("entry_price")),
                reduce_only=False,
                order_link_id=order_link_id,
            )
            result = response.get("result") or {}
            order_id = f"auto_{uuid.uuid4().hex[:18]}"
            plan.update(
                {
                    "id": order_id,
                    "status": "SUBMITTED",
                    "mode": "TESTNET",
                    "order_link_id": order_link_id,
                    "exchange_order_id": result.get("orderId"),
                    "submitted_at": utc_now_iso(),
                    "raw_response_json": response,
                    "exchange_response": response,
                    "autopilot_order": True,
                }
            )
            self.storage.add_paper_order(plan)
            decision = self._record_decision(
                alert_record,
                "SENT",
                "whitelist rules passed",
                order_id=order_id,
                plan=plan,
            )
            LOGGER.warning(
                "Autopilot TESTNET order sent symbol=%s side=%s order_id=%s",
                plan.get("symbol"),
                plan.get("side"),
                result.get("orderId") or order_id,
            )
            self.telegram.send_message(format_autopilot_sent_message(plan, decision))
        except Exception as exc:
            reason = sanitize_error(exc)
            self._record_decision(alert_record, "REJECTED", reason)
            LOGGER.error("Autopilot failed symbol=%s error=%s", alert_record.get("symbol"), reason)

    def _build_allowed_plan(self, alert_record: dict[str, Any]) -> dict[str, Any] | str:
        base_reasons = autopilot_block_reasons(self.storage, self.private_client)
        if base_reasons:
            return base_reasons[0]

        alert_reason = alert_whitelist_rejection(alert_record)
        if alert_reason:
            return alert_reason

        symbol = str(alert_record.get("symbol") or "")
        if not symbol:
            return "symbol missing"
        if has_duplicate_symbol_order(self.storage, symbol):
            return "duplicate active order for symbol"

        try:
            open_orders = self.private_client.get_open_orders(symbol)
            if open_orders:
                return "уже есть активный Bybit ордер по монете"
            positions = self.private_client.get_positions(symbol)
            if has_active_position(positions):
                return "уже есть открытая позиция по монете"
        except Exception as exc:
            return f"API status failed: {sanitize_error(exc)}"

        try:
            instrument_info = self.private_client.get_instrument_info(symbol)
        except Exception as exc:
            return f"instrument info unavailable: {sanitize_error(exc)}"
        if instrument_info is None:
            return "instrument info unavailable"

        try:
            balance = extract_usdt_balance(self.private_client.get_account_balance())
        except Exception as exc:
            return f"balance read failed: {sanitize_error(exc)}"

        plan = build_order_plan_from_alert(
            alert=alert_record,
            instrument_info=instrument_info,
            account_balance_usdt=balance,
            leverage=config.DEFAULT_LEVERAGE,
            has_existing_order=False,
            has_existing_position=False,
        )
        if not plan.get("allowed"):
            reasons = plan.get("reasons") or ["risk validation rejected"]
            return str(reasons[0])
        if (to_float(plan.get("rr_tp1")) or 0) < config.TESTNET_AUTOPILOT_MIN_RR:
            return "autopilot R/R below minimum"
        return plan

    def _record_decision(
        self,
        alert_record: dict[str, Any],
        decision: str,
        reason: str,
        order_id: str | None = None,
        plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        side = (plan or {}).get("side") or direction_from_bias(alert_record.get("setup_bias"))
        record = {
            "id": f"auto_dec_{uuid.uuid4().hex}",
            "created_at": utc_now_iso(),
            "symbol": alert_record.get("symbol"),
            "side": side,
            "alert_type": alert_record.get("alert_type"),
            "risk_level": alert_record.get("risk_level"),
            "execution_quality": normalized_execution_quality(alert_record),
            "decision": decision,
            "reason": reason,
            "order_id": order_id,
            "setup_id": alert_record.get("source_setup_id") or alert_record.get("id"),
            "mode": "TESTNET",
            "source_alert_id": alert_record.get("id"),
            "rr_tp1": (plan or {}).get("rr_tp1"),
            "entry_price": (plan or {}).get("entry_price"),
            "qty": (plan or {}).get("qty"),
        }
        return self.storage.add_autopilot_decision(record)


def autopilot_enabled(storage: Any) -> bool:
    return bool(storage.get_runtime_state("TESTNET_AUTOPILOT_ENABLED", config.TESTNET_AUTOPILOT_ENABLED))


def autopilot_block_reasons(storage: Any, private_client: Any) -> list[str]:
    reasons = []
    if not autopilot_enabled(storage):
        reasons.append("TESTNET_AUTOPILOT_ENABLED=false")
    if not private_client.testnet:
        reasons.append("TESTNET Autopilot works only in TESTNET mode.")
    if not config.BYBIT_TRADING_ENABLED:
        reasons.append("BYBIT_TRADING_ENABLED=false")
    if not private_client.has_api_keys:
        reasons.append("Bybit API keys не настроены.")
    if storage.get_runtime_state("PANIC_MODE", False):
        reasons.append("PANIC_MODE=true")
    if storage.get_runtime_state("RUNTIME_TRADING_DISABLED", False):
        reasons.append("runtime trading disabled")
    if storage.get_runtime_state("daily_loss_limit_reached", False):
        reasons.append("daily loss limit reached")
    if storage.get_runtime_state("order_lifecycle_unstable", False):
        reasons.append("order lifecycle unstable")
    if orders_today(storage) >= config.TESTNET_AUTOPILOT_MAX_ORDERS_PER_DAY:
        reasons.append("max autopilot orders per day reached")
    if active_testnet_orders(storage) >= config.TESTNET_AUTOPILOT_MAX_ACTIVE_ORDERS:
        reasons.append("max active TESTNET orders reached")
    if not reasons:
        checks = run_private_api_checks(private_client)
        if not (checks["balance_ok"] and checks["positions_ok"] and checks["orders_ok"]):
            reasons.append("API test failed")
    return reasons


def alert_whitelist_rejection(alert_record: dict[str, Any]) -> str | None:
    alert_type = str(alert_record.get("alert_type") or "")
    allowed = {item.lower() for item in config.TESTNET_AUTOPILOT_ALLOWED_ALERT_TYPES}
    if alert_type.lower() not in allowed:
        return "alert type not whitelisted"

    risk_level = str(alert_record.get("risk_level") or "")
    if risk_level == "EXTREME":
        return "risk_level = EXTREME"
    if risk_level == "HIGH" and not config.TESTNET_AUTOPILOT_ALLOW_HIGH_RISK:
        return "risk_level = HIGH"

    execution_status = str(alert_record.get("execution_status") or "")
    if execution_status in {"TOO_LATE_DO_NOT_CHASE", "NO_SETUP"}:
        return f"execution_status = {execution_status}"
    if config.TESTNET_AUTOPILOT_REQUIRE_REALISTIC and execution_status not in {"ENTERABLE_NOW", "PENDING_LIMIT_ONLY"}:
        return "execution is not realistic enough"

    execution_quality = normalized_execution_quality(alert_record)
    if execution_quality == "FAST_MOVE" and not config.TESTNET_AUTOPILOT_ALLOW_FAST_MOVE:
        return "execution_quality = FAST_MOVE"
    if execution_quality == "MAYBE_NOT_EXECUTABLE" and not config.TESTNET_AUTOPILOT_ALLOW_FAST_MOVE:
        return "execution_quality = MAYBE_NOT_EXECUTABLE"
    if execution_quality == "AMBIGUOUS" and not config.TESTNET_AUTOPILOT_ALLOW_AMBIGUOUS:
        return "execution_quality = AMBIGUOUS"
    return None


def autopilot_status_text(storage: Any, private_client: Any) -> str:
    reasons = autopilot_block_reasons(storage, private_client)
    lines = [
        "🤖 TESTNET Autopilot Status",
        "",
        f"Enabled: {str(autopilot_enabled(storage)).lower()}",
        f"Mode: {'TESTNET' if private_client.testnet else 'MAINNET'}",
        f"Orders today: {orders_today(storage)} / {config.TESTNET_AUTOPILOT_MAX_ORDERS_PER_DAY}",
        f"Active orders: {active_testnet_orders(storage)} / {config.TESTNET_AUTOPILOT_MAX_ACTIVE_ORDERS}",
        "Allowed alert types:",
    ]
    lines.extend(f"- {item}" for item in config.TESTNET_AUTOPILOT_ALLOWED_ALERT_TYPES)
    lines.extend(["", "Blocked reasons:"])
    lines.extend(f"- {reason}" for reason in reasons)
    if not reasons:
        lines.append("- none")
    return "\n".join(lines)


def autopilot_rules_text() -> str:
    lines = [
        "📜 TESTNET Autopilot Rules",
        "",
        f"Enabled by default: {str(config.TESTNET_AUTOPILOT_ENABLED).lower()}",
        f"Require realistic execution: {str(config.TESTNET_AUTOPILOT_REQUIRE_REALISTIC).lower()}",
        f"Allow HIGH risk: {str(config.TESTNET_AUTOPILOT_ALLOW_HIGH_RISK).lower()}",
        f"Allow FAST_MOVE: {str(config.TESTNET_AUTOPILOT_ALLOW_FAST_MOVE).lower()}",
        f"Allow AMBIGUOUS: {str(config.TESTNET_AUTOPILOT_ALLOW_AMBIGUOUS).lower()}",
        f"Max orders/day: {config.TESTNET_AUTOPILOT_MAX_ORDERS_PER_DAY}",
        f"Max active orders: {config.TESTNET_AUTOPILOT_MAX_ACTIVE_ORDERS}",
        f"Min R/R: {config.TESTNET_AUTOPILOT_MIN_RR:g}",
        "",
        "Allowed alert types:",
    ]
    lines.extend(f"- {item}" for item in config.TESTNET_AUTOPILOT_ALLOWED_ALERT_TYPES)
    lines.extend(
        [
            "",
            "Real market: NO",
            "Autopilot can only submit TESTNET limit orders.",
        ]
    )
    return "\n".join(lines)


def autopilot_journal_text(storage: Any, limit: int = 10) -> str:
    decisions = storage.get_autopilot_decisions(limit=limit)
    if not decisions:
        return "📒 Autopilot journal пуст."
    lines = ["📒 Autopilot journal:"]
    for index, item in enumerate(decisions, start=1):
        lines.extend(
            [
                (
                    f"{index}. {item.get('symbol', 'n/a')} | {item.get('side') or 'n/a'} | "
                    f"{item.get('decision')} | {item.get('reason')}"
                ),
                f"Type: {item.get('alert_type') or 'n/a'} | Risk: {item.get('risk_level') or 'n/a'}",
                f"Order: {item.get('order_id') or 'n/a'} | Time: {format_time(item.get('created_at'))}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def format_autopilot_sent_message(plan: dict[str, Any], decision: dict[str, Any]) -> str:
    return "\n".join(
        [
            "🧪 TESTNET AUTOPILOT ORDER SENT",
            "",
            f"Symbol: {plan.get('symbol')}",
            f"Side: {plan.get('side')}",
            f"Entry: {plan.get('entry_price')}",
            f"Qty: {plan.get('qty')}",
            f"Risk: {plan.get('risk_usdt', 0):.2f} USDT",
            "Reason: whitelist rules passed",
            f"Order ID: {plan.get('exchange_order_id') or plan.get('order_link_id') or plan.get('id')}",
            "",
            "Real market: NO",
        ]
    )


def orders_today(storage: Any) -> int:
    today = datetime.now(UTC).date().isoformat()
    total = 0
    for item in storage.get_autopilot_decisions(limit=None):
        if item.get("decision") != "SENT":
            continue
        created_at = str(item.get("created_at") or "")
        if created_at.startswith(today):
            total += 1
    return total


def active_testnet_orders(storage: Any) -> int:
    return sum(
        1
        for order in storage.get_paper_orders(limit=None)
        if order.get("mode") == "TESTNET" and str(order.get("status") or "") in ACTIVE_ORDER_STATUSES
    )


def has_duplicate_symbol_order(storage: Any, symbol: str) -> bool:
    symbol = str(symbol or "")
    for order in storage.get_paper_orders(limit=None):
        if order.get("mode") != "TESTNET":
            continue
        if str(order.get("status") or "") not in ACTIVE_ORDER_STATUSES:
            continue
        if str(order.get("symbol") or "") == symbol:
            return True
    return False


def run_private_api_checks(private_client: Any) -> dict[str, Any]:
    result = {
        "balance_ok": False,
        "positions_ok": False,
        "orders_ok": False,
        "errors": [],
    }
    if not private_client.has_api_keys:
        result["errors"].append("Bybit API keys не настроены.")
        return result
    try:
        private_client.get_account_balance()
        result["balance_ok"] = True
    except Exception as exc:
        result["errors"].append(f"balance: {sanitize_error(exc)}")
    try:
        private_client.get_positions()
        result["positions_ok"] = True
    except Exception as exc:
        result["errors"].append(f"positions: {sanitize_error(exc)}")
    try:
        private_client.get_open_orders()
        result["orders_ok"] = True
    except Exception as exc:
        result["errors"].append(f"orders: {sanitize_error(exc)}")
    return result


def extract_usdt_balance(payload: dict[str, Any]) -> float:
    accounts = payload.get("result", {}).get("list", [])
    if not isinstance(accounts, list):
        return 0.0
    for account in accounts:
        coins = account.get("coin", [])
        if not isinstance(coins, list):
            continue
        for coin in coins:
            if coin.get("coin") == "USDT":
                return to_float(coin.get("equity") or coin.get("walletBalance")) or 0.0
    return 0.0


def has_active_position(positions: list[dict[str, Any]]) -> bool:
    return any(abs(to_float(item.get("size")) or 0) > 0 for item in positions)


def flatten_alert(alert: dict[str, Any]) -> dict[str, Any]:
    setup = alert.get("setup_scenario") if isinstance(alert.get("setup_scenario"), dict) else {}
    return {
        "id": alert.get("alert_record_id") or alert.get("id"),
        "timestamp": alert.get("timestamp"),
        "symbol": alert.get("symbol"),
        "price": alert.get("price"),
        "alert_type": alert.get("alert_type"),
        "risk_level": alert.get("risk_level"),
        "setup_bias": setup.get("bias"),
        "setup_status": setup.get("setup_status"),
        "execution_status": setup.get("execution_status"),
        "source_setup_id": setup.get("setup_id") or alert.get("alert_record_id"),
        "entry_zone": setup.get("entry_zone"),
        "invalidation": setup.get("invalidation"),
        "tp1": setup.get("tp1"),
        "tp2": setup.get("tp2"),
    }


def normalized_execution_quality(alert_record: dict[str, Any]) -> str:
    quality = str(alert_record.get("execution_quality") or "").upper()
    if quality:
        return quality
    execution_status = str(alert_record.get("execution_status") or "")
    if execution_status in {"ENTERABLE_NOW", "PENDING_LIMIT_ONLY"}:
        return "REALISTIC"
    return "UNKNOWN"


def direction_from_bias(value: Any) -> str | None:
    bias = str(value or "")
    if "LONG" in bias:
        return "LONG"
    if "SHORT" in bias:
        return "SHORT"
    return None


def format_time(value: Any) -> str:
    if not value:
        return "n/a"
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    return parsed.strftime("%H:%M UTC")


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sanitize_error(value: Any) -> str:
    text = str(value)
    for secret in (config.BYBIT_API_SECRET, config.BYBIT_API_KEY):
        if secret:
            text = text.replace(secret, "[hidden]")
    if len(text) > 180:
        return text[:177] + "..."
    return text

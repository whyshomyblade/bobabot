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

    def maybe_run_testnet_autopilot(self, setup: dict[str, Any]) -> None:
        setup_record = flatten_setup(setup)
        setup_id = str(setup_record.get("source_setup_id") or setup_record.get("id") or "")
        self.storage.save_runtime_state("last_activated_setup", setup_record)

        if not setup_id:
            LOGGER.warning("Autopilot activated hook skipped: setup_id missing symbol=%s", setup_record.get("symbol"))
            return
        if setup.get("autopilot_decision_id") or has_autopilot_decision(self.storage, setup_id):
            LOGGER.info("Autopilot already decided for setup_id=%s", setup_id)
            return

        if not autopilot_enabled(self.storage):
            decision = self._record_decision(setup_record, "SKIPPED", "autopilot disabled")
            self._mark_setup_decided(setup_id, decision)
            self.storage.save_runtime_state("last_autopilot_decision", decision)
            return

        aggressive = bool(self.private_client.testnet and testnet_aggressive_enabled(self.storage))
        warnings = autopilot_warnings(setup_record) if aggressive else []

        try:
            plan_or_reason = self._build_allowed_plan(
                setup_record,
                activated=True,
                aggressive=aggressive,
            )
            if isinstance(plan_or_reason, str):
                decision = self._record_decision(
                    setup_record,
                    "REJECTED",
                    plan_or_reason,
                    aggressive_mode=aggressive,
                    warnings=warnings,
                )
                self._mark_setup_decided(setup_id, decision)
                self.storage.save_runtime_state("last_autopilot_decision", decision)
                LOGGER.warning(
                    "Autopilot activated setup rejected symbol=%s reason=%s",
                    setup_record.get("symbol"),
                    plan_or_reason,
                )
                self.telegram.send_message(format_autopilot_rejected_message(setup_record, plan_or_reason))
                return

            plan = plan_or_reason
            plan_rr = to_float(plan.get("rr_tp1"))
            if aggressive and plan_rr is not None and plan_rr < config.TESTNET_AUTOPILOT_MIN_RR:
                rr_warning = f"R/R below normal autopilot minimum: {plan_rr:.2f}R"
                if rr_warning not in warnings:
                    warnings.append(rr_warning)
            plan["aggressive_mode"] = aggressive
            plan["warnings"] = warnings
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
            local_order_id = f"auto_{uuid.uuid4().hex[:18]}"
            plan.update(
                {
                    "id": local_order_id,
                    "status": "SUBMITTED",
                    "mode": "TESTNET",
                    "order_link_id": order_link_id,
                    "exchange_order_id": result.get("orderId"),
                    "submitted_at": utc_now_iso(),
                    "raw_response_json": response,
                    "exchange_response": response,
                    "autopilot_order": True,
                    "source_setup_id": setup_id,
                }
            )
            try:
                self.storage.add_paper_order(plan)
            except Exception as exc:
                LOGGER.error("TESTNET order sent but local DB save failed: %s", sanitize_error(exc))
                self.telegram.send_message("TESTNET order sent but local DB save failed.")

            decision = self._record_decision(
                setup_record,
                "SENT",
                "setup activated + autopilot passed",
                order_id=result.get("orderId") or local_order_id,
                order_link_id=order_link_id,
                plan=plan,
                aggressive_mode=aggressive,
                warnings=warnings,
            )
            self._mark_setup_decided(setup_id, decision)
            self.storage.save_runtime_state("last_autopilot_decision", decision)
            LOGGER.warning(
                "Autopilot TESTNET order sent from activated setup symbol=%s side=%s order_id=%s",
                plan.get("symbol"),
                plan.get("side"),
                result.get("orderId") or local_order_id,
            )
            self.telegram.send_message(format_autopilot_sent_message(plan, decision))
        except Exception as exc:
            reason = sanitize_error(exc)
            decision = self._record_decision(
                setup_record,
                "REJECTED",
                reason,
                aggressive_mode=aggressive,
                warnings=warnings,
            )
            self._mark_setup_decided(setup_id, decision)
            self.storage.save_runtime_state("last_autopilot_decision", decision)
            LOGGER.error("Autopilot activated hook failed symbol=%s error=%s", setup_record.get("symbol"), reason)
            self.telegram.send_message(format_autopilot_rejected_message(setup_record, reason))

    def _build_allowed_plan(
        self,
        alert_record: dict[str, Any],
        activated: bool = False,
        aggressive: bool = False,
    ) -> dict[str, Any] | str:
        base_reasons = autopilot_block_reasons(
            self.storage,
            self.private_client,
            aggressive=aggressive,
        )
        if base_reasons:
            return base_reasons[0]

        if activated and aggressive:
            alert_reason = technical_setup_rejection(alert_record)
        else:
            alert_reason = activated_setup_rejection(alert_record) if activated else alert_whitelist_rejection(alert_record)
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

        plan_record = aggressive_plan_view(alert_record) if aggressive else alert_record
        plan = build_order_plan_from_alert(
            alert=plan_record,
            instrument_info=instrument_info,
            account_balance_usdt=balance,
            leverage=config.DEFAULT_LEVERAGE,
            has_existing_order=False,
            has_existing_position=False,
            ignore_min_rr=aggressive,
        )
        if not plan.get("allowed"):
            reasons = plan.get("reasons") or ["risk validation rejected"]
            return str(reasons[0])
        if not aggressive and (to_float(plan.get("rr_tp1")) or 0) < config.TESTNET_AUTOPILOT_MIN_RR:
            return "autopilot R/R below minimum"
        if aggressive:
            plan.update(
                {
                    "risk_level": alert_record.get("risk_level"),
                    "execution_quality": normalized_execution_quality(alert_record),
                    "execution_status": alert_record.get("execution_status"),
                    "setup_status": alert_record.get("setup_status") or alert_record.get("state"),
                }
            )
        return plan

    def _record_decision(
        self,
        alert_record: dict[str, Any],
        decision: str,
        reason: str,
        order_id: str | None = None,
        order_link_id: str | None = None,
        plan: dict[str, Any] | None = None,
        aggressive_mode: bool = False,
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        side = (plan or {}).get("side") or direction_from_bias(alert_record.get("setup_bias"))
        warnings = list(warnings or [])
        record = {
            "id": f"auto_dec_{uuid.uuid4().hex}",
            "created_at": utc_now_iso(),
            "symbol": alert_record.get("symbol"),
            "side": side,
            "alert_type": alert_record.get("alert_type"),
            "risk_level": alert_record.get("risk_level"),
            "execution_quality": normalized_execution_quality(alert_record),
            "setup_status": alert_record.get("setup_status") or alert_record.get("state"),
            "decision": decision,
            "reason": reason,
            "order_id": order_id,
            "order_link_id": order_link_id or (plan or {}).get("order_link_id"),
            "setup_id": alert_record.get("source_setup_id") or alert_record.get("id"),
            "mode": "TESTNET",
            "aggressive_mode": aggressive_mode,
            "warnings": warnings,
            "warnings_json": warnings,
            "source_alert_id": alert_record.get("id"),
            "rr_tp1": (plan or {}).get("rr_tp1"),
            "entry_price": (plan or {}).get("entry_price"),
            "qty": (plan or {}).get("qty"),
            "raw_context_json": alert_record,
        }
        return self.storage.add_autopilot_decision(record)

    def _mark_setup_decided(self, setup_id: str, decision: dict[str, Any]) -> None:
        try:
            self.storage.update_active_setup(setup_id, {"autopilot_decision_id": decision.get("id")})
        except Exception as exc:
            LOGGER.warning("Could not mark active setup autopilot decision setup_id=%s: %s", setup_id, exc)


def autopilot_enabled(storage: Any) -> bool:
    return bool(storage.get_runtime_state("TESTNET_AUTOPILOT_ENABLED", config.TESTNET_AUTOPILOT_ENABLED))


def testnet_aggressive_enabled(storage: Any) -> bool:
    return bool(storage.get_runtime_state("TESTNET_AGGRESSIVE_MODE", config.TESTNET_AGGRESSIVE_MODE))


def set_testnet_aggressive_mode_text(storage: Any, enabled: bool) -> str:
    storage.save_runtime_state("TESTNET_AGGRESSIVE_MODE", enabled)
    LOGGER.warning("TESTNET aggressive mode set to %s", enabled)
    if enabled:
        return "\n".join(
            [
                "🧪 TESTNET Aggressive Mode: ON",
                "",
                "Автопилот будет отправлять TESTNET лимитки по каждому активированному сетапу, если это технически возможно.",
                "HIGH/EXTREME, FAST_MOVE, whitelist и TOO_LATE станут warnings, а не стопорами.",
                "",
                "Real market: NO",
            ]
        )
    return "\n".join(
        [
            "🧊 TESTNET Aggressive Mode: OFF",
            "",
            "Автопилот снова использует обычные whitelist/risk/execution фильтры.",
            "Real market: NO",
        ]
    )


def testnet_aggressive_status_text(storage: Any, private_client: Any) -> str:
    aggressive = testnet_aggressive_enabled(storage)
    reasons = autopilot_block_reasons(storage, private_client, aggressive=aggressive)
    lines = [
        "🧪 TESTNET Aggressive Status",
        "",
        f"Aggressive mode: {'ON' if aggressive else 'OFF'}",
        f"Autopilot enabled: {str(autopilot_enabled(storage)).lower()}",
        f"Mode: {'TESTNET' if private_client.testnet else 'MAINNET'}",
        f"Trading enabled: {str(config.BYBIT_TRADING_ENABLED).lower()}",
        "Real market: NO",
        "",
        "Hard blockers:",
    ]
    lines.extend(f"- {reason}" for reason in reasons)
    if not reasons:
        lines.append("- none")
    lines.extend(
        [
            "",
            "When ON, these become warnings instead of blockers:",
            "- HIGH / EXTREME risk",
            "- FAST_MOVE / MAYBE_NOT_EXECUTABLE / AMBIGUOUS",
            "- alert type whitelist",
            "- TOO_LATE_DO_NOT_CHASE",
        ]
    )
    return "\n".join(lines)


def autopilot_block_reasons(storage: Any, private_client: Any, aggressive: bool = False) -> list[str]:
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
    if not aggressive and orders_today(storage) >= config.TESTNET_AUTOPILOT_MAX_ORDERS_PER_DAY:
        reasons.append("max autopilot orders per day reached")
    if not aggressive and active_testnet_orders(storage) >= config.TESTNET_AUTOPILOT_MAX_ACTIVE_ORDERS:
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
    if risk_level not in {"LOW", "MEDIUM", "HIGH", "EXTREME"}:
        return f"risk_level invalid: {risk_level or 'unknown'}"
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


def activated_setup_rejection(alert_record: dict[str, Any]) -> str | None:
    setup_status = str(alert_record.get("setup_status") or alert_record.get("state") or "")
    if setup_status not in {"ENTERED", "ACTIVE", "ACTIVATED", "ВХОД АКТИВИРОВАН"}:
        return f"setup status is not activated: {setup_status or 'unknown'}"

    for field in ("entry_zone", "invalidation", "tp1", "tp2"):
        if alert_record.get(field) in {None, "", "n/a"}:
            return f"{field} missing"

    execution_quality = normalized_execution_quality(alert_record)
    if execution_quality != "REALISTIC":
        return f"execution_quality = {execution_quality}"

    risk_level = str(alert_record.get("risk_level") or "")
    if risk_level == "EXTREME":
        return "risk_level = EXTREME"
    if risk_level == "HIGH" and not config.TESTNET_AUTOPILOT_ALLOW_HIGH_RISK:
        return "risk_level = HIGH"

    execution_status = str(alert_record.get("execution_status") or "")
    if execution_status in {"TOO_LATE_DO_NOT_CHASE", "NO_SETUP"}:
        return f"execution_status = {execution_status}"

    if config.STRICT_ALERT_TYPE_WHITELIST:
        return alert_whitelist_rejection(alert_record)
    return None


def technical_setup_rejection(alert_record: dict[str, Any]) -> str | None:
    setup_status = str(alert_record.get("setup_status") or alert_record.get("state") or "")
    if setup_status not in {"ENTERED", "ACTIVE", "ACTIVATED", "ВХОД АКТИВИРОВАН"}:
        return f"setup status is not activated: {setup_status or 'unknown'}"

    if direction_from_bias(alert_record.get("setup_bias")) not in {"LONG", "SHORT"}:
        return "setup direction missing"

    for field in ("entry_zone", "invalidation", "tp1", "tp2"):
        if alert_record.get(field) in {None, "", "n/a"}:
            return f"{field} missing"
    return None


def aggressive_plan_view(alert_record: dict[str, Any]) -> dict[str, Any]:
    plan_record = dict(alert_record)
    plan_record["risk_level"] = "MEDIUM"
    plan_record["execution_quality"] = "REALISTIC"
    plan_record["execution_status"] = "ENTERABLE_NOW"
    plan_record["setup_status"] = "ENTERED"
    return plan_record


def autopilot_warnings(alert_record: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    alert_type = str(alert_record.get("alert_type") or "")
    allowed = {item.lower() for item in config.TESTNET_AUTOPILOT_ALLOWED_ALERT_TYPES}
    if alert_type and alert_type.lower() not in allowed:
        warnings.append(f"Alert type outside whitelist: {alert_type}")

    risk_level = str(alert_record.get("risk_level") or "unknown")
    if risk_level in {"HIGH", "EXTREME"}:
        warnings.append(f"Risk level warning: {risk_level}")

    execution_quality = normalized_execution_quality(alert_record)
    if execution_quality in {"FAST_MOVE", "MAYBE_NOT_EXECUTABLE", "AMBIGUOUS"}:
        warnings.append(f"Execution quality warning: {execution_quality}")

    execution_status = str(alert_record.get("execution_status") or "")
    if execution_status in {"TOO_LATE_DO_NOT_CHASE", "NO_SETUP"}:
        warnings.append(f"Execution status warning: {execution_status}")

    rr = to_float(alert_record.get("rr_tp1"))
    if rr is not None and rr < config.TESTNET_AUTOPILOT_MIN_RR:
        warnings.append(f"R/R below normal autopilot minimum: {rr:.2f}R")
    return warnings


def autopilot_status_text(storage: Any, private_client: Any) -> str:
    aggressive = testnet_aggressive_enabled(storage)
    reasons = autopilot_block_reasons(storage, private_client, aggressive=aggressive)
    lines = [
        "🤖 TESTNET Autopilot Status",
        "",
        f"Enabled: {str(autopilot_enabled(storage)).lower()}",
        f"Mode: {'TESTNET' if private_client.testnet else 'MAINNET'}",
        f"Aggressive mode: {'ON' if aggressive else 'OFF'}",
        f"Strict whitelist: {str(config.STRICT_ALERT_TYPE_WHITELIST).lower()}",
        f"Orders today: {orders_today(storage)} / {config.TESTNET_AUTOPILOT_MAX_ORDERS_PER_DAY}",
        f"Active orders: {active_testnet_orders(storage)} / {config.TESTNET_AUTOPILOT_MAX_ACTIVE_ORDERS}",
        "Allowed alert types:",
    ]
    lines.extend(f"- {item}" for item in config.TESTNET_AUTOPILOT_ALLOWED_ALERT_TYPES)
    lines.extend(["", "Blocked reasons:"])
    lines.extend(f"- {reason}" for reason in reasons)
    if not reasons:
        lines.append("- none")
    if aggressive:
        lines.extend(
            [
                "",
                "Aggressive mode notes:",
                "- HIGH/EXTREME, FAST_MOVE, whitelist and TOO_LATE are warnings only.",
                "- API, panic/runtime blocks, duplicates, active positions and invalid risk/qty still reject.",
            ]
        )
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
        f"Aggressive mode default: {str(config.TESTNET_AGGRESSIVE_MODE).lower()}",
        f"Strict alert type whitelist: {str(config.STRICT_ALERT_TYPE_WHITELIST).lower()}",
        "",
        "Allowed alert types:",
    ]
    lines.extend(f"- {item}" for item in config.TESTNET_AUTOPILOT_ALLOWED_ALERT_TYPES)
    lines.extend(
        [
            "",
            "Real market: NO",
            "Autopilot can only submit TESTNET limit orders.",
            "If strict whitelist is false, activated LOW/MEDIUM REALISTIC setups can pass even when alert type is not whitelisted.",
            "",
            "Aggressive mode:",
            "- TESTNET only.",
            "- Sends a TESTNET limit order for every activated setup when technically possible.",
            "- HIGH/EXTREME, FAST_MOVE/AMBIGUOUS, TOO_LATE and whitelist mismatches become warnings.",
            "- API not ready, panic mode, runtime trading disabled, duplicates, active positions and invalid qty/risk still reject.",
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
                    f"{item.get('decision')} | "
                    f"{'order_id=' + str(item.get('order_id')) if item.get('order_id') else 'reason=' + str(item.get('reason') or 'n/a')}"
                ),
                f"Type: {item.get('alert_type') or 'n/a'}",
                f"Risk: {item.get('risk_level') or 'n/a'}",
                f"Execution: {item.get('execution_quality') or 'n/a'}",
                f"Aggressive: {'ON' if item.get('aggressive_mode') else 'OFF'}",
                f"Warnings: {format_warnings_inline(item)}",
                f"Reason: {item.get('reason') or 'n/a'}",
                f"Time: {format_time(item.get('created_at'))}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def normalize_warnings(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                import json

                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed if str(item)]
            except Exception:
                return [text]
        return [text]
    return []


def format_warnings_inline(item: dict[str, Any]) -> str:
    warnings = normalize_warnings(item.get("warnings") or item.get("warnings_json"))
    if not warnings:
        return "none"
    return "; ".join(warnings[:3]) + ("; ..." if len(warnings) > 3 else "")


def format_autopilot_sent_message(plan: dict[str, Any], decision: dict[str, Any]) -> str:
    aggressive = bool(plan.get("aggressive_mode") or decision.get("aggressive_mode"))
    warnings = normalize_warnings(plan.get("warnings") or decision.get("warnings") or decision.get("warnings_json"))
    lines = [
        "🧪 TESTNET AUTOPILOT ORDER SENT",
        "",
        f"Symbol: {plan.get('symbol')}",
        f"Side: {plan.get('side')}",
        f"Entry: {plan.get('entry_price')}",
        f"Qty: {plan.get('qty')}",
        f"Risk: {plan.get('risk_usdt', 0):.2f} USDT",
        f"Reason: {decision.get('reason') or 'setup activated + autopilot passed'}",
        f"Order ID: {plan.get('exchange_order_id') or plan.get('order_link_id') or plan.get('id')}",
        "",
        "Mode: TESTNET",
        f"Aggressive mode: {'ON' if aggressive else 'OFF'}",
        "Real market: NO",
    ]
    if warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines)


def format_autopilot_rejected_message(setup: dict[str, Any], reason: str) -> str:
    return "\n".join(
        [
            "❌ TESTNET AUTOPILOT REJECTED",
            "",
            f"Symbol: {setup.get('symbol', 'n/a')}",
            f"Side: {direction_from_bias(setup.get('setup_bias')) or setup.get('side') or 'n/a'}",
            f"Reason: {reason}",
        ]
    )


def autopilot_debug_last_text(storage: Any, private_client: Any) -> str:
    setup = storage.get_runtime_state("last_activated_setup", {}) or {}
    decision = storage.get_runtime_state("last_autopilot_decision", {}) or {}
    checks = run_private_api_checks(private_client) if private_client.has_api_keys else {
        "balance_ok": False,
        "positions_ok": False,
        "orders_ok": False,
        "errors": ["Bybit API keys не настроены."],
    }
    api_ok = checks["balance_ok"] and checks["positions_ok"] and checks["orders_ok"]
    symbol = str(setup.get("symbol") or "")
    duplicate = has_duplicate_symbol_order(storage, symbol) if symbol else False
    aggressive = testnet_aggressive_enabled(storage)
    risk_ok = not (technical_setup_rejection(setup) if aggressive else activated_setup_rejection(setup)) if setup else False
    return "\n".join(
        [
            "🧪 Autopilot Debug Last",
            "",
            "Last setup:",
            f"Symbol: {setup.get('symbol', 'n/a')}",
            f"Side: {direction_from_bias(setup.get('setup_bias')) or setup.get('side') or 'n/a'}",
            f"Status: {setup.get('setup_status') or setup.get('state') or 'n/a'}",
            f"Risk: {setup.get('risk_level', 'n/a')}",
            f"Execution: {normalized_execution_quality(setup)}",
            f"Entry: {setup.get('entry_zone', 'n/a')}",
            f"Stop: {setup.get('invalidation', 'n/a')}",
            f"TP1: {setup.get('tp1', 'n/a')}",
            f"TP2: {setup.get('tp2', 'n/a')}",
            "",
            "Autopilot:",
            f"Enabled: {str(autopilot_enabled(storage)).lower()}",
            f"Aggressive mode: {'ON' if aggressive else 'OFF'}",
            f"Trading enabled: {str(config.BYBIT_TRADING_ENABLED).lower()}",
            f"Mode: {'TESTNET' if private_client.testnet else 'MAINNET'}",
            f"API OK: {str(api_ok).lower()}",
            f"Paper mode: {str(not config.BYBIT_TRADING_ENABLED).lower()}",
            f"Risk OK: {str(risk_ok).lower()}",
            f"Duplicate order: {str(duplicate).lower()}",
            f"Decision: {decision.get('decision', 'n/a')}",
            f"Reason: {decision.get('reason', 'n/a')}",
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


def has_autopilot_decision(storage: Any, setup_id: str) -> bool:
    setup_id = str(setup_id or "")
    if not setup_id:
        return False
    return any(
        str(item.get("setup_id") or "") == setup_id
        for item in storage.get_autopilot_decisions(limit=None)
    )


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


def flatten_setup(setup: dict[str, Any]) -> dict[str, Any]:
    entry_low = setup.get("entry_low")
    entry_high = setup.get("entry_high")
    entry_zone = setup.get("entry_zone")
    if entry_zone in {None, "", "n/a"} and entry_low not in {None, ""} and entry_high not in {None, ""}:
        entry_zone = f"{entry_low} - {entry_high}"
    direction = str(setup.get("direction") or "")
    return {
        "id": setup.get("id"),
        "timestamp": setup.get("created_at"),
        "symbol": setup.get("symbol"),
        "price": setup.get("created_price"),
        "alert_type": setup.get("alert_type"),
        "risk_level": setup.get("risk_level"),
        "setup_bias": setup.get("bias") or direction,
        "setup_status": setup.get("state") or setup.get("setup_status"),
        "state": setup.get("state"),
        "execution_status": setup.get("execution_status") or "ENTERABLE_NOW",
        "execution_quality": setup.get("execution_quality"),
        "source_setup_id": setup.get("id"),
        "entry_zone": entry_zone,
        "entry_price_virtual": setup.get("entry_price_virtual"),
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

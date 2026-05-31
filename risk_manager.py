import math
from typing import Any

import config
from setup_tracker import parse_entry_zone, parse_price_value


def build_order_plan_from_alert(
    alert: dict[str, Any],
    instrument_info: dict[str, Any] | None,
    account_balance_usdt: float,
    leverage: int = config.DEFAULT_LEVERAGE,
    has_existing_order: bool = False,
    has_existing_position: bool = False,
    ignore_min_rr: bool = False,
) -> dict[str, Any]:
    setup = _setup_view(alert)
    symbol = str(alert.get("symbol") or "")
    side = _direction_from_bias(setup.get("setup_bias"))
    entry_zone = parse_entry_zone(setup.get("entry_zone"))
    stop_price = parse_price_value(setup.get("invalidation"))
    tp1 = parse_price_value(setup.get("tp1"))
    tp2 = parse_price_value(setup.get("tp2"))

    base_plan: dict[str, Any] = {
        "allowed": False,
        "reasons": [],
        "safety": [],
        "symbol": symbol,
        "side": side or "UNKNOWN",
        "mode": _mode_label(),
        "source_alert_id": alert.get("id"),
        "source_setup_id": alert.get("source_setup_id") or alert.get("id"),
        "alert_type": alert.get("alert_type"),
        "risk_level": alert.get("risk_level"),
        "execution_status": setup.get("execution_status"),
        "setup_status": setup.get("setup_status"),
        "execution_quality": alert.get("execution_quality"),
        "leverage": leverage,
        "account_balance_usdt": account_balance_usdt,
    }

    if instrument_info is None:
        return _reject(base_plan, "instrument info unavailable")
    if side is None:
        return _reject(base_plan, "не удалось определить направление сетапа")
    if entry_zone is None or None in {stop_price, tp1, tp2}:
        return _reject(base_plan, "нет корректной зоны входа / stop / TP")

    entry_low, entry_high = entry_zone
    entry_price = _round_price((entry_low + entry_high) / 2, instrument_info)
    stop_price = _round_price(float(stop_price), instrument_info)
    tp1 = _round_price(float(tp1), instrument_info)
    tp2 = _round_price(float(tp2), instrument_info)

    safety_reasons = _safety_rejections(
        side=side,
        entry_price=entry_price,
        stop_price=stop_price,
        tp1=tp1,
        setup=setup,
        alert=alert,
        leverage=leverage,
        has_existing_order=has_existing_order,
        has_existing_position=has_existing_position,
    )
    if safety_reasons:
        return _reject(base_plan, safety_reasons[0], safety_reasons)

    stop_distance = abs(entry_price - stop_price)
    stop_distance_percent = stop_distance / entry_price * 100 if entry_price > 0 else 0
    risk_usdt = account_balance_usdt * (config.ACCOUNT_RISK_PERCENT / 100)
    if stop_distance_percent <= 0:
        return _reject(base_plan, "stop distance <= 0")

    position_size_usdt = risk_usdt / (stop_distance_percent / 100)
    if position_size_usdt > config.MAX_POSITION_USDT:
        return _reject(base_plan, "position size выше лимита")
    if position_size_usdt < config.MIN_POSITION_USDT:
        return _reject(base_plan, "position size ниже минимума")

    qty = _round_qty(position_size_usdt / entry_price, instrument_info)
    min_qty = _min_qty(instrument_info)
    if qty <= 0:
        return _reject(base_plan, "qty <= 0")
    if min_qty is not None and qty < min_qty:
        return _reject(base_plan, f"qty меньше minOrderQty ({min_qty:g})")

    position_size_usdt = qty * entry_price
    fee_mode = _fee_mode()
    fee_rate = _fee_rate_for_mode(fee_mode)
    estimated_entry_fee = position_size_usdt * fee_rate
    estimated_exit_fee_tp1 = qty * tp1 * fee_rate
    estimated_exit_fee_tp2 = qty * tp2 * fee_rate
    estimated_stop_fee = qty * stop_price * fee_rate
    estimated_fee_usdt = estimated_entry_fee + estimated_exit_fee_tp1
    estimated_slippage_usdt = position_size_usdt * (config.SLIPPAGE_PERCENT / 100)
    gross_risk_usdt = abs(_profit_usdt(side, entry_price, stop_price, position_size_usdt))
    gross_reward_tp1 = _profit_usdt(side, entry_price, tp1, position_size_usdt)
    gross_reward_tp2 = _profit_usdt(side, entry_price, tp2, position_size_usdt)
    max_loss_usdt = gross_risk_usdt + estimated_entry_fee + estimated_stop_fee + estimated_slippage_usdt
    tp1_profit_usdt = gross_reward_tp1 - estimated_entry_fee - estimated_exit_fee_tp1 - estimated_slippage_usdt
    tp2_profit_usdt = gross_reward_tp2 - estimated_entry_fee - estimated_exit_fee_tp2 - estimated_slippage_usdt
    rr_tp1 = abs(tp1 - entry_price) / stop_distance if stop_distance > 0 else 0
    rr_tp2 = abs(tp2 - entry_price) / stop_distance if stop_distance > 0 else 0
    net_r_tp1 = tp1_profit_usdt / gross_risk_usdt if gross_risk_usdt > 0 else 0
    net_r_tp2 = tp2_profit_usdt / gross_risk_usdt if gross_risk_usdt > 0 else 0
    net_r_stop = -max_loss_usdt / gross_risk_usdt if gross_risk_usdt > 0 else -1

    if not ignore_min_rr and rr_tp1 < config.MIN_RR_TO_ALLOW_ORDER:
        return _reject(base_plan, "R/R ниже минимального порога")
    if not ignore_min_rr and net_r_tp1 < config.MIN_RR_TO_ALLOW_ORDER:
        return _reject(base_plan, "R/R после комиссий ниже минимума")

    base_plan.update(
        {
            "allowed": True,
            "side": side,
            "bybit_side": "Buy" if side == "LONG" else "Sell",
            "entry_price": entry_price,
            "entry_low": _round_price(entry_low, instrument_info),
            "entry_high": _round_price(entry_high, instrument_info),
            "stop_price": stop_price,
            "tp1": tp1,
            "tp2": tp2,
            "risk_usdt": risk_usdt,
            "risk_percent": config.ACCOUNT_RISK_PERCENT,
            "stop_distance_percent": stop_distance_percent,
            "position_size_usdt": position_size_usdt,
            "qty": qty,
            "gross_risk_usdt": gross_risk_usdt,
            "gross_reward_tp1": gross_reward_tp1,
            "gross_reward_tp2": gross_reward_tp2,
            "fee_mode": fee_mode,
            "fee_rate": fee_rate,
            "estimated_entry_fee": estimated_entry_fee,
            "estimated_exit_fee_tp1": estimated_exit_fee_tp1,
            "estimated_exit_fee_tp2": estimated_exit_fee_tp2,
            "estimated_stop_fee": estimated_stop_fee,
            "estimated_fee_usdt": estimated_fee_usdt,
            "estimated_slippage_usdt": estimated_slippage_usdt,
            "max_loss_usdt": max_loss_usdt,
            "tp1_profit_usdt": tp1_profit_usdt,
            "tp2_profit_usdt": tp2_profit_usdt,
            "rr_tp1": rr_tp1,
            "rr_tp2": rr_tp2,
            "net_r_tp1": net_r_tp1,
            "net_r_tp2": net_r_tp2,
            "net_r_stop": net_r_stop,
            "safety": _safety_lines(has_existing_order, has_existing_position),
            "status": "PLANNED",
        }
    )
    return base_plan


def format_order_plan(plan: dict[str, Any]) -> str:
    if not plan.get("allowed"):
        reasons = plan.get("reasons") or ["неизвестная причина"]
        return "\n".join(["❌ Ордер отклонён", f"Причина: {reasons[0]}"])

    safety = plan.get("safety") or []
    lines = [
        "🧮 План ордера",
        "",
        f"Монета: {plan.get('symbol')}",
        f"Направление: {_direction_ru(plan.get('side'))}",
        f"Режим: {plan.get('mode')}",
        "",
        f"Entry limit: {_fmt_price(plan.get('entry_price'))}",
        f"Stop: {_fmt_price(plan.get('stop_price'))}",
        f"TP1: {_fmt_price(plan.get('tp1'))}",
        f"TP2: {_fmt_price(plan.get('tp2'))}",
        "",
        f"Баланс: {plan.get('account_balance_usdt', 0):.2f} USDT",
        f"Риск: {plan.get('risk_percent', 0):g}% = {plan.get('risk_usdt', 0):.2f} USDT",
        f"Плечо: {plan.get('leverage')}x",
        f"Размер позиции: {plan.get('position_size_usdt', 0):.2f} USDT",
        f"Qty: {_fmt_qty(plan.get('qty'))}",
        "",
        "💸 Fees:",
        f"Mode: {plan.get('fee_mode')}",
        f"Entry fee estimate: {plan.get('estimated_entry_fee', 0):.4f} USDT",
        f"TP1 exit fee estimate: {plan.get('estimated_exit_fee_tp1', 0):.4f} USDT",
        f"TP2 exit fee estimate: {plan.get('estimated_exit_fee_tp2', 0):.4f} USDT",
        f"Stop exit fee estimate: {plan.get('estimated_stop_fee', 0):.4f} USDT",
        f"Проскальзывание estimate: {plan.get('estimated_slippage_usdt', 0):.4f} USDT",
        f"Макс убыток estimate: {plan.get('max_loss_usdt', 0):.4f} USDT",
        f"TP1 estimate: {plan.get('tp1_profit_usdt', 0):.4f} USDT",
        f"TP2 estimate: {plan.get('tp2_profit_usdt', 0):.4f} USDT",
        "",
        "Net R:",
        f"TP1 gross: {plan.get('rr_tp1', 0):.2f}R",
        f"TP1 net: {plan.get('net_r_tp1', 0):.2f}R",
        f"TP2 gross: {plan.get('rr_tp2', 0):.2f}R",
        f"TP2 net: {plan.get('net_r_tp2', 0):.2f}R",
        f"Stop net: {plan.get('net_r_stop', 0):.2f}R",
        "",
        f"Execution:\n{_execution_label(plan.get('execution_status'))}",
        "",
        "Safety:",
    ]
    lines.extend(safety or ["✅ Проверки пройдены"])
    return "\n".join(lines)


def _setup_view(alert: dict[str, Any]) -> dict[str, Any]:
    return {
        "setup_bias": alert.get("setup_bias") or alert.get("bias"),
        "setup_status": alert.get("setup_status") or alert.get("status"),
        "execution_status": alert.get("execution_status"),
        "entry_zone": alert.get("entry_zone"),
        "invalidation": alert.get("invalidation"),
        "tp1": alert.get("tp1"),
        "tp2": alert.get("tp2"),
    }


def _safety_rejections(
    side: str,
    entry_price: float,
    stop_price: float,
    tp1: float,
    setup: dict[str, Any],
    alert: dict[str, Any],
    leverage: int,
    has_existing_order: bool,
    has_existing_position: bool,
) -> list[str]:
    reasons = []
    if side == "LONG" and stop_price >= entry_price:
        reasons.append("stop distance <= 0")
    if side == "SHORT" and stop_price <= entry_price:
        reasons.append("stop distance <= 0")
    if entry_price <= 0:
        reasons.append("entry price <= 0")
    if leverage > config.MAX_LEVERAGE:
        reasons.append("плечо выше MAX_LEVERAGE")
    if setup.get("execution_status") == "TOO_LATE_DO_NOT_CHASE":
        reasons.append("цена ушла от зоны, догонять нельзя")
    if setup.get("setup_status") == "NO SETUP":
        reasons.append("setup_status = NO SETUP")
    if str(alert.get("execution_quality") or "") in {"FAST_MOVE", "MAYBE_NOT_EXECUTABLE"}:
        reasons.append("сетап только FAST_MOVE, руками можно было не успеть")
    risk_level = str(alert.get("risk_level") or "")
    if risk_level == "EXTREME":
        reasons.append("risk_level = EXTREME")
    if risk_level == "HIGH" and not config.ALLOW_HIGH_RISK_ORDERS:
        reasons.append("risk_level = HIGH, ALLOW_HIGH_RISK_ORDERS=false")
    if has_existing_order:
        reasons.append("уже есть активный ордер по монете")
    if has_existing_position:
        reasons.append("уже есть открытая позиция по монете")
    if side == "LONG" and tp1 <= entry_price:
        reasons.append("TP1 не выше entry для LONG")
    if side == "SHORT" and tp1 >= entry_price:
        reasons.append("TP1 не ниже entry для SHORT")
    return reasons


def _safety_lines(has_existing_order: bool, has_existing_position: bool) -> list[str]:
    return [
        "✅ Цена не убежала",
        "✅ R/R нормальный",
        "✅ Нет открытой позиции" if not has_existing_position else "❌ Есть открытая позиция",
        "✅ Нет открытого ордера" if not has_existing_order else "❌ Есть открытый ордер",
    ]


def _reject(plan: dict[str, Any], reason: str, reasons: list[str] | None = None) -> dict[str, Any]:
    plan["allowed"] = False
    plan["reasons"] = reasons or [reason]
    return plan


def _direction_from_bias(value: Any) -> str | None:
    text = str(value or "")
    if "LONG" in text:
        return "LONG"
    if "SHORT" in text:
        return "SHORT"
    return None


def _profit_usdt(side: str, entry: float, target: float, position_size_usdt: float) -> float:
    if side == "LONG":
        return ((target - entry) / entry) * position_size_usdt
    return ((entry - target) / entry) * position_size_usdt


def _round_qty(qty: float, instrument_info: dict[str, Any]) -> float:
    step = parse_price_value((instrument_info.get("lotSizeFilter") or {}).get("qtyStep"))
    if step is None or step <= 0:
        return qty
    return math.floor(qty / step) * step


def _round_price(price: float, instrument_info: dict[str, Any]) -> float:
    tick = parse_price_value((instrument_info.get("priceFilter") or {}).get("tickSize"))
    if tick is None or tick <= 0:
        return float(price)
    return round(round(price / tick) * tick, _decimal_places(tick))


def _min_qty(instrument_info: dict[str, Any]) -> float | None:
    return parse_price_value((instrument_info.get("lotSizeFilter") or {}).get("minOrderQty"))


def _decimal_places(value: float) -> int:
    text = f"{value:.12f}".rstrip("0")
    if "." not in text:
        return 0
    return len(text.split(".", 1)[1])


def _mode_label() -> str:
    if not config.BYBIT_TRADING_ENABLED:
        return "PAPER"
    return "TESTNET" if config.BYBIT_TESTNET else "REAL"


def _fee_mode() -> str:
    mode = str(getattr(config, "FEE_MODE", config.DEFAULT_EXECUTION_FEE_MODE) or "maker").lower()
    return mode if mode in {"maker", "taker", "worst_case"} else "maker"


def _fee_rate_for_mode(mode: str) -> float:
    if mode == "maker":
        return config.BYBIT_MAKER_FEE_RATE
    if mode == "worst_case":
        return max(config.BYBIT_MAKER_FEE_RATE, config.BYBIT_TAKER_FEE_RATE)
    return config.BYBIT_TAKER_FEE_RATE


def _direction_ru(value: Any) -> str:
    return "ЛОНГ" if value == "LONG" else "ШОРТ" if value == "SHORT" else "НЕИЗВЕСТНО"


def _execution_label(value: Any) -> str:
    labels = {
        "ENTERABLE_NOW": "🟢 МОЖНО СМОТРЕТЬ ВХОД СЕЙЧАС",
        "PENDING_LIMIT_ONLY": "🟡 ТОЛЬКО ЛИМИТКА / НЕ ВХОДИТЬ ПО РЫНКУ",
        "TOO_LATE_DO_NOT_CHASE": "🔴 ПОЕЗД УШЁЛ / НЕ ДОГОНЯТЬ",
        "NO_SETUP": "⚪ НЕТ СЕТАПА",
    }
    return labels.get(str(value or ""), "⚪ НЕИЗВЕСТНО")


def _fmt_price(value: Any) -> str:
    number = parse_price_value(value)
    if number is None:
        return "n/a"
    if number >= 100:
        return f"{number:.2f}"
    if number >= 1:
        return f"{number:.4f}"
    if number >= 0.01:
        return f"{number:.6f}"
    return f"{number:.8f}"


def _fmt_qty(value: Any) -> str:
    number = parse_price_value(value)
    if number is None:
        return "n/a"
    return f"{number:.8f}".rstrip("0").rstrip(".")

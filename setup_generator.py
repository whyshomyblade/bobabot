from typing import Any

import config


WEAK_SETUP_REASON = "Radar activity detected, but move/OI/volatility are too weak for a trade setup."
WEAK_SETUP_WARNING = "No setup generated. Avoid trading market noise."


def generate_setup(
    symbol: str,
    last_price: float,
    price_change_15m: float,
    oi_change_15m: float,
    volume_spike: float,
    funding_rate: float | None,
    alert_type: str,
    risk_level: str,
    indicator_context: dict[str, Any] | None,
    candles: list[Any] | None,
) -> dict[str, Any]:
    indicator_context = indicator_context or {}
    rsi = _to_float(indicator_context.get("rsi"))
    ema_trend = indicator_context.get("ema_trend")
    macd_histogram = _to_float(indicator_context.get("macd_histogram"))

    if alert_type == "Late Pump / Chase Risk":
        return {
            "bias": "WAIT",
            "setup_status": "NO CHASE",
            "execution_status": "TOO_LATE_DO_NOT_CHASE",
            "execution_label": "🔴 ПОЕЗД УШЁЛ / НЕ ДОГОНЯТЬ",
            "execution_short_label": "TOO LATE",
            "current_price": _format_price(last_price),
            "distance_to_entry_percent": None,
            "distance_to_entry": "n/a",
            "plan": "Цена уже ушла от зоны. Догонять нельзя.",
            "entry_zone": "No entry. Wait for structure reset.",
            "entry_distance": "n/a",
            "setup_entry_distance_percent": None,
            "invalidation": "n/a",
            "tp1": "n/a",
            "tp2": "n/a",
            "risk_reward": "n/a",
            "reason": "Strong move already expanded. Pullback or retest is required before any manual plan.",
            "warning": "Late pump. Market entry is dangerous.",
        }

    if alert_type == "Low-Quality Radar Activity":
        return _weak_no_setup()

    if alert_type == "Radar Activity":
        return _no_setup("Radar activity detected, but conditions are not clean enough.")

    atr = _to_float(indicator_context.get("atr"))
    if atr is None and candles:
        atr = _fallback_atr_from_candles(candles, config.ATR_PERIOD)

    if last_price <= 0 or atr is None or atr <= 0:
        return _weak_no_setup()

    atr_percent = _atr_percent(last_price, atr)

    if alert_type == "Possible Short Squeeze":
        if not _squeeze_quality_ok(
            price_change_15m=price_change_15m,
            oi_change_15m=oi_change_15m,
            volume_spike=volume_spike,
            funding_rate=funding_rate,
            direction="short",
        ):
            return _weak_no_setup()

        return _long_setup(
            last_price=last_price,
            atr=atr,
            entry_multiplier=0.25,
            bias="LONG WATCH",
            status="WAIT FOR PULLBACK",
            reason="Price and OI are rising while funding is negative. Pullback structure is needed before considering a manual long scenario.",
            warning="Squeeze setups are volatile. Avoid chasing vertical candles.",
            risk_level=risk_level,
        )

    if alert_type == "Possible Long Squeeze":
        if not _squeeze_quality_ok(
            price_change_15m=price_change_15m,
            oi_change_15m=oi_change_15m,
            volume_spike=volume_spike,
            funding_rate=funding_rate,
            direction="long",
        ):
            return _weak_no_setup()

        return _short_setup(
            last_price=last_price,
            atr=atr,
            entry_multiplier=0.25,
            bias="SHORT WATCH",
            status="WAIT FOR PULLBACK",
            reason="Price is falling while OI rises and funding is positive. Pullback structure is needed before considering a manual short scenario.",
            warning="Squeeze setups are volatile. Avoid shorting after an extended dump.",
            risk_level=risk_level,
        )

    if alert_type == "Overbought Volume Breakout":
        if not _base_setup_quality_ok(price_change_15m, oi_change_15m, volume_spike, atr_percent):
            return _weak_no_setup()

        min_pullback = config.MIN_PULLBACK_PERCENT_HIGH_RISK
        if (
            (rsi is not None and rsi >= config.EXTREME_OVERBOUGHT_RSI_LEVEL)
            or volume_spike >= config.EXTREME_VOLUME_SPIKE_LEVEL
        ):
            min_pullback = config.MIN_PULLBACK_PERCENT_EXTREME_RISK

        return _long_setup(
            last_price=last_price,
            atr=atr,
            entry_multiplier=0.35,
            bias="LONG WATCH",
            status="WAIT FOR DEEP PULLBACK",
            reason="Overbought volume breakout. Pullback required before considering long scenario.",
            warning="Overbought breakout. Do not chase near market price. Wait for deeper pullback or retest.",
            risk_level=risk_level,
            min_pullback_percent=min_pullback,
        )

    if alert_type == "Oversold Volume Breakdown":
        if not _base_setup_quality_ok(price_change_15m, oi_change_15m, volume_spike, atr_percent):
            return _weak_no_setup()

        min_pullback = config.MIN_PULLBACK_PERCENT_HIGH_RISK
        if (
            (rsi is not None and rsi <= config.EXTREME_OVERSOLD_RSI_LEVEL)
            or volume_spike >= config.EXTREME_VOLUME_SPIKE_LEVEL
        ):
            min_pullback = config.MIN_PULLBACK_PERCENT_EXTREME_RISK

        return _short_setup(
            last_price=last_price,
            atr=atr,
            entry_multiplier=0.35,
            bias="SHORT WATCH",
            status="WAIT FOR DEEP PULLBACK",
            reason="Oversold volume breakdown. Pullback required before considering short scenario.",
            warning="Oversold breakdown. Do not chase near market price. Wait for deeper pullback or retest.",
            risk_level=risk_level,
            min_pullback_percent=min_pullback,
        )

    if alert_type == "Clean Bullish Momentum":
        if not _clean_bullish_quality_ok(
            price_change_15m=price_change_15m,
            oi_change_15m=oi_change_15m,
            volume_spike=volume_spike,
            atr_percent=atr_percent,
            rsi=rsi,
            ema_trend=ema_trend,
            macd_histogram=macd_histogram,
        ):
            return _weak_no_setup("Bullish indicator alignment exists, but price/OI expansion is too weak.")

        return _long_setup(
            last_price=last_price,
            atr=atr,
            entry_multiplier=0.25,
            bias="LONG",
            status="ACTIVE WATCH",
            reason="Price, OI, EMA and MACD support bullish momentum.",
            warning="This is a scenario, not a signal.",
            risk_level=risk_level,
        )

    if alert_type == "Clean Bearish Pressure":
        if not _clean_bearish_quality_ok(
            price_change_15m=price_change_15m,
            oi_change_15m=oi_change_15m,
            volume_spike=volume_spike,
            atr_percent=atr_percent,
            rsi=rsi,
            ema_trend=ema_trend,
            macd_histogram=macd_histogram,
        ):
            return _weak_no_setup("Bearish indicator alignment exists, but price/OI expansion is too weak.")

        return _short_setup(
            last_price=last_price,
            atr=atr,
            entry_multiplier=0.25,
            bias="SHORT",
            status="ACTIVE WATCH",
            reason="Price, OI, EMA and MACD support bearish pressure.",
            warning="This is a scenario, not a signal.",
            risk_level=risk_level,
        )

    if alert_type == "Mixed Bullish Pressure":
        if not _base_setup_quality_ok(price_change_15m, oi_change_15m, volume_spike, atr_percent):
            return _weak_no_setup("Mixed signal and weak move. No actionable setup.")

        return _long_setup(
            last_price=last_price,
            atr=atr,
            entry_multiplier=0.35,
            bias="LONG WATCH" if price_change_15m >= 0 else "WAIT",
            status="HIGH RISK ONLY",
            reason="Price and OI show pressure, but indicators conflict. Conservative pullback confirmation is required.",
            warning="Indicators conflict. Confirmation required.",
            risk_level=risk_level,
        )

    if alert_type == "Mixed Bearish Pressure":
        if not _base_setup_quality_ok(price_change_15m, oi_change_15m, volume_spike, atr_percent):
            return _weak_no_setup("Mixed signal and weak move. No actionable setup.")

        return _short_setup(
            last_price=last_price,
            atr=atr,
            entry_multiplier=0.35,
            bias="SHORT WATCH" if price_change_15m < 0 else "WAIT",
            status="HIGH RISK ONLY",
            reason="Price and OI show pressure, but indicators conflict. Conservative pullback confirmation is required.",
            warning="Indicators conflict. Confirmation required.",
            risk_level=risk_level,
        )

    return _no_setup("Radar activity detected, but conditions are not clean enough.")


def _long_setup(
    last_price: float,
    atr: float,
    entry_multiplier: float,
    bias: str,
    status: str,
    reason: str,
    warning: str,
    risk_level: str,
    min_pullback_percent: float | None = None,
) -> dict[str, Any]:
    min_pullback = _min_pullback_percent(risk_level, min_pullback_percent)
    atr_based_high = last_price - atr * entry_multiplier
    percent_based_high = last_price * (1 - min_pullback / 100)
    entry_high = min(atr_based_high, percent_based_high)

    chase_distance = _long_entry_distance_percent(last_price, entry_high)
    if chase_distance < config.MAX_ENTRY_CHASE_DISTANCE_PERCENT:
        entry_high = last_price * (1 - min_pullback / 100)

    atr_range = atr * 0.75
    percent_low = last_price * (1 - (min_pullback + 0.5) / 100)
    if atr_range < last_price * 0.005:
        entry_low = percent_low
    else:
        entry_low = entry_high - atr_range

    entry_mid = (entry_low + entry_high) / 2
    stop = entry_low - config.SETUP_ATR_STOP_MULTIPLIER * atr
    risk = entry_mid - stop
    if entry_low <= 0 or stop <= 0 or risk <= 0:
        return _no_setup("Risk calculation is invalid for this scenario.")
    if config.SETUP_TP1_R_MULTIPLIER < config.MIN_RISK_REWARD:
        return _no_setup("Risk/reward is below the configured minimum.")

    tp1 = entry_mid + risk * config.SETUP_TP1_R_MULTIPLIER
    tp2 = entry_mid + risk * config.SETUP_TP2_R_MULTIPLIER
    return _build_setup(
        bias=bias,
        status=status,
        direction="LONG",
        last_price=last_price,
        entry_low=entry_low,
        entry_high=entry_high,
        tp1_value=tp1,
        entry_zone=_format_zone(entry_low, entry_high),
        entry_distance=_format_entry_distance(_long_entry_distance_percent(last_price, entry_high), "below"),
        invalidation=f"below {_format_price(stop)}",
        tp1=_format_price(tp1),
        tp2=_format_price(tp2),
        reason=reason,
        warning=warning,
    )


def _short_setup(
    last_price: float,
    atr: float,
    entry_multiplier: float,
    bias: str,
    status: str,
    reason: str,
    warning: str,
    risk_level: str,
    min_pullback_percent: float | None = None,
) -> dict[str, Any]:
    min_pullback = _min_pullback_percent(risk_level, min_pullback_percent)
    atr_based_low = last_price + atr * entry_multiplier
    percent_based_low = last_price * (1 + min_pullback / 100)
    entry_low = max(atr_based_low, percent_based_low)

    chase_distance = _short_entry_distance_percent(last_price, entry_low)
    if chase_distance < config.MAX_ENTRY_CHASE_DISTANCE_PERCENT:
        entry_low = last_price * (1 + min_pullback / 100)

    atr_range = atr * 0.75
    percent_high = last_price * (1 + (min_pullback + 0.5) / 100)
    if atr_range < last_price * 0.005:
        entry_high = percent_high
    else:
        entry_high = entry_low + atr_range

    entry_mid = (entry_low + entry_high) / 2
    stop = entry_high + config.SETUP_ATR_STOP_MULTIPLIER * atr
    risk = stop - entry_mid
    if risk <= 0:
        return _no_setup("Risk calculation is invalid for this scenario.")
    if config.SETUP_TP1_R_MULTIPLIER < config.MIN_RISK_REWARD:
        return _no_setup("Risk/reward is below the configured minimum.")

    tp1 = entry_mid - risk * config.SETUP_TP1_R_MULTIPLIER
    tp2 = entry_mid - risk * config.SETUP_TP2_R_MULTIPLIER
    if tp1 <= 0 or tp2 <= 0:
        return _no_setup("Risk calculation is invalid for this scenario.")
    return _build_setup(
        bias=bias,
        status=status,
        direction="SHORT",
        last_price=last_price,
        entry_low=entry_low,
        entry_high=entry_high,
        tp1_value=tp1,
        entry_zone=_format_zone(entry_low, entry_high),
        entry_distance=_format_entry_distance(_short_entry_distance_percent(last_price, entry_low), "above"),
        invalidation=f"above {_format_price(stop)}",
        tp1=_format_price(tp1),
        tp2=_format_price(tp2),
        reason=reason,
        warning=warning,
    )


def _build_setup(
    bias: str,
    status: str,
    direction: str,
    last_price: float,
    entry_low: float,
    entry_high: float,
    tp1_value: float,
    entry_zone: str,
    entry_distance: str,
    invalidation: str,
    tp1: str,
    tp2: str,
    reason: str,
    warning: str,
) -> dict[str, Any]:
    distance_percent = _extract_distance_percent(entry_distance)
    execution = _classify_execution(
        direction=direction,
        status=status,
        last_price=last_price,
        entry_low=entry_low,
        entry_high=entry_high,
        tp1_value=tp1_value,
    )
    return {
        "bias": bias,
        "setup_status": status,
        "execution_status": execution["execution_status"],
        "execution_label": execution["execution_label"],
        "execution_short_label": execution["execution_short_label"],
        "current_price": _format_price(last_price),
        "distance_to_entry_percent": execution["distance_to_entry_percent"],
        "distance_to_entry": execution["distance_to_entry"],
        "plan": _execution_plan(bias, status, execution["execution_status"]),
        "entry_zone": entry_zone,
        "entry_distance": entry_distance,
        "setup_entry_distance_percent": distance_percent,
        "invalidation": invalidation,
        "tp1": tp1,
        "tp2": tp2,
        "risk_reward": (
            f"approx {config.SETUP_TP1_R_MULTIPLIER:g}R / "
            f"{config.SETUP_TP2_R_MULTIPLIER:g}R"
        ),
        "reason": reason,
        "warning": warning,
    }


def _weak_no_setup(reason: str = WEAK_SETUP_REASON) -> dict[str, Any]:
    return _no_setup(reason, WEAK_SETUP_WARNING)


def _no_setup(reason: str, warning: str = "Wait for better structure.") -> dict[str, Any]:
    return {
        "bias": "WAIT",
        "setup_status": "NO SETUP",
        "execution_status": "NO_SETUP",
        "execution_label": "⚪ НЕТ СЕТАПА",
        "execution_short_label": "NO SETUP",
        "current_price": "n/a",
        "distance_to_entry_percent": None,
        "distance_to_entry": "n/a",
        "plan": "Нет торгового сценария. Ничего не делать.",
        "entry_zone": "n/a",
        "entry_distance": "n/a",
        "setup_entry_distance_percent": None,
        "invalidation": "n/a",
        "tp1": "n/a",
        "tp2": "n/a",
        "risk_reward": "n/a",
        "reason": reason,
        "warning": warning,
    }


def _base_setup_quality_ok(
    price_change_15m: float,
    oi_change_15m: float,
    volume_spike: float,
    atr_percent: float,
) -> bool:
    return (
        abs(price_change_15m) >= config.SETUP_MIN_PRICE_CHANGE_PERCENT
        and oi_change_15m >= config.SETUP_MIN_OI_CHANGE_PERCENT
        and volume_spike >= config.SETUP_MIN_VOLUME_SPIKE
        and atr_percent >= config.SETUP_MIN_ATR_PERCENT
    )


def _clean_bullish_quality_ok(
    price_change_15m: float,
    oi_change_15m: float,
    volume_spike: float,
    atr_percent: float,
    rsi: float | None,
    ema_trend: str | None,
    macd_histogram: float | None,
) -> bool:
    return (
        price_change_15m >= config.SETUP_CLEAN_MIN_PRICE_CHANGE_PERCENT
        and oi_change_15m >= config.SETUP_CLEAN_MIN_OI_CHANGE_PERCENT
        and volume_spike >= config.SETUP_CLEAN_MIN_VOLUME_SPIKE
        and atr_percent >= config.SETUP_MIN_ATR_PERCENT
        and rsi is not None
        and rsi < config.OVERBOUGHT_RSI_LEVEL
        and ema_trend == "bullish"
        and macd_histogram is not None
        and macd_histogram > 0
    )


def _clean_bearish_quality_ok(
    price_change_15m: float,
    oi_change_15m: float,
    volume_spike: float,
    atr_percent: float,
    rsi: float | None,
    ema_trend: str | None,
    macd_histogram: float | None,
) -> bool:
    return (
        price_change_15m <= -config.SETUP_CLEAN_MIN_PRICE_CHANGE_PERCENT
        and oi_change_15m >= config.SETUP_CLEAN_MIN_OI_CHANGE_PERCENT
        and volume_spike >= config.SETUP_CLEAN_MIN_VOLUME_SPIKE
        and atr_percent >= config.SETUP_MIN_ATR_PERCENT
        and rsi is not None
        and rsi > config.OVERSOLD_RSI_LEVEL
        and ema_trend == "bearish"
        and macd_histogram is not None
        and macd_histogram < 0
    )


def _squeeze_quality_ok(
    price_change_15m: float,
    oi_change_15m: float,
    volume_spike: float,
    funding_rate: float | None,
    direction: str,
) -> bool:
    if funding_rate is None:
        return False

    min_price_change = (
        0.5 if config.SETUP_ALLOW_WEAK_SQUEEZE else config.SETUP_MIN_PRICE_CHANGE_PERCENT
    )
    base_ok = (
        abs(price_change_15m) >= min_price_change
        and oi_change_15m >= config.SETUP_MIN_OI_CHANGE_PERCENT
        and volume_spike >= config.SETUP_MIN_VOLUME_SPIKE
    )
    if not base_ok:
        return False

    if direction == "short":
        return price_change_15m > 0 and funding_rate <= -0.02
    if direction == "long":
        return price_change_15m < 0 and funding_rate >= 0.02
    return False


def _atr_percent(last_price: float, atr: float) -> float:
    if last_price <= 0:
        return 0.0
    return (atr / last_price) * 100


def _classify_execution(
    direction: str,
    status: str,
    last_price: float,
    entry_low: float,
    entry_high: float,
    tp1_value: float,
) -> dict[str, Any]:
    if entry_low <= last_price <= entry_high:
        return _execution_result("ENTERABLE_NOW", 0.0)

    if direction == "LONG":
        distance = _long_entry_distance_percent(last_price, entry_high)
        if _long_move_is_missed(last_price, tp1_value):
            return _execution_result("TOO_LATE_DO_NOT_CHASE", distance)
        if last_price > entry_high:
            return _execution_result("PENDING_LIMIT_ONLY", distance)
        return _execution_result("PENDING_LIMIT_ONLY", _long_entry_distance_percent(entry_low, last_price))

    distance = _short_entry_distance_percent(last_price, entry_low)
    if _short_move_is_missed(last_price, tp1_value):
        return _execution_result("TOO_LATE_DO_NOT_CHASE", distance)
    if last_price < entry_low:
        return _execution_result("PENDING_LIMIT_ONLY", distance)
    return _execution_result("PENDING_LIMIT_ONLY", _short_entry_distance_percent(entry_high, last_price))


def _execution_result(status: str, distance_percent: float | None) -> dict[str, Any]:
    labels = {
        "ENTERABLE_NOW": ("🟢 МОЖНО СМОТРЕТЬ ВХОД СЕЙЧАС", "ENTERABLE"),
        "PENDING_LIMIT_ONLY": ("🟡 ТОЛЬКО ЛИМИТКА В ЗОНЕ / НЕ ВХОДИТЬ ПО РЫНКУ", "LIMIT ONLY"),
        "TOO_LATE_DO_NOT_CHASE": ("🔴 ПОЕЗД УШЁЛ / НЕ ДОГОНЯТЬ", "TOO LATE"),
        "NO_SETUP": ("⚪ НЕТ СЕТАПА", "NO SETUP"),
    }
    label, short_label = labels.get(status, labels["NO_SETUP"])
    return {
        "execution_status": status,
        "execution_label": label,
        "execution_short_label": short_label,
        "distance_to_entry_percent": distance_percent,
        "distance_to_entry": "n/a" if distance_percent is None else f"{distance_percent:.2f}%",
    }


def _execution_plan(bias: str, status: str, execution_status: str) -> str:
    if execution_status == "NO_SETUP":
        return "Нет сетапа. Ничего не делать."
    if execution_status == "TOO_LATE_DO_NOT_CHASE":
        return "Цена уже ушла от зоны. Догонять нельзя."
    if status in {"WAIT FOR PULLBACK", "WAIT FOR DEEP PULLBACK"}:
        limit_plan = "Лимитный план: ждать цену в зоне входа."
        if "LONG WATCH" in bias:
            limit_plan = "Лимитный план: ждать цену в зоне входа. Не покупать после импульса."
        elif "SHORT WATCH" in bias:
            limit_plan = "Лимитный план: ждать цену в зоне входа. Не шортить после импульса."
        return "\n".join(
            [
                "НЕ ВХОДИТЬ ПО РЫНКУ. Только ждать откат в зону или ставить лимитку.",
                limit_plan,
                "Ждать откат в Entry Zone.",
                "Идея отменяется, если цена не даст откат или сломает структуру.",
            ]
        )
    if "LONG WATCH" in bias:
        return "Лимитный план: ждать цену в зоне входа. Не покупать после импульса."
    if "SHORT WATCH" in bias:
        return "Лимитный план: ждать цену в зоне входа. Не шортить после импульса."
    if execution_status != "ENTERABLE_NOW":
        return "Сценарий не в зоне входа. Не входить по рынку; ждать цену в Entry Zone."
    return "Цена в зоне входа. Всё равно проверить график вручную и не считать это торговым сигналом."


def _long_move_is_missed(last_price: float, tp1_value: float) -> bool:
    if last_price <= 0:
        return False
    if last_price >= tp1_value:
        return True
    distance_to_tp1 = ((tp1_value - last_price) / last_price) * 100
    return distance_to_tp1 <= config.MAX_CHASE_DISTANCE_PERCENT


def _short_move_is_missed(last_price: float, tp1_value: float) -> bool:
    if last_price <= 0:
        return False
    if last_price <= tp1_value:
        return True
    distance_to_tp1 = ((last_price - tp1_value) / last_price) * 100
    return distance_to_tp1 <= config.MAX_CHASE_DISTANCE_PERCENT


def _min_pullback_percent(
    risk_level: str,
    override: float | None = None,
) -> float:
    if risk_level == "EXTREME":
        base = config.MIN_PULLBACK_PERCENT_EXTREME_RISK
    elif risk_level == "HIGH":
        base = config.MIN_PULLBACK_PERCENT_HIGH_RISK
    else:
        base = config.MIN_PULLBACK_PERCENT_NORMAL

    values = [base, config.MAX_ENTRY_CHASE_DISTANCE_PERCENT]
    if override is not None:
        values.append(override)
    return max(values)


def _long_entry_distance_percent(last_price: float, entry_high: float) -> float:
    if last_price <= 0:
        return 0.0
    return max(((last_price - entry_high) / last_price) * 100, 0.0)


def _short_entry_distance_percent(last_price: float, entry_low: float) -> float:
    if last_price <= 0:
        return 0.0
    return max(((entry_low - last_price) / last_price) * 100, 0.0)


def _format_entry_distance(distance_percent: float, direction: str) -> str:
    return f"~{distance_percent:.1f}% {direction} current price"


def _extract_distance_percent(entry_distance: str) -> float | None:
    if not entry_distance or entry_distance == "n/a":
        return None
    try:
        return float(entry_distance.split("%", 1)[0].replace("~", ""))
    except (IndexError, ValueError):
        return None


def _format_zone(price_a: float, price_b: float) -> str:
    low = min(price_a, price_b)
    high = max(price_a, price_b)
    return f"{_format_price(low)} - {_format_price(high)}"


def _format_price(price: float) -> str:
    if price >= 100:
        return f"{price:.2f}"
    if price >= 1:
        return f"{price:.4f}"
    if price >= 0.01:
        return f"{price:.6f}"
    return f"{price:.8f}"


def _fallback_atr_from_candles(candles: list[Any], period: int) -> float | None:
    normalized = _normalize_candles(candles)
    if len(normalized) <= period:
        return None

    true_ranges = []
    for index in range(1, len(normalized)):
        candle = normalized[index]
        previous_close = normalized[index - 1]["close"]
        true_ranges.append(
            max(
                candle["high"] - candle["low"],
                abs(candle["high"] - previous_close),
                abs(candle["low"] - previous_close),
            )
        )

    if len(true_ranges) < period:
        return None

    atr = sum(true_ranges[:period]) / period
    for true_range in true_ranges[period:]:
        atr = ((atr * (period - 1)) + true_range) / period
    return atr


def _normalize_candles(candles: list[Any]) -> list[dict[str, float]]:
    normalized = []
    for candle in candles:
        parsed = _parse_candle(candle)
        if parsed is not None:
            normalized.append(parsed)
    normalized.sort(key=lambda item: item["timestamp"])
    return normalized


def _parse_candle(candle: Any) -> dict[str, float] | None:
    if isinstance(candle, dict):
        timestamp = _to_float(candle.get("startTime") or candle.get("timestamp") or candle.get("time"))
        high_price = _to_float(candle.get("high") or candle.get("highPrice"))
        low_price = _to_float(candle.get("low") or candle.get("lowPrice"))
        close_price = _to_float(candle.get("close") or candle.get("closePrice"))
    elif isinstance(candle, (list, tuple)) and len(candle) >= 5:
        timestamp = _to_float(candle[0])
        high_price = _to_float(candle[2])
        low_price = _to_float(candle[3])
        close_price = _to_float(candle[4])
    else:
        return None

    if None in {timestamp, high_price, low_price, close_price}:
        return None

    return {
        "timestamp": float(timestamp),
        "high": float(high_price),
        "low": float(low_price),
        "close": float(close_price),
    }


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

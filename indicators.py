from typing import Any

import config


def calculate_ema(values: list[float], period: int) -> float | None:
    series = _ema_series(values, period)
    if not series:
        return None
    return series[-1]


def calculate_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None

    gains = []
    losses = []
    for index in range(1, period + 1):
        change = closes[index] - closes[index - 1]
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))

    average_gain = sum(gains) / period
    average_loss = sum(losses) / period

    for index in range(period + 1, len(closes)):
        change = closes[index] - closes[index - 1]
        gain = max(change, 0.0)
        loss = abs(min(change, 0.0))
        average_gain = ((average_gain * (period - 1)) + gain) / period
        average_loss = ((average_loss * (period - 1)) + loss) / period

    if average_loss == 0:
        return 100.0
    if average_gain == 0:
        return 0.0

    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def calculate_macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, float] | None:
    fast_ema = _ema_series(closes, fast)
    slow_ema = _ema_series(closes, slow)
    if len(fast_ema) != len(closes) or len(slow_ema) != len(closes):
        return None

    macd_values = [
        fast_value - slow_value
        for fast_value, slow_value in zip(fast_ema, slow_ema)
        if fast_value is not None and slow_value is not None
    ]
    if len(macd_values) < signal:
        return None

    signal_values = _ema_series(macd_values, signal)
    signal_value = signal_values[-1] if signal_values else None
    if signal_value is None:
        return None

    macd_value = macd_values[-1]
    histogram = macd_value - signal_value
    return {
        "macd": macd_value,
        "signal": signal_value,
        "histogram": histogram,
    }


def calculate_atr(candles: list[Any], period: int = 14) -> float | None:
    normalized = _normalize_candles(candles)
    if len(normalized) <= period:
        return None

    true_ranges = []
    for index in range(1, len(normalized)):
        candle = normalized[index]
        previous_close = normalized[index - 1]["close"]
        true_range = max(
            candle["high"] - candle["low"],
            abs(candle["high"] - previous_close),
            abs(candle["low"] - previous_close),
        )
        true_ranges.append(true_range)

    if len(true_ranges) < period:
        return None

    atr = sum(true_ranges[:period]) / period
    for true_range in true_ranges[period:]:
        atr = ((atr * (period - 1)) + true_range) / period
    return atr


def analyze_indicator_context(candles: list[Any]) -> dict[str, Any]:
    normalized = _normalize_candles(candles)
    if not normalized:
        raise ValueError("no valid candles")

    closes = [candle["close"] for candle in normalized]
    last_price = closes[-1]

    rsi = calculate_rsi(closes, config.RSI_PERIOD)
    macd = calculate_macd(
        closes,
        config.MACD_FAST_PERIOD,
        config.MACD_SLOW_PERIOD,
        config.MACD_SIGNAL_PERIOD,
    )
    ema_fast = calculate_ema(closes, config.EMA_FAST_PERIOD)
    ema_slow = calculate_ema(closes, config.EMA_SLOW_PERIOD)
    atr = calculate_atr(normalized, config.ATR_PERIOD)

    if rsi is None:
        raise ValueError("not enough candles to calculate RSI")
    if macd is None:
        raise ValueError("not enough candles to calculate MACD")
    if ema_fast is None or ema_slow is None:
        raise ValueError("not enough candles to calculate EMA trend")
    if atr is None:
        raise ValueError("not enough candles to calculate ATR")

    rsi_note = _rsi_note(rsi)
    macd_note = _macd_note(macd["histogram"], last_price)
    ema_trend = _ema_trend(ema_fast, ema_slow, last_price)
    atr_percent = (atr / last_price) * 100 if last_price else 0.0
    risk_note = _risk_note(atr_percent)

    return {
        "rsi": rsi,
        "rsi_note": rsi_note,
        "macd": macd["macd"],
        "macd_signal": macd["signal"],
        "macd_histogram": macd["histogram"],
        "macd_note": macd_note,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "ema_trend": ema_trend,
        "atr": atr,
        "atr_percent": atr_percent,
        "risk_note": risk_note,
        "indicator_summary": _generic_indicator_summary(
            ema_trend,
            rsi_note,
            macd_note,
            risk_note,
        ),
    }


def _ema_series(values: list[float], period: int) -> list[float | None]:
    if period <= 0 or len(values) < period:
        return []

    multiplier = 2 / (period + 1)
    ema_values: list[float | None] = [None] * (period - 1)
    ema = sum(values[:period]) / period
    ema_values.append(ema)

    for value in values[period:]:
        ema = (value - ema) * multiplier + ema
        ema_values.append(ema)

    return ema_values


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
        timestamp = _to_float(
            candle.get("startTime")
            or candle.get("timestamp")
            or candle.get("time")
        )
        open_price = _to_float(candle.get("open") or candle.get("openPrice"))
        high_price = _to_float(candle.get("high") or candle.get("highPrice"))
        low_price = _to_float(candle.get("low") or candle.get("lowPrice"))
        close_price = _to_float(candle.get("close") or candle.get("closePrice"))
    elif isinstance(candle, (list, tuple)) and len(candle) >= 5:
        timestamp = _to_float(candle[0])
        open_price = _to_float(candle[1])
        high_price = _to_float(candle[2])
        low_price = _to_float(candle[3])
        close_price = _to_float(candle[4])
    else:
        return None

    if None in {timestamp, open_price, high_price, low_price, close_price}:
        return None

    return {
        "timestamp": float(timestamp),
        "open": float(open_price),
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


def _rsi_note(rsi: float) -> str:
    if rsi > 70:
        return "overbought"
    if rsi < 30:
        return "oversold"
    if 45 <= rsi <= 55:
        return "neutral"
    return "normal"


def _macd_note(histogram: float, last_price: float) -> str:
    close_to_zero_threshold = max(abs(last_price) * 0.000001, 0.00000001)
    if abs(histogram) <= close_to_zero_threshold:
        return "weak momentum"
    if histogram > 0:
        return "bullish momentum"
    return "bearish momentum"


def _ema_trend(ema_fast: float, ema_slow: float, last_price: float) -> str:
    if ema_fast > ema_slow and last_price > ema_fast:
        return "bullish"
    if ema_fast < ema_slow and last_price < ema_fast:
        return "bearish"
    return "neutral"


def _risk_note(atr_percent: float) -> str:
    if atr_percent > 2.5:
        return "high volatility"
    if atr_percent < 0.5:
        return "low volatility"
    return "normal volatility"


def _generic_indicator_summary(
    ema_trend: str,
    rsi_note: str,
    macd_note: str,
    risk_note: str,
) -> str:
    first = f"EMA trend {ema_trend}"
    second = "MACD weak" if macd_note == "weak momentum" else f"RSI {rsi_note}"
    third = _risk_summary(risk_note)
    return f"{first}. {second}. {third}."


def _risk_summary(risk_note: str) -> str:
    if risk_note == "high volatility":
        return "High volatility"
    if risk_note == "low volatility":
        return "Low volatility"
    return "Volatility normal"

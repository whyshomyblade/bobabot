import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import config


LOGGER = logging.getLogger("SetupTracker")
FINAL_STATES = {
    "TP2_HIT",
    "INVALIDATED",
    "INVALIDATED_BEFORE_TP1",
    "TP1_THEN_INVALIDATED",
    "AMBIGUOUS_INVALIDATION_FIRST",
    "INVALIDATED_FIRST_UNKNOWN",
    "EXPIRED_NO_ENTRY",
    "EXPIRED_AFTER_ENTRY",
}

STATE_LABELS_RU = {
    "WAITING_ENTRY": "ОЖИДАЕТ ВХОДА",
    "ENTERED": "ВХОД АКТИВИРОВАН",
    "TP1_HIT": "TP1 ДОСТИГНУТ",
    "TP2_HIT": "TP2 ДОСТИГНУТ",
    "INVALIDATED": "СЦЕНАРИЙ СЛОМАН",
    "INVALIDATED_BEFORE_TP1": "СЦЕНАРИЙ СЛОМАН ДО TP1",
    "TP1_THEN_INVALIDATED": "TP1 ДОСТИГНУТ, ПОТОМ СЛОМАН",
    "EXPIRED_NO_ENTRY": "ИСТЁК БЕЗ ВХОДА",
    "EXPIRED_AFTER_ENTRY": "ИСТЁК ПОСЛЕ ВХОДА",
    "AMBIGUOUS_INVALIDATION_FIRST": "СПОРНЫЙ ИСХОД / СЧИТАЕМ КАК СЛОМ",
    "INVALIDATED_FIRST_UNKNOWN": "СПОРНЫЙ ИСХОД / СЧИТАЕМ КАК СЛОМ",
}

DIRECTION_LABELS_RU = {
    "LONG": "ЛОНГ",
    "SHORT": "ШОРТ",
}

BIAS_LABELS_RU = {
    "LONG WATCH": "ЛОНГ НАБЛЮДЕНИЕ",
    "SHORT WATCH": "ШОРТ НАБЛЮДЕНИЕ",
    "LONG": "ЛОНГ",
    "SHORT": "ШОРТ",
    "WAIT": "ЖДАТЬ",
}


def translate_setup_state(value: Any) -> str:
    return STATE_LABELS_RU.get(str(value or ""), "НЕИЗВЕСТНО")


def translate_direction(value: Any) -> str:
    return DIRECTION_LABELS_RU.get(str(value or ""), "НЕИЗВЕСТНО")


def translate_bias(value: Any) -> str:
    return BIAS_LABELS_RU.get(str(value or ""), "НЕИЗВЕСТНО")


def setup_has_tp1(record: dict[str, Any]) -> bool:
    return bool(record.get("tp1_hit_at") or record.get("tp1_hit"))


def normalize_final_state(record: dict[str, Any]) -> str:
    final_state = str(record.get("final_state") or record.get("state") or "UNKNOWN")
    if final_state == "INVALIDATED":
        if setup_has_tp1(record):
            return "TP1_THEN_INVALIDATED"
        return "INVALIDATED_BEFORE_TP1"
    if final_state in {"AMBIGUOUS_INVALIDATION_FIRST", "INVALIDATED_FIRST_UNKNOWN"}:
        if setup_has_tp1(record):
            return "TP1_THEN_INVALIDATED"
        return "AMBIGUOUS_INVALIDATION_FIRST"
    return final_state


def result_for_record(record: dict[str, Any]) -> str:
    return _result_for_state(normalize_final_state(record))


def raw_result_r_for_record(record: dict[str, Any]) -> float:
    value = _to_float(record.get("raw_result_r"))
    if value is not None:
        return value
    return _raw_result_r_for_state(normalize_final_state(record))


def fee_adjusted_result_r_for_record(record: dict[str, Any]) -> float:
    value = _to_float(record.get("fee_adjusted_result_r"))
    if value is not None:
        return value
    return _calculate_fee_adjusted_result(record)


def fee_summary_for_record(record: dict[str, Any]) -> str:
    fee_mode = str(record.get("fee_mode") or config.DEFAULT_EXECUTION_FEE_MODE)
    fee_rate = _fee_rate_for_mode(fee_mode)
    return f"{fee_mode} {fee_rate * 100:.3f}% x2"


def slippage_for_record(record: dict[str, Any]) -> float:
    value = _to_float(record.get("slippage_percent"))
    if value is not None:
        return value
    return config.SLIPPAGE_PERCENT


def execution_quality_for_record(record: dict[str, Any]) -> str:
    value = str(record.get("execution_quality") or "")
    if value:
        return value
    return "UNKNOWN"


def execution_quality_label(value: Any) -> str:
    quality = str(value or "UNKNOWN")
    if quality == "REALISTIC":
        return "🟢 РЕАЛИСТИЧНО"
    if quality in {"FAST_MOVE", "MAYBE_NOT_EXECUTABLE", "FAST_MOVE / MAYBE_NOT_EXECUTABLE"}:
        return "⚠️ БЫСТРОЕ ДВИЖЕНИЕ / РУКАМИ МОЖНО БЫЛО НЕ УСПЕТЬ"
    return "⚪ НЕИЗВЕСТНО"


def parse_price_value(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if value in (None, "", "n/a"):
        return None

    matches = re.findall(r"[-+]?\d+(?:\.\d+)?", str(value).replace(",", ""))
    if not matches:
        return None

    try:
        return float(matches[0])
    except ValueError:
        return None


def parse_entry_zone(entry_zone_string: Any) -> tuple[float, float] | None:
    if not entry_zone_string or entry_zone_string == "n/a":
        return None

    matches = re.findall(r"[-+]?\d+(?:\.\d+)?", str(entry_zone_string).replace(",", ""))
    if len(matches) < 2:
        return None

    try:
        first = float(matches[0])
        second = float(matches[1])
    except ValueError:
        return None

    return min(first, second), max(first, second)


def create_active_setup_from_alert(
    alert_record: dict[str, Any],
    setup_record: dict[str, Any],
) -> dict[str, Any] | None:
    if not _is_actionable_setup(setup_record):
        return None

    entry_zone = parse_entry_zone(setup_record.get("entry_zone"))
    invalidation = parse_price_value(setup_record.get("invalidation"))
    tp1 = parse_price_value(setup_record.get("tp1"))
    tp2 = parse_price_value(setup_record.get("tp2"))
    created_price = parse_price_value(alert_record.get("price"))
    if entry_zone is None or None in {invalidation, tp1, tp2, created_price}:
        return None

    bias = str(setup_record.get("bias") or "")
    direction = _direction_from_bias(bias)
    if direction is None:
        return None

    created_at = str(alert_record.get("timestamp") or _iso_now())
    symbol = str(alert_record.get("symbol") or "").strip()
    if not symbol:
        return None

    entry_low, entry_high = entry_zone
    setup_id = _build_setup_id(symbol, created_at, direction)
    return {
        "id": setup_id,
        "created_at": created_at,
        "symbol": symbol,
        "direction": direction,
        "bias": bias,
        "setup_status": setup_record.get("setup_status"),
        "alert_type": alert_record.get("alert_type", "Unknown"),
        "risk_level": alert_record.get("risk_level", "Unknown"),
        "created_price": float(created_price),
        "execution_status": setup_record.get("execution_status"),
        "execution_short_label": setup_record.get("execution_short_label"),
        "current_price_at_alert": parse_price_value(setup_record.get("current_price")),
        "distance_to_entry_percent": _to_float(setup_record.get("distance_to_entry_percent")),
        "entry_low": float(entry_low),
        "entry_high": float(entry_high),
        "invalidation": float(invalidation),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "entry_distance_percent": _to_float(setup_record.get("setup_entry_distance_percent")),
        "state": "WAITING_ENTRY",
        "entered_at": None,
        "entry_price_virtual": None,
        "entry_candle_ts": None,
        "execution_quality": None,
        "tp1_hit_at": None,
        "tp2_hit_at": None,
        "invalidated_at": None,
        "expired_at": None,
        "last_checked_at": None,
        "max_favorable_move_percent": 0,
        "max_adverse_move_percent": 0,
        "notes": [],
    }


def update_active_setups(bybit_client: Any, storage: Any) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not config.ENABLE_SETUP_TRACKING:
        return events

    now_ts = int(time.time())
    last_check_ts = int(storage.state.get("last_setup_tracking_ts") or 0)
    if now_ts - last_check_ts < config.SETUP_TRACKING_CHECK_INTERVAL_SECONDS:
        return events

    LOGGER.info("Setup tracking enabled")
    active_setups = storage.get_active_setups()
    LOGGER.info("Active setups count: %s", len(active_setups))
    if len(active_setups) > config.SETUP_TRACKING_MAX_ACTIVE:
        LOGGER.warning(
            "Active setups count %s exceeds max %s; checking only the oldest allowed records",
            len(active_setups),
            config.SETUP_TRACKING_MAX_ACTIVE,
        )

    now = datetime.now(UTC)
    remaining: list[dict[str, Any]] = []
    checked_count = 0

    for setup in active_setups:
        if checked_count >= config.SETUP_TRACKING_MAX_ACTIVE:
            remaining.append(setup)
            continue

        checked_count += 1
        setup_id = setup.get("id")
        symbol = setup.get("symbol")
        if not setup_id or not symbol:
            continue

        try:
            expired_state = _expired_state(setup, now)
            if expired_state:
                setup["expired_at"] = _iso_now(now)
                journal_record = move_setup_to_journal(
                    setup,
                    expired_state,
                    f"setup expired after {config.SETUP_TRACKING_EXPIRATION_HOURS} hours",
                )
                storage.add_setup_journal_record(journal_record)
                events.append(_event(expired_state, journal_record))
                LOGGER.info("Setup expired: %s", symbol)
                continue

            latest_candle = _fetch_latest_candle(bybit_client, str(symbol))
            if latest_candle is None:
                remaining.append(setup)
                continue

            latest_price = latest_candle["close"]
            setup_events = evaluate_setup_with_latest_market_data(
                setup,
                latest_price,
                latest_candle,
            )
            setup["last_checked_at"] = _iso_now()

            final_event = None
            for setup_event in setup_events:
                if setup_event.get("final_state") in FINAL_STATES:
                    final_event = setup_event
                else:
                    events.append(setup_event)

            if final_event:
                final_state = str(final_event["final_state"])
                journal_record = move_setup_to_journal(
                    setup,
                    final_state,
                    str(final_event.get("reason") or final_state),
                )
                storage.add_setup_journal_record(journal_record)
                events.append(_event(final_state, journal_record))
                _log_final_state(final_state, str(symbol))
            else:
                remaining.append(setup)

        except Exception as exc:
            LOGGER.error("Setup tracking failed for %s: %s", symbol, exc)
            remaining.append(setup)

    storage.state["active_setups"] = remaining
    storage.state["last_setup_tracking_ts"] = now_ts
    storage.save()
    LOGGER.info("Journal records count: %s", len(storage.get_setup_journal(limit=0)))
    return events


def evaluate_setup_with_latest_market_data(
    setup: dict[str, Any],
    latest_price: float,
    latest_candle: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    latest_low, latest_high = _latest_range(latest_price, latest_candle)
    state = setup.get("state") or "WAITING_ENTRY"
    just_entered = False
    latest_candle_ts = _candle_timestamp(latest_candle)

    if state == "WAITING_ENTRY" and _entry_touched(setup, latest_low, latest_high):
        entry_price = _entry_midpoint(setup)
        setup["state"] = "ENTERED"
        setup["entered_at"] = _iso_now()
        setup["entry_price_virtual"] = entry_price
        setup["entry_candle_ts"] = latest_candle_ts
        setup["execution_quality"] = _entry_execution_quality(setup)
        state = "ENTERED"
        just_entered = True
        events.append(_event("ENTERED", setup))
        LOGGER.info("Setup entered: %s", setup.get("symbol"))

    if state not in {"ENTERED", "TP1_HIT"}:
        return events

    _update_excursion(setup, latest_low, latest_high)
    invalidated = _invalidated(setup, latest_low, latest_high)
    tp1_hit = _tp1_hit(setup, latest_low, latest_high)
    tp2_hit = _tp2_hit(setup, latest_low, latest_high)
    had_tp1 = setup_has_tp1(setup) or state == "TP1_HIT"
    tp_fast_move = just_entered or _same_entry_candle(setup, latest_candle_ts)

    if invalidated and (tp1_hit or tp2_hit):
        if had_tp1:
            setup["state"] = "TP1_THEN_INVALIDATED"
            setup["invalidated_at"] = _iso_now()
            if tp_fast_move:
                setup["execution_quality"] = "FAST_MOVE"
            events.append(
                _final_event(
                    "TP1_THEN_INVALIDATED",
                    setup,
                    "TP1 reached before invalidation",
                )
            )
        else:
            note = "TP and invalidation touched in same candle. Conservative classification used."
            setup.setdefault("notes", []).append(note)
            setup["state"] = "AMBIGUOUS_INVALIDATION_FIRST"
            setup["invalidated_at"] = _iso_now()
            events.append(
                _final_event(
                    "AMBIGUOUS_INVALIDATION_FIRST",
                    setup,
                    note,
                )
            )
        return events

    if invalidated:
        final_state = "TP1_THEN_INVALIDATED" if had_tp1 else "INVALIDATED_BEFORE_TP1"
        setup["state"] = final_state
        setup["invalidated_at"] = _iso_now()
        reason = "TP1 reached before invalidation" if had_tp1 else "scenario failed before TP1"
        events.append(_final_event(final_state, setup, reason))
        return events

    if tp2_hit:
        if setup.get("tp1_hit_at") is None:
            setup["tp1_hit_at"] = _iso_now()
        setup["state"] = "TP2_HIT"
        setup["tp2_hit_at"] = _iso_now()
        if tp_fast_move:
            setup["execution_quality"] = "FAST_MOVE"
        events.append(_final_event("TP2_HIT", setup, "+2.5R scenario reached"))
        return events

    if tp1_hit and setup.get("tp1_hit_at") is None:
        setup["state"] = "TP1_HIT"
        setup["tp1_hit_at"] = _iso_now()
        if tp_fast_move:
            setup["execution_quality"] = "FAST_MOVE"
        events.append(_event("TP1_HIT", setup))
        LOGGER.info("TP1 hit: %s", setup.get("symbol"))

    return events


def move_setup_to_journal(
    setup: dict[str, Any],
    final_state: str,
    reason: str,
) -> dict[str, Any]:
    record = dict(setup)
    if final_state == "INVALIDATED":
        final_state = normalize_final_state(record)
    record["state"] = final_state
    record["final_state"] = final_state
    record["closed_at"] = _iso_now()
    record["close_reason"] = reason
    _attach_result_metrics(record)
    return record


def format_tracking_event_message(event: dict[str, Any]) -> str:
    event_type = event.get("event")
    setup = event.get("setup") or {}
    if event_type == "ENTERED":
        return "\n".join(
            [
                "🟡 СЕТАП АКТИВИРОВАН",
                "",
                f"Монета: {setup.get('symbol', 'n/a')}",
                f"Направление: {translate_direction(setup.get('direction'))}",
                f"Виртуальный вход: {_format_price(setup.get('entry_price_virtual'))}",
                f"Зона входа: {_format_zone(setup)}",
                f"Инвалидация: {_format_price(setup.get('invalidation'))}",
                f"TP1: {_format_price(setup.get('tp1'))}",
                f"TP2: {_format_price(setup.get('tp2'))}",
                "",
                f"Execution: {execution_quality_label(setup.get('execution_quality'))}",
                "Это виртуальное отслеживание, не реальная сделка.",
            ]
        )

    if event_type == "TP1_HIT":
        return "\n".join(
            [
                "🟢 TP1 ДОСТИГНУТ",
                "",
                f"Монета: {setup.get('symbol', 'n/a')}",
                f"Направление: {translate_direction(setup.get('direction'))}",
                f"Виртуальный вход: {_format_price(setup.get('entry_price_virtual'))}",
                f"TP1: {_format_price(setup.get('tp1'))}",
                f"Execution: {execution_quality_label(setup.get('execution_quality'))}",
                "Результат: сценарий дошёл до +1.5R",
            ] + _execution_warning_lines(setup)
        )

    if event_type == "TP2_HIT":
        return "\n".join(
            [
                "✅ TP2 ДОСТИГНУТ / СЕТАП ЗАКРЫТ",
                "",
                f"Монета: {setup.get('symbol', 'n/a')}",
                f"Направление: {translate_direction(setup.get('direction'))}",
                f"Виртуальный вход: {_format_price(setup.get('entry_price_virtual'))}",
                f"TP2: {_format_price(setup.get('tp2'))}",
                f"Execution: {execution_quality_label(setup.get('execution_quality'))}",
                "Результат: +2.5R",
            ] + _execution_warning_lines(setup)
        )

    if event_type == "TP1_THEN_INVALIDATED":
        return "\n".join(
            [
                "🟠 TP1 ДОСТИГНУТ, ПОТОМ СЕТАП СЛОМАН",
                "",
                f"Монета: {setup.get('symbol', 'n/a')}",
                f"Направление: {translate_direction(setup.get('direction'))}",
                f"Виртуальный вход: {_format_price(setup.get('entry_price_virtual'))}",
                f"TP1: {_format_price(setup.get('tp1'))}",
                f"Инвалидация: {_format_price(setup.get('invalidation'))}",
                f"Execution: {execution_quality_label(setup.get('execution_quality'))}",
                "Результат: частичная отработка / TP1 only",
            ] + _execution_warning_lines(setup)
        )

    if event_type in {
        "INVALIDATED",
        "INVALIDATED_BEFORE_TP1",
        "AMBIGUOUS_INVALIDATION_FIRST",
        "INVALIDATED_FIRST_UNKNOWN",
    }:
        lines = [
            "🔴 СЕТАП СЛОМАН",
            "",
            f"Монета: {setup.get('symbol', 'n/a')}",
            f"Направление: {translate_direction(setup.get('direction'))}",
            f"Виртуальный вход: {_format_price(setup.get('entry_price_virtual'))}",
            f"Инвалидация: {_format_price(setup.get('invalidation'))}",
            f"Execution: {execution_quality_label(setup.get('execution_quality'))}",
            "Результат: сценарий провален / -1R",
        ]
        if event_type in {"AMBIGUOUS_INVALIDATION_FIRST", "INVALIDATED_FIRST_UNKNOWN"}:
            lines.append("Примечание: TP и инвалидация задеты в одной свече. Используем консервативную оценку.")
        return "\n".join(lines)

    if event_type in {"EXPIRED_NO_ENTRY", "EXPIRED_AFTER_ENTRY"}:
        return "\n".join(
            [
                "⚪ СЕТАП ИСТЁК",
                "",
                f"Монета: {setup.get('symbol', 'n/a')}",
                f"Состояние: {translate_setup_state(event_type)}",
                f"Причина: сценарий истёк через {config.SETUP_TRACKING_EXPIRATION_HOURS:g} часов",
                f"Execution: {execution_quality_label(setup.get('execution_quality'))}",
            ]
        )

    return ""


def _is_actionable_setup(setup_record: dict[str, Any]) -> bool:
    return (
        setup_record.get("setup_status") != "NO SETUP"
        and setup_record.get("execution_status") != "TOO_LATE_DO_NOT_CHASE"
        and setup_record.get("bias") != "WAIT"
        and setup_record.get("entry_zone") not in (None, "", "n/a")
        and setup_record.get("invalidation") not in (None, "", "n/a")
        and setup_record.get("tp1") not in (None, "", "n/a")
        and setup_record.get("tp2") not in (None, "", "n/a")
    )


def _fetch_latest_candle(bybit_client: Any, symbol: str) -> dict[str, float] | None:
    klines = bybit_client.get_klines(symbol, config.KLINE_INTERVAL, 2)
    candles = [_parse_kline(item) for item in klines]
    normalized = [item for item in candles if item is not None]
    if not normalized:
        return None
    normalized.sort(key=lambda item: item["timestamp"])
    return normalized[-1]


def _parse_kline(item: Any) -> dict[str, float] | None:
    if isinstance(item, dict):
        timestamp = parse_price_value(item.get("startTime") or item.get("timestamp") or item.get("time"))
        high_price = parse_price_value(item.get("high") or item.get("highPrice"))
        low_price = parse_price_value(item.get("low") or item.get("lowPrice"))
        close_price = parse_price_value(item.get("close") or item.get("closePrice"))
    elif isinstance(item, (list, tuple)) and len(item) >= 5:
        timestamp = parse_price_value(item[0])
        high_price = parse_price_value(item[2])
        low_price = parse_price_value(item[3])
        close_price = parse_price_value(item[4])
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


def _latest_range(
    latest_price: float,
    latest_candle: dict[str, float] | None,
) -> tuple[float, float]:
    if config.SETUP_TOUCH_MODE == "close" or not latest_candle:
        return latest_price, latest_price

    low = _to_float(latest_candle.get("low"))
    high = _to_float(latest_candle.get("high"))
    if low is None or high is None:
        return latest_price, latest_price
    return low, high


def _candle_timestamp(latest_candle: dict[str, float] | None) -> float | None:
    if not latest_candle:
        return None
    return _to_float(latest_candle.get("timestamp"))


def _entry_execution_quality(setup: dict[str, Any]) -> str:
    created_at = _parse_iso(setup.get("created_at"))
    entered_at = _parse_iso(setup.get("entered_at"))
    if created_at is None or entered_at is None:
        return "UNKNOWN"

    elapsed = (entered_at - created_at).total_seconds()
    if elapsed >= config.MIN_SECONDS_AFTER_ALERT_FOR_ENTRY:
        return "REALISTIC"
    return "FAST_MOVE"


def _same_entry_candle(setup: dict[str, Any], latest_candle_ts: float | None) -> bool:
    entry_candle_ts = _to_float(setup.get("entry_candle_ts"))
    if entry_candle_ts is None or latest_candle_ts is None:
        return False
    return entry_candle_ts == latest_candle_ts


def _entry_touched(setup: dict[str, Any], latest_low: float, latest_high: float) -> bool:
    entry_low = _to_float(setup.get("entry_low"))
    entry_high = _to_float(setup.get("entry_high"))
    if entry_low is None or entry_high is None:
        return False
    return latest_low <= entry_high and latest_high >= entry_low


def _tp1_hit(setup: dict[str, Any], latest_low: float, latest_high: float) -> bool:
    tp1 = _to_float(setup.get("tp1"))
    if tp1 is None:
        return False
    if setup.get("direction") == "LONG":
        return latest_high >= tp1
    if setup.get("direction") == "SHORT":
        return latest_low <= tp1
    return False


def _tp2_hit(setup: dict[str, Any], latest_low: float, latest_high: float) -> bool:
    tp2 = _to_float(setup.get("tp2"))
    if tp2 is None:
        return False
    if setup.get("direction") == "LONG":
        return latest_high >= tp2
    if setup.get("direction") == "SHORT":
        return latest_low <= tp2
    return False


def _invalidated(setup: dict[str, Any], latest_low: float, latest_high: float) -> bool:
    invalidation = _to_float(setup.get("invalidation"))
    if invalidation is None:
        return False
    if setup.get("direction") == "LONG":
        return latest_low <= invalidation
    if setup.get("direction") == "SHORT":
        return latest_high >= invalidation
    return False


def _update_excursion(setup: dict[str, Any], latest_low: float, latest_high: float) -> None:
    entry = _to_float(setup.get("entry_price_virtual"))
    if entry is None or entry <= 0:
        return

    if setup.get("direction") == "LONG":
        favorable = ((latest_high - entry) / entry) * 100
        adverse = ((entry - latest_low) / entry) * 100
    elif setup.get("direction") == "SHORT":
        favorable = ((entry - latest_low) / entry) * 100
        adverse = ((latest_high - entry) / entry) * 100
    else:
        return

    setup["max_favorable_move_percent"] = max(
        _to_float(setup.get("max_favorable_move_percent")) or 0,
        favorable,
    )
    setup["max_adverse_move_percent"] = max(
        _to_float(setup.get("max_adverse_move_percent")) or 0,
        adverse,
    )


def _expired_state(setup: dict[str, Any], now: datetime) -> str | None:
    created_at = _parse_iso(setup.get("created_at"))
    if created_at is None:
        return None

    expires_at = created_at + timedelta(hours=config.SETUP_TRACKING_EXPIRATION_HOURS)
    if now < expires_at:
        return None

    state = setup.get("state") or "WAITING_ENTRY"
    if state == "WAITING_ENTRY":
        return "EXPIRED_NO_ENTRY"
    return "EXPIRED_AFTER_ENTRY"


def _event(event_type: str, setup: dict[str, Any]) -> dict[str, Any]:
    return {
        "event": event_type,
        "setup": dict(setup),
    }


def _final_event(final_state: str, setup: dict[str, Any], reason: str) -> dict[str, Any]:
    event = _event(final_state, setup)
    event["final_state"] = final_state
    event["reason"] = reason
    return event


def _log_final_state(final_state: str, symbol: str) -> None:
    if final_state == "TP2_HIT":
        LOGGER.info("TP2 hit: %s", symbol)
    elif final_state == "TP1_THEN_INVALIDATED":
        LOGGER.info("TP1 then invalidated: %s", symbol)
    elif final_state in {"INVALIDATED_BEFORE_TP1", "INVALIDATED"}:
        LOGGER.info("Invalidated before TP1: %s", symbol)
    elif final_state in {"AMBIGUOUS_INVALIDATION_FIRST", "INVALIDATED_FIRST_UNKNOWN"}:
        LOGGER.info("Setup invalidated: %s", symbol)


def _entry_midpoint(setup: dict[str, Any]) -> float | None:
    entry_low = _to_float(setup.get("entry_low"))
    entry_high = _to_float(setup.get("entry_high"))
    if entry_low is None or entry_high is None:
        return None
    return (entry_low + entry_high) / 2


def _direction_from_bias(bias: str) -> str | None:
    if "LONG" in bias:
        return "LONG"
    if "SHORT" in bias:
        return "SHORT"
    return None


def _build_setup_id(symbol: str, created_at: str, direction: str) -> str:
    safe_created_at = re.sub(r"[^0-9A-Za-z]+", "", created_at)
    return f"{symbol}-{safe_created_at}"


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso_now(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).isoformat()


def _result_for_state(final_state: str) -> str:
    if final_state == "TP2_HIT":
        return "+2.5R"
    if final_state in {"TP1_THEN_INVALIDATED", "TP1_HIT"}:
        return "TP1 only"
    if final_state in {
        "INVALIDATED",
        "INVALIDATED_BEFORE_TP1",
        "AMBIGUOUS_INVALIDATION_FIRST",
        "INVALIDATED_FIRST_UNKNOWN",
    }:
        return "-1R"
    return "0R"


def _raw_result_r_for_state(final_state: str) -> float:
    if final_state == "TP2_HIT":
        return config.SETUP_TP2_R_MULTIPLIER
    if final_state in {"TP1_THEN_INVALIDATED", "TP1_HIT"}:
        return config.SETUP_TP1_R_MULTIPLIER
    if final_state in {
        "INVALIDATED",
        "INVALIDATED_BEFORE_TP1",
        "AMBIGUOUS_INVALIDATION_FIRST",
        "INVALIDATED_FIRST_UNKNOWN",
    }:
        return -1.0
    return 0.0


def _attach_result_metrics(record: dict[str, Any]) -> None:
    raw_result_r = _raw_result_r_for_state(normalize_final_state(record))
    fee_mode = str(record.get("fee_mode") or config.DEFAULT_EXECUTION_FEE_MODE)
    fee_rate = _fee_rate_for_mode(fee_mode)
    round_trip_fee_percent = fee_rate * 100 * 2

    record["result_r"] = result_for_record(record)
    record["raw_result_r"] = raw_result_r
    record["fee_mode"] = fee_mode
    record["fee_rate"] = fee_rate
    record["round_trip_fee_percent"] = round_trip_fee_percent
    record["slippage_percent"] = config.SLIPPAGE_PERCENT
    record["fee_adjusted_result_r"] = _calculate_fee_adjusted_result(record)


def _calculate_fee_adjusted_result(record: dict[str, Any]) -> float:
    raw_result_r = _to_float(record.get("raw_result_r"))
    if raw_result_r is None:
        raw_result_r = _raw_result_r_for_state(normalize_final_state(record))

    if normalize_final_state(record) in {"EXPIRED_NO_ENTRY", "EXPIRED_AFTER_ENTRY"}:
        return raw_result_r

    risk_percent = _risk_percent(record)
    if risk_percent is None or risk_percent <= 0:
        return raw_result_r

    fee_mode = str(record.get("fee_mode") or config.DEFAULT_EXECUTION_FEE_MODE)
    fee_rate = _to_float(record.get("fee_rate"))
    if fee_rate is None:
        fee_rate = _fee_rate_for_mode(fee_mode)

    round_trip_fee_percent = _to_float(record.get("round_trip_fee_percent"))
    if round_trip_fee_percent is None:
        round_trip_fee_percent = fee_rate * 100 * 2

    slippage_percent = slippage_for_record(record)
    cost_r = (round_trip_fee_percent + slippage_percent) / risk_percent
    return raw_result_r - cost_r


def _risk_percent(record: dict[str, Any]) -> float | None:
    entry = _to_float(record.get("entry_price_virtual"))
    if entry is None:
        entry = _entry_midpoint(record)
    invalidation = _to_float(record.get("invalidation"))
    if entry is None or invalidation is None or entry <= 0:
        return None
    return abs(entry - invalidation) / entry * 100


def _fee_rate_for_mode(fee_mode: str) -> float:
    if fee_mode == "maker":
        return config.BYBIT_MAKER_FEE_RATE
    if fee_mode == "worst_case":
        return max(config.BYBIT_MAKER_FEE_RATE, config.BYBIT_TAKER_FEE_RATE)
    return config.BYBIT_TAKER_FEE_RATE


def _execution_warning_lines(record: dict[str, Any]) -> list[str]:
    if execution_quality_for_record(record) != "FAST_MOVE":
        return []
    return [
        "⚠️ Быстрое движение: вход и TP произошли почти сразу. Руками можно было не успеть."
    ]


def _format_zone(setup: dict[str, Any]) -> str:
    return f"{_format_price(setup.get('entry_low'))} - {_format_price(setup.get('entry_high'))}"


def _format_price(value: Any) -> str:
    price = _to_float(value)
    if price is None:
        return "n/a"
    if price >= 100:
        return f"{price:.2f}"
    if price >= 1:
        return f"{price:.4f}"
    if price >= 0.01:
        return f"{price:.6f}"
    return f"{price:.8f}"


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

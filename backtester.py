from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import config
from indicators import analyze_indicator_context
from setup_generator import generate_setup
from setup_tracker import (
    create_active_setup_from_alert,
    fee_adjusted_result_r_for_record,
    normalize_final_state,
    raw_result_r_for_record,
    result_for_record,
)


BACKTEST_EXPORT_FIELDS = [
    "run_id",
    "symbol",
    "side",
    "alert_type",
    "risk_level",
    "execution_status",
    "execution_quality",
    "result_type",
    "raw_r",
    "net_r",
    "opened_at",
    "closed_at",
    "close_reason",
    "data_quality",
]


class Backtester:
    def __init__(self, bybit_client: Any, storage: Any, scanner: Any | None = None) -> None:
        self.bybit = bybit_client
        self.storage = storage
        self.scanner = scanner
        self.logger = logging.getLogger(self.__class__.__name__)

    def run(
        self,
        symbols: list[str],
        days: int | None = None,
        interval: str | None = None,
    ) -> dict[str, Any]:
        if not config.BACKTEST_ENABLED:
            raise RuntimeError("Backtest disabled by config")

        clean_symbols = [symbol.upper().strip() for symbol in symbols if symbol.strip()]
        if not clean_symbols:
            clean_symbols = ["BTCUSDT"]
        clean_symbols = clean_symbols[: max(config.BACKTEST_MAX_SYMBOLS, 1)]

        days = max(int(days or config.BACKTEST_DEFAULT_DAYS), 1)
        interval = interval or config.BACKTEST_DEFAULT_INTERVAL
        api_interval = normalize_interval_for_bybit(interval)
        interval_ms = interval_to_ms(api_interval)
        max_days_by_candles = max(config.BACKTEST_MAX_CANDLES_PER_SYMBOL * interval_ms / 86_400_000, interval_ms / 86_400_000)
        effective_days = min(float(days), max_days_by_candles)

        run_id = f"bt_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        end = datetime.now(UTC)
        start = end - timedelta(days=effective_days)
        notes = [
            "Исторический тест не гарантирует результат в реале.",
            "Могут быть отличия из-за OI/funding availability, задержки Telegram, проскальзывания, лимитного исполнения и same-candle ambiguity.",
        ]
        if effective_days < days:
            notes.append(
                f"Период ограничен BACKTEST_MAX_CANDLES_PER_SYMBOL={config.BACKTEST_MAX_CANDLES_PER_SYMBOL}."
            )

        all_trades: list[dict[str, Any]] = []
        for symbol in clean_symbols:
            try:
                self.logger.info("Backtest started for %s days=%s interval=%s", symbol, effective_days, api_interval)
                symbol_trades, symbol_notes = self._run_symbol(
                    symbol=symbol,
                    run_id=run_id,
                    start=start,
                    end=end,
                    interval=api_interval,
                    interval_ms=interval_ms,
                )
                all_trades.extend(symbol_trades)
                notes.extend(symbol_notes)
                self.logger.info("Backtest completed for %s trades=%s", symbol, len(symbol_trades))
            except Exception as exc:
                self.logger.error("Backtest failed for %s: %s", symbol, exc)
                notes.append(f"{symbol}: ошибка backtest: {exc}")

        raw_r = sum(_to_float(trade.get("raw_r")) or 0 for trade in all_trades)
        net_r = sum(_to_float(trade.get("net_r")) or 0 for trade in all_trades)
        run_record = {
            "id": run_id,
            "created_at": datetime.now(UTC).isoformat(),
            "symbols": clean_symbols,
            "days": days,
            "effective_days": round(effective_days, 2),
            "interval": interval,
            "api_interval": api_interval,
            "config_snapshot": config_snapshot(),
            "total_trades": len(all_trades),
            "net_r": net_r,
            "raw_r": raw_r,
            "notes": _unique_notes(notes),
        }
        self.storage.add_backtest_run(run_record)
        for trade in all_trades:
            self.storage.add_backtest_trade(trade)

        return {
            "run": run_record,
            "trades": all_trades,
            "analysis": analyze_backtest_trades(all_trades),
        }

    def _run_symbol(
        self,
        symbol: str,
        run_id: str,
        start: datetime,
        end: datetime,
        interval: str,
        interval_ms: int,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        candles = self._fetch_candles(symbol, interval, start_ms, end_ms, interval_ms)
        notes: list[str] = []
        if len(candles) < max(config.KLINE_LIMIT, config.LOOKBACK_MINUTES + 5):
            return [], [f"{symbol}: недостаточно свечей для backtest."]

        oi_points = self._fetch_open_interest(symbol, start_ms, end_ms)
        funding_points = self._fetch_funding(symbol, start_ms, end_ms)
        if not oi_points:
            notes.append(f"{symbol}: ⚠️ OI/funding historical data unavailable. Test quality reduced.")
            return [], notes
        if not funding_points:
            notes.append(f"{symbol}: funding history unavailable, funding-dependent squeeze classes may be reduced.")

        blacklist = {str(item).upper() for item in (self.storage.state.get("symbol_blacklist") or config.SYMBOL_BLACKLIST)}
        if symbol in blacklist:
            return [], [f"{symbol}: symbol blacklisted, tradeable setups ignored."]

        trades: list[dict[str, Any]] = []
        last_alert_ts = 0
        for index in range(config.KLINE_LIMIT, len(candles) - 1):
            current = candles[index]
            previous = self._reference_candle(candles, index, config.LOOKBACK_MINUTES, interval_ms)
            if previous is None:
                continue

            price_change = percent_change(previous["close"], current["close"])
            if price_change is None or abs(price_change) < config.PRICE_CHANGE_THRESHOLD_PERCENT:
                continue

            current_oi = nearest_value_before(oi_points, current["timestamp"])
            previous_oi = nearest_value_before(oi_points, current["timestamp"] - config.LOOKBACK_MINUTES * 60_000)
            oi_change = percent_change(previous_oi, current_oi)
            if oi_change is None or oi_change < config.OI_THRESHOLD_PERCENT:
                continue

            volume_spike = self._volume_spike(candles, index)
            if volume_spike is None or volume_spike < config.VOLUME_SPIKE_MULTIPLIER:
                continue

            if current["timestamp"] - last_alert_ts < config.ALERT_COOLDOWN_MINUTES * 60_000:
                continue

            funding_rate = nearest_value_before(funding_points, current["timestamp"])
            try:
                alert = self._build_alert(
                    symbol=symbol,
                    candle=current,
                    price_change=price_change,
                    oi_change=oi_change,
                    volume_spike=volume_spike,
                    funding_rate=funding_rate,
                    indicator_candles=candles[index - config.KLINE_LIMIT + 1 : index + 1],
                )
            except Exception as exc:
                self.logger.debug("Backtest alert build skipped for %s at %s: %s", symbol, current["timestamp"], exc)
                continue
            setup = alert.get("setup_scenario")
            if not isinstance(setup, dict):
                continue
            active_setup = create_active_setup_from_alert(alert, setup)
            if active_setup is None:
                continue

            trade = simulate_setup(
                run_id=run_id,
                alert=alert,
                setup=active_setup,
                future_candles=candles[index + 1 :],
            )
            if trade is not None:
                trades.append(trade)
                last_alert_ts = current["timestamp"]

        return trades, notes

    def _build_alert(
        self,
        symbol: str,
        candle: dict[str, Any],
        price_change: float,
        oi_change: float,
        volume_spike: float,
        funding_rate: float | None,
        indicator_candles: list[dict[str, Any]],
    ) -> dict[str, Any]:
        timestamp = ms_to_iso(candle["timestamp"])
        indicator_context = analyze_indicator_context(indicator_candles)
        alert = {
            "symbol": symbol,
            "timestamp": timestamp,
            "price": candle["close"],
            "price_change_percent": price_change,
            "oi_change_percent": oi_change,
            "volume_spike": volume_spike,
            "funding_rate": funding_rate,
            "funding_rate_percent": funding_rate * 100 if funding_rate is not None else None,
            "indicator_context": indicator_context,
            "_indicator_candles": indicator_candles,
        }
        if self.scanner is not None:
            indicator_context["indicator_summary"] = self.scanner._indicator_summary_for_alert(alert, indicator_context)
            alert.update(self.scanner._classify_alert_risk(alert))
        else:
            alert.update(_fallback_classification(alert))

        setup = generate_setup(
            symbol=symbol,
            last_price=candle["close"],
            price_change_15m=price_change,
            oi_change_15m=oi_change,
            volume_spike=volume_spike,
            funding_rate=alert.get("funding_rate_percent"),
            alert_type=alert.get("alert_type", "Radar Activity"),
            risk_level=alert.get("risk_level", "MEDIUM"),
            indicator_context=indicator_context,
            candles=indicator_candles,
        )
        setup.setdefault("setup_id", f"bt_setup_{symbol}_{int(candle['timestamp'])}")
        alert["setup_scenario"] = setup
        alert["alert_record_id"] = setup["setup_id"]
        return alert

    def _fetch_candles(
        self,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
        interval_ms: int,
    ) -> list[dict[str, Any]]:
        candles_by_ts: dict[int, dict[str, Any]] = {}
        cursor = start_ms
        page_limit = 1000
        max_pages = max(config.BACKTEST_MAX_CANDLES_PER_SYMBOL // page_limit + 3, 3)
        for _ in range(max_pages):
            if cursor > end_ms or len(candles_by_ts) >= config.BACKTEST_MAX_CANDLES_PER_SYMBOL:
                break
            page_end = min(cursor + interval_ms * (page_limit - 1), end_ms)
            rows = self.bybit.get_klines(symbol, interval, page_limit, start_ms=cursor, end_ms=page_end)
            parsed = [_parse_kline(row) for row in rows]
            parsed = [item for item in parsed if item is not None]
            if not parsed:
                cursor = page_end + interval_ms
                continue
            for candle in parsed:
                candles_by_ts[int(candle["timestamp"])] = candle
            cursor = max(int(item["timestamp"]) for item in parsed) + interval_ms

        candles = sorted(candles_by_ts.values(), key=lambda item: item["timestamp"])
        return candles[: config.BACKTEST_MAX_CANDLES_PER_SYMBOL]

    def _fetch_open_interest(self, symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
        points: dict[int, float] = {}
        step_ms = 5 * 60_000 * 200
        cursor = start_ms
        while cursor <= end_ms:
            page_end = min(cursor + step_ms, end_ms)
            try:
                rows = self.bybit.get_open_interest_history(symbol, "5min", cursor, page_end, limit=200)
            except Exception as exc:
                self.logger.warning("Open interest history unavailable for %s: %s", symbol, exc)
                return []
            for row in rows:
                ts = _to_int(row.get("timestamp"))
                value = _to_float(row.get("openInterest"))
                if ts is not None and value is not None:
                    points[ts] = value
            cursor = page_end + 1
        return sorted(points.items())

    def _fetch_funding(self, symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
        try:
            rows = self.bybit.get_funding_history(symbol, start_ms, end_ms, limit=200)
        except Exception as exc:
            self.logger.warning("Funding history unavailable for %s: %s", symbol, exc)
            return []
        points = []
        for row in rows:
            ts = _to_int(row.get("fundingRateTimestamp") or row.get("timestamp"))
            value = _to_float(row.get("fundingRate"))
            if ts is not None and value is not None:
                points.append((ts, value))
        return sorted(points)

    def _reference_candle(
        self,
        candles: list[dict[str, Any]],
        index: int,
        lookback_minutes: int,
        interval_ms: int,
    ) -> dict[str, Any] | None:
        offset = max(int((lookback_minutes * 60_000) / interval_ms), 1)
        ref_index = index - offset
        if ref_index < 0:
            return None
        return candles[ref_index]

    def _volume_spike(self, candles: list[dict[str, Any]], index: int) -> float | None:
        current = volume_metric(candles[index])
        if current is None or current <= 0:
            return None
        lookback = max(config.HISTORY_WINDOW_MINUTES, 5)
        start = max(0, index - lookback)
        previous = [volume_metric(candle) for candle in candles[start:index]]
        values = [value for value in previous if value is not None and value > 0]
        if not values:
            return None
        average = sum(values) / len(values)
        if average <= 0:
            return None
        return current / average


def simulate_setup(
    run_id: str,
    alert: dict[str, Any],
    setup: dict[str, Any],
    future_candles: list[dict[str, Any]],
) -> dict[str, Any] | None:
    created_ts = iso_to_ms(setup.get("created_at")) or iso_to_ms(alert.get("timestamp"))
    if created_ts is None:
        return None
    expires_ts = created_ts + int(config.SETUP_TRACKING_EXPIRATION_HOURS * 3_600_000)
    entry_low = _to_float(setup.get("entry_low"))
    entry_high = _to_float(setup.get("entry_high"))
    invalidation = _to_float(setup.get("invalidation"))
    tp1 = _to_float(setup.get("tp1"))
    tp2 = _to_float(setup.get("tp2"))
    if None in {entry_low, entry_high, invalidation, tp1, tp2}:
        return None

    entered = False
    tp1_hit = False
    final_state = "EXPIRED_NO_ENTRY"
    close_reason = "expired before entry"
    closed_at = ms_to_iso(expires_ts)
    data_quality = "OK"
    opened_at = ""
    execution_quality = "UNKNOWN"

    for candle in future_candles:
        ts = int(candle["timestamp"])
        if ts > expires_ts:
            final_state = "EXPIRED_AFTER_ENTRY" if entered else "EXPIRED_NO_ENTRY"
            close_reason = "expired after entry" if entered else "expired before entry"
            closed_at = ms_to_iso(expires_ts)
            break

        low = float(candle["low"])
        high = float(candle["high"])
        direction = setup.get("direction")

        if not entered and _entry_touched(direction, entry_low, entry_high, low, high):
            entered = True
            opened_at = ms_to_iso(ts)
            setup["entered_at"] = opened_at
            setup["entry_price_virtual"] = (entry_low + entry_high) / 2
            elapsed = max((ts - created_ts) / 1000, 0)
            execution_quality = "REALISTIC" if elapsed >= config.MIN_SECONDS_AFTER_ALERT_FOR_ENTRY else "FAST_MOVE"
            setup["execution_quality"] = execution_quality

        if not entered:
            continue

        sl_hit = _sl_hit(direction, invalidation, low, high)
        tp1_now = _tp_hit(direction, tp1, low, high)
        tp2_now = _tp_hit(direction, tp2, low, high)

        if sl_hit and (tp1_now or tp2_now):
            if tp1_hit and not tp2_now:
                final_state = "TP1_THEN_INVALIDATED"
                close_reason = "TP1 reached before invalidation"
            else:
                final_state = "AMBIGUOUS_INVALIDATION_FIRST"
                close_reason = "TP and invalidation touched in same candle. Conservative classification used."
                execution_quality = "AMBIGUOUS"
                data_quality = "AMBIGUOUS"
            closed_at = ms_to_iso(ts)
            break

        if sl_hit:
            final_state = "TP1_THEN_INVALIDATED" if tp1_hit else "INVALIDATED_BEFORE_TP1"
            close_reason = "TP1 reached before invalidation" if tp1_hit else "scenario failed before TP1"
            closed_at = ms_to_iso(ts)
            break

        if tp2_now:
            final_state = "TP2_HIT"
            close_reason = "+2.5R scenario reached"
            closed_at = ms_to_iso(ts)
            if not tp1_hit:
                setup["tp1_hit_at"] = ms_to_iso(ts)
            setup["tp2_hit_at"] = ms_to_iso(ts)
            if ts == iso_to_ms(opened_at):
                execution_quality = "FAST_MOVE"
            break

        if tp1_now and not tp1_hit:
            tp1_hit = True
            setup["tp1_hit_at"] = ms_to_iso(ts)
            if ts == iso_to_ms(opened_at):
                execution_quality = "FAST_MOVE"

    record = {
        **setup,
        "id": f"bt_trade_{uuid.uuid4().hex}",
        "run_id": run_id,
        "symbol": alert.get("symbol"),
        "direction": setup.get("direction"),
        "side": setup.get("direction"),
        "alert_type": alert.get("alert_type"),
        "risk_level": alert.get("risk_level"),
        "setup_status": setup.get("setup_status"),
        "execution_status": setup.get("execution_status"),
        "execution_quality": execution_quality,
        "final_state": final_state,
        "state": final_state,
        "created_at": setup.get("created_at"),
        "opened_at": opened_at,
        "closed_at": closed_at,
        "close_reason": close_reason,
        "data_quality": data_quality,
        "entry_price": setup.get("entry_price_virtual"),
        "stop_price": invalidation,
        "tp1": tp1,
        "tp2": tp2,
    }
    if final_state == "TP1_THEN_INVALIDATED":
        record.setdefault("tp1_hit_at", opened_at or closed_at)
    record["result_type"] = result_for_record(record)
    record["raw_r"] = raw_result_r_for_record(record)
    record["net_r"] = fee_adjusted_result_r_for_record(record)
    return record


def analyze_backtest_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(trades)
    ambiguous = sum(1 for trade in trades if trade.get("execution_quality") == "AMBIGUOUS")
    raw_total = sum(_to_float(trade.get("raw_r")) or 0 for trade in trades)
    net_total = sum(_to_float(trade.get("net_r")) or 0 for trade in trades)
    summary = {
        "trades": total,
        "net_r": net_total,
        "raw_r": raw_total,
        "fee_adjusted_r": net_total,
        "fee_impact_total_r": raw_total - net_total,
        "average_fee_impact_r": (raw_total - net_total) / total if total else 0.0,
        "avg_net_r": net_total / total if total else 0.0,
        "tp1_only": _count_result(trades, "TP1 only"),
        "tp2": _count_result(trades, "+2.5R"),
        "sl_before_tp1": _count_state(trades, {"INVALIDATED_BEFORE_TP1", "AMBIGUOUS_INVALIDATION_FIRST"}),
        "broken_after_tp1": _count_state(trades, {"TP1_THEN_INVALIDATED"}),
        "expired": _count_state(trades, {"EXPIRED_NO_ENTRY", "EXPIRED_AFTER_ENTRY"}),
        "ambiguous": ambiguous,
    }
    summary["total_broken"] = summary["sl_before_tp1"] + summary["broken_after_tp1"]
    return {
        "summary": summary,
        "by_symbol": _group_backtest(trades, "symbol"),
        "by_alert_type": _group_backtest(trades, "alert_type"),
        "by_execution_quality": _group_backtest(trades, "execution_quality"),
    }


def format_backtest_result(result: dict[str, Any]) -> str:
    run = result.get("run") or {}
    analysis = result.get("analysis") or {}
    summary = analysis.get("summary") or {}
    symbols = ", ".join(run.get("symbols") or [])
    lines = [
        "🧪 Backtest",
        "",
        f"Symbol: {symbols or 'n/a'}",
        f"Period: {run.get('effective_days', run.get('days'))}d",
        f"Interval: {run.get('interval', config.BACKTEST_DEFAULT_INTERVAL)}",
        "",
        f"Trades: {summary.get('trades', 0)}",
        f"Raw R: {_fmt_r(summary.get('raw_r'))}",
        f"Fee adjusted R: {_fmt_r(summary.get('fee_adjusted_r'))}",
        f"Net R: {_fmt_r(summary.get('net_r'))}",
        f"Avg Net R: {_fmt_r(summary.get('avg_net_r'))}",
        f"Estimated fee impact: {_fmt_r(-(_to_float(summary.get('fee_impact_total_r')) or 0))}",
        f"TP1 only: {summary.get('tp1_only', 0)}",
        f"TP2: {summary.get('tp2', 0)}",
        f"SL before TP1: {summary.get('sl_before_tp1', 0)}",
        f"Broken after TP1: {summary.get('broken_after_tp1', 0)}",
        f"Total broken: {summary.get('total_broken', 0)}",
        f"Expired: {summary.get('expired', 0)}",
        f"Ambiguous: {summary.get('ambiguous', 0)}",
        "",
        "Execution:",
    ]
    execution_groups = analysis.get("by_execution_quality") or {}
    if execution_groups:
        for name, metrics in sorted(execution_groups.items()):
            suffix = " / excluded from clean stats" if name == "AMBIGUOUS" else ""
            lines.append(f"{name}: Avg {_fmt_r(metrics.get('net_avg_r'))}{suffix}")
    else:
        lines.append("нет данных")

    alert_groups = analysis.get("by_alert_type") or {}
    lines.extend(
        [
            "",
            f"Best alert type: {_top_group(alert_groups, best=True)}",
            f"Worst alert type: {_top_group(alert_groups, best=False)}",
            "",
            "Conclusion:",
            _conclusion(summary),
            "Не включать real trading только по одному backtest.",
            "",
            "⚠️ Исторический тест не гарантирует результат в реале.",
        ]
    )
    for note in (run.get("notes") or [])[:4]:
        lines.append(f"• {note}")
    return "\n".join(lines)


def format_backtest_report(run: dict[str, Any] | None, trades: list[dict[str, Any]]) -> str:
    if not run:
        return "Backtest отчётов пока нет. Запусти /backtest SYMBOL DAYS."
    result = {"run": run, "trades": trades, "analysis": analyze_backtest_trades(trades)}
    return format_backtest_result(result)


def format_backtest_top(run: dict[str, Any] | None, trades: list[dict[str, Any]]) -> str:
    if not run:
        return "Backtest данных пока нет. Запусти /backtest SYMBOL DAYS."
    analysis = analyze_backtest_trades(trades)
    lines = ["🏁 Backtest Top", "", f"Run: {run.get('id')}"]
    lines.extend(["", "Лучшие символы:"])
    lines.extend(_format_group_lines(_rank_groups(analysis.get("by_symbol") or {}, reverse=True)))
    lines.extend(["", "Худшие символы:"])
    lines.extend(_format_group_lines(_rank_groups(analysis.get("by_symbol") or {}, reverse=False, only_negative=True)))
    lines.extend(["", "Лучшие alert types:"])
    lines.extend(_format_group_lines(_rank_groups(analysis.get("by_alert_type") or {}, reverse=True)))
    lines.extend(["", "Худшие alert types:"])
    lines.extend(_format_group_lines(_rank_groups(analysis.get("by_alert_type") or {}, reverse=False, only_negative=True)))
    return "\n".join(lines)


def backtest_export_rows(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{field: trade.get(field) for field in BACKTEST_EXPORT_FIELDS} for trade in trades]


def config_snapshot() -> dict[str, Any]:
    return {
        "OI_THRESHOLD_PERCENT": config.OI_THRESHOLD_PERCENT,
        "PRICE_CHANGE_THRESHOLD_PERCENT": config.PRICE_CHANGE_THRESHOLD_PERCENT,
        "VOLUME_SPIKE_MULTIPLIER": config.VOLUME_SPIKE_MULTIPLIER,
        "LOOKBACK_MINUTES": config.LOOKBACK_MINUTES,
        "SETUP_TRACKING_EXPIRATION_HOURS": config.SETUP_TRACKING_EXPIRATION_HOURS,
        "BYBIT_TAKER_FEE_RATE": config.BYBIT_TAKER_FEE_RATE,
        "BYBIT_MAKER_FEE_RATE": config.BYBIT_MAKER_FEE_RATE,
        "FEE_MODE": config.FEE_MODE,
        "SLIPPAGE_PERCENT": config.SLIPPAGE_PERCENT,
    }


def normalize_interval_for_bybit(interval: str) -> str:
    value = str(interval or "1").strip().lower()
    if value in {"d", "1d"}:
        return "D"
    if value.endswith("m"):
        return value[:-1]
    if value.endswith("h"):
        return str(int(value[:-1]) * 60)
    return value


def interval_to_ms(interval: str) -> int:
    value = normalize_interval_for_bybit(interval)
    if value.upper() == "D":
        return 86_400_000
    try:
        minutes = int(value)
    except ValueError:
        minutes = 1
    return max(minutes, 1) * 60_000


def _parse_kline(item: Any) -> dict[str, Any] | None:
    if isinstance(item, dict):
        timestamp = _to_int(item.get("startTime") or item.get("timestamp") or item.get("time"))
        open_price = _to_float(item.get("open") or item.get("openPrice"))
        high = _to_float(item.get("high") or item.get("highPrice"))
        low = _to_float(item.get("low") or item.get("lowPrice"))
        close = _to_float(item.get("close") or item.get("closePrice"))
        volume = _to_float(item.get("volume"))
        turnover = _to_float(item.get("turnover"))
    elif isinstance(item, (list, tuple)) and len(item) >= 6:
        timestamp = _to_int(item[0])
        open_price = _to_float(item[1])
        high = _to_float(item[2])
        low = _to_float(item[3])
        close = _to_float(item[4])
        volume = _to_float(item[5])
        turnover = _to_float(item[6]) if len(item) > 6 else None
    else:
        return None
    if None in {timestamp, open_price, high, low, close}:
        return None
    return {
        "timestamp": int(timestamp),
        "open": float(open_price),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": float(volume or 0),
        "turnover": float(turnover or 0),
    }


def nearest_value_before(points: list[tuple[int, float]], timestamp: int) -> float | None:
    best = None
    for point_ts, value in points:
        if point_ts <= timestamp:
            best = value
        else:
            break
    return best


def percent_change(previous: float | None, current: float | None) -> float | None:
    if previous is None or current is None or previous == 0:
        return None
    return ((current - previous) / previous) * 100


def volume_metric(candle: dict[str, Any]) -> float | None:
    turnover = _to_float(candle.get("turnover"))
    if turnover is not None and turnover > 0:
        return turnover
    return _to_float(candle.get("volume"))


def ms_to_iso(timestamp_ms: int | float) -> str:
    return datetime.fromtimestamp(float(timestamp_ms) / 1000, tz=UTC).isoformat()


def iso_to_ms(value: Any) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.astimezone(UTC).timestamp() * 1000)


def _entry_touched(direction: Any, entry_low: float, entry_high: float, low: float, high: float) -> bool:
    if direction == "LONG":
        return low <= entry_high and high >= entry_low
    if direction == "SHORT":
        return high >= entry_low and low <= entry_high
    return False


def _tp_hit(direction: Any, target: float, low: float, high: float) -> bool:
    if direction == "LONG":
        return high >= target
    if direction == "SHORT":
        return low <= target
    return False


def _sl_hit(direction: Any, invalidation: float, low: float, high: float) -> bool:
    if direction == "LONG":
        return low <= invalidation
    if direction == "SHORT":
        return high >= invalidation
    return False


def _group_backtest(trades: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        grouped[str(trade.get(key) or "Unknown")].append(trade)
    return {name: _group_metrics(items) for name, items in grouped.items()}


def _group_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(trades)
    net_values = [_to_float(trade.get("net_r")) or 0 for trade in trades]
    return {
        "count": count,
        "net_avg_r": sum(net_values) / count if count else 0,
        "net_total_r": sum(net_values),
        "tp2_rate": _rate(_count_result(trades, "+2.5R"), count),
        "sl_before_tp1_rate": _rate(_count_state(trades, {"INVALIDATED_BEFORE_TP1", "AMBIGUOUS_INVALIDATION_FIRST"}), count),
    }


def _rank_groups(groups: dict[str, dict[str, Any]], reverse: bool, only_negative: bool = False) -> list[tuple[str, dict[str, Any]]]:
    rows = list(groups.items())
    if only_negative:
        rows = [row for row in rows if row[1].get("net_avg_r", 0) < 0]
    return sorted(rows, key=lambda item: item[1].get("net_avg_r", 0), reverse=reverse)[:5]


def _format_group_lines(groups: list[tuple[str, dict[str, Any]]]) -> list[str]:
    if not groups:
        return ["нет данных"]
    return [
        f"{index}. {name}: {metrics.get('count', 0)} trades | Avg {_fmt_r(metrics.get('net_avg_r'))}"
        for index, (name, metrics) in enumerate(groups, start=1)
    ]


def _top_group(groups: dict[str, dict[str, Any]], best: bool) -> str:
    ranked = _rank_groups(groups, reverse=best, only_negative=not best)
    if not ranked:
        return "нет данных"
    name, metrics = ranked[0]
    return f"{name}: Avg {_fmt_r(metrics.get('net_avg_r'))}"


def _conclusion(summary: dict[str, Any]) -> str:
    trades = int(summary.get("trades") or 0)
    avg = _to_float(summary.get("avg_net_r")) or 0
    if trades < 30:
        return "Данных мало. Тест пока не показывает надёжный edge."
    if avg > 0:
        return "Данные теста показывают положительный edge, но нужна проверка на paper/live выборке."
    return "Данные теста не показывают устойчивый edge."


def _count_result(trades: list[dict[str, Any]], result_type: str) -> int:
    return sum(1 for trade in trades if trade.get("result_type") == result_type)


def _count_state(trades: list[dict[str, Any]], states: set[str]) -> int:
    return sum(1 for trade in trades if normalize_final_state(trade) in states)


def _rate(count: int, total: int) -> float:
    return (count / total) * 100 if total else 0.0


def _fmt_r(value: Any) -> str:
    return f"{(_to_float(value) or 0):+.2f}R"


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    return int(number) if number is not None else None


def _unique_notes(notes: list[str]) -> list[str]:
    seen = set()
    unique = []
    for note in notes:
        if note not in seen:
            seen.add(note)
            unique.append(note)
    return unique


def _fallback_classification(alert: dict[str, Any]) -> dict[str, Any]:
    return {
        "alert_type": "Radar Activity",
        "risk_level": "MEDIUM",
        "action_note": "Backtest fallback classification.",
        "warnings": [],
        "context_notes": [],
    }

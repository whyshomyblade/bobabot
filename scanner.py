import logging
import time
from datetime import UTC, datetime
from typing import Any

import config
from bybit_client import BybitClient
from indicators import analyze_indicator_context
from performance_analyzer import format_dataset_progress
from setup_generator import generate_setup
from setup_tracker import (
    create_active_setup_from_alert,
    execution_quality_for_record,
    execution_quality_label,
    fee_adjusted_result_r_for_record,
    fee_summary_for_record,
    format_tracking_event_message,
    normalize_final_state,
    raw_result_r_for_record,
    result_for_record,
    slippage_for_record,
    translate_bias,
    translate_direction,
    translate_setup_state,
    update_active_setups,
)
from storage import Storage
from telegram_client import TelegramClient, build_alert_inline_keyboard, build_bybit_chart_keyboard


class MarketScanner:
    def __init__(
        self,
        bybit: BybitClient,
        storage: Storage,
        telegram: TelegramClient,
    ) -> None:
        self.bybit = bybit
        self.storage = storage
        self.telegram = telegram
        self.logger = logging.getLogger(self.__class__.__name__)
        self.autopilot_handler = None

    def refresh_symbols_if_needed(self, force: bool = False) -> None:
        now = int(time.time())
        last_refresh = int(self.storage.state.get("symbols_last_refresh_ts") or 0)
        refresh_after = config.SYMBOL_REFRESH_INTERVAL_MINUTES * 60

        if not force and self.storage.state.get("symbols") and now - last_refresh < refresh_after:
            return

        all_symbols = self.bybit.get_usdt_perpetual_symbols()
        if not all_symbols:
            raise RuntimeError("Bybit returned an empty USDT perpetual symbol list")

        tickers, _ = self.bybit.get_linear_tickers()
        filtered_symbols = self._filter_symbols_by_liquidity(all_symbols, tickers)
        if not filtered_symbols:
            raise RuntimeError("No symbols left after turnover and keyword filters")

        self.storage.state["symbols"] = filtered_symbols
        self.storage.state["symbols_last_refresh_ts"] = now
        self.storage.save()
        self.logger.info("Monitoring %s symbols after filters", len(filtered_symbols))

    def _filter_symbols_by_liquidity(
        self,
        all_symbols: list[str],
        tickers: list[dict[str, Any]],
    ) -> list[str]:
        active_symbols = set(all_symbols)
        ticker_by_symbol = {
            item.get("symbol"): item
            for item in tickers
            if item.get("symbol") in active_symbols
        }

        excluded_by_keyword = [
            symbol
            for symbol in all_symbols
            if self._is_excluded_symbol(symbol)
        ]

        liquid_rows = []
        for symbol in all_symbols:
            if self._is_excluded_symbol(symbol):
                continue

            ticker = ticker_by_symbol.get(symbol)
            turnover_24h = self._to_float((ticker or {}).get("turnover24h"))
            if turnover_24h is None or turnover_24h < config.MIN_24H_TURNOVER_USDT:
                continue

            liquid_rows.append(
                {
                    "symbol": symbol,
                    "turnover_24h": turnover_24h,
                }
            )

        liquid_rows.sort(key=lambda row: row["turnover_24h"], reverse=True)
        selected_rows = liquid_rows[: config.MAX_SYMBOLS_TO_MONITOR]
        selected_symbols = [row["symbol"] for row in selected_rows]
        top_5 = ", ".join(
            f"{row['symbol']}={row['turnover_24h']:,.0f}"
            for row in liquid_rows[:5]
        )

        self.logger.info("Total active USDT perpetual symbols loaded: %s", len(all_symbols))
        self.logger.info("Symbols excluded by keyword: %s", len(excluded_by_keyword))
        self.logger.info("Symbols after liquidity filter: %s", len(liquid_rows))
        self.logger.info("Top 5 symbols by turnover24h USDT: %s", top_5 or "n/a")

        return selected_symbols

    def _is_excluded_symbol(self, symbol: str) -> bool:
        return any(keyword in symbol for keyword in config.EXCLUDED_SYMBOL_KEYWORDS)

    def scan_once(self) -> None:
        if not self.storage.state.get("monitoring_enabled", True):
            self.logger.info("Monitoring paused; skipping scan")
            return

        self.refresh_symbols_if_needed()
        symbols = set(self.storage.state.get("symbols", []))

        tickers, response_timestamp_ms = self.bybit.get_linear_tickers()
        timestamp = datetime.fromtimestamp(response_timestamp_ms / 1000, UTC)
        timestamp_ts = int(timestamp.timestamp())

        ticker_by_symbol = {
            item.get("symbol"): item
            for item in tickers
            if item.get("symbol") in symbols
        }

        snapshots: list[dict[str, Any]] = []
        for symbol in sorted(symbols):
            ticker = ticker_by_symbol.get(symbol)
            if not ticker:
                continue

            snapshot = self._ticker_to_snapshot(symbol, ticker, timestamp, timestamp_ts)
            if snapshot is not None:
                snapshots.append(snapshot)

        self._append_snapshots(snapshots)
        alerts = self._find_alerts(snapshots, timestamp)

        sent = 0
        for alert in alerts[: config.MAX_ALERTS_PER_SCAN]:
            try:
                enriched_alert = self._attach_indicator_context(alert)
                enriched_alert = self._attach_risk_classification(enriched_alert)
                enriched_alert = self._attach_setup_scenario(enriched_alert)
                enriched_alert["timestamp"] = timestamp.isoformat()
                enriched_alert["alert_record_id"] = self._alert_record_id(enriched_alert)
                self._attach_setup_ids(enriched_alert)
                self._record_recent_alert(enriched_alert, timestamp)
                self.telegram.send_message(
                    self._format_alert(enriched_alert),
                    reply_markup=build_alert_inline_keyboard(
                        enriched_alert["symbol"],
                        alert_id=enriched_alert["alert_record_id"],
                        show_order_buttons=self._order_buttons_allowed(enriched_alert),
                    ),
                )
                self._record_alert(enriched_alert["symbol"], timestamp)
                self._track_setup_if_actionable(enriched_alert)
                self._run_autopilot_if_enabled(enriched_alert)
                sent += 1
            except Exception as exc:
                self.logger.error("Failed to send alert for %s: %s", alert["symbol"], exc)

        if len(alerts) > config.MAX_ALERTS_PER_SCAN:
            self.logger.warning(
                "Suppressed %s alerts due to MAX_ALERTS_PER_SCAN=%s",
                len(alerts) - config.MAX_ALERTS_PER_SCAN,
                config.MAX_ALERTS_PER_SCAN,
            )

        self._update_setup_tracking()
        self.storage.state["last_scan_time"] = timestamp.isoformat()
        self.storage.reset_alert_counter_if_needed()
        self.storage.save()

        self.logger.info(
            "Scan complete: symbols=%s snapshots=%s alerts_sent=%s time=%s",
            len(symbols),
            len(snapshots),
            sent,
            timestamp.isoformat(),
        )

    def get_top_oi_growth(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = []
        for symbol, history in self.storage.state.get("history", {}).items():
            if not history:
                continue

            current = history[-1]
            now_ts = int(current.get("ts") or time.time())
            previous = self._find_reference_snapshot(history, now_ts, config.LOOKBACK_MINUTES)
            if not previous:
                continue

            oi_change = self._percent_change(
                previous.get("open_interest"),
                current.get("open_interest"),
            )
            price_change = self._percent_change(previous.get("price"), current.get("price"))
            if oi_change is None or price_change is None:
                continue

            rows.append(
                {
                    "symbol": symbol,
                    "oi_change_percent": oi_change,
                    "price_change_percent": price_change,
                    "price": current.get("price"),
                }
            )

        rows.sort(key=lambda row: row["oi_change_percent"], reverse=True)
        return rows[:limit]

    def _run_autopilot_if_enabled(self, alert: dict[str, Any]) -> None:
        if self.autopilot_handler is None:
            return
        try:
            self.autopilot_handler(alert)
        except Exception as exc:
            self.logger.error("Autopilot handler failed for %s: %s", alert.get("symbol"), exc)

    def format_top_oi_growth(self) -> str:
        rows = self.get_top_oi_growth(limit=10)
        if not rows:
            return "Not enough 15m history yet. Wait until the bot collects more data."

        lines = ["Top 10 OI growth over last 15 minutes:"]
        for index, row in enumerate(rows, start=1):
            lines.append(
                (
                    f"{index}. {row['symbol']}: "
                    f"OI {self._format_signed_percent(row['oi_change_percent'])}, "
                    f"Price {self._format_signed_percent(row['price_change_percent'])}, "
                    f"Last {row['price']}"
                )
            )
        return "\n".join(lines)

    def format_last_alerts(self, limit: int = 10) -> str:
        recent_alerts = self.storage.state.get("recent_alerts", [])
        if not recent_alerts:
            return "🕘 Последние алерты:\n\nПока алертов нет."

        lines = ["🕘 Последние алерты:"]
        for index, alert in enumerate(reversed(recent_alerts[-limit:]), start=1):
            timestamp = self._format_alert_time_short(alert.get("timestamp"))
            price_change = self._to_float(alert.get("price_change_percent")) or 0.0
            oi_change = self._to_float(alert.get("oi_change_percent")) or 0.0
            volume_spike = self._to_float(alert.get("volume_spike")) or 0.0
            alert_type = alert.get("alert_type") or "Unknown"
            risk_level = alert.get("risk_level") or "Unknown"
            setup_bias = alert.get("setup_bias") or "Unknown"
            setup_status = alert.get("setup_status") or "Unknown"
            execution_status = self._alert_execution_status(alert)
            execution_label = self._execution_status_label_ru(execution_status)
            lines.extend(
                [
                    "",
                    (
                        f"{index}. {alert.get('symbol', 'n/a')} | "
                        f"{alert_type} | {risk_level} | "
                        f"{setup_bias} / {setup_status} | {execution_label}"
                    ),
                    (
                        f"Price: {self._format_signed_percent(price_change)} | "
                        f"OI: {self._format_signed_percent(oi_change)} | "
                        f"Vol: {volume_spike:.1f}x"
                    ),
                    f"Time: {timestamp}",
                ]
            )
        return "\n".join(lines)

    def format_active_setups(self, limit: int = 10) -> str:
        active_setups = self.storage.get_active_setups()
        if not active_setups:
            return "Активных сетапов нет."

        lines = ["📒 Активные сетапы:"]
        for index, setup in enumerate(active_setups[:limit], start=1):
            state = setup.get("state") or "WAITING_ENTRY"
            lines.extend(
                [
                    "",
                    (
                        f"{index}. {setup.get('symbol', 'n/a')} | "
                        f"{translate_bias(setup.get('bias'))} | "
                        f"{translate_setup_state(state)}"
                    ),
                    f"Execution: {setup.get('execution_short_label') or self._execution_short_label(setup.get('execution_status'))}",
                ]
            )
            if state == "WAITING_ENTRY":
                lines.extend(
                    [
                        f"Зона входа: {self._format_setup_zone(setup)}",
                        (
                            f"TP1: {self._format_price_value(setup.get('tp1'))} | "
                            f"TP2: {self._format_price_value(setup.get('tp2'))}"
                        ),
                        f"Инвалидация: {self._format_price_value(setup.get('invalidation'))}",
                    ]
                )
            else:
                lines.extend(
                    [
                        f"Виртуальный вход: {self._format_price_value(setup.get('entry_price_virtual'))}",
                        (
                            f"TP1: {self._format_price_value(setup.get('tp1'))} | "
                            f"TP2: {self._format_price_value(setup.get('tp2'))}"
                        ),
                        f"Инвалидация: {self._format_price_value(setup.get('invalidation'))}",
                    ]
                )
            lines.append(f"Возраст: {self._format_age(setup.get('created_at'))}")

        remaining = len(active_setups) - limit
        if remaining > 0:
            lines.extend(["", f"Еще активных сетапов: {remaining}"])
        return "\n".join(lines)

    def format_setup_journal(self, limit: int = 10) -> str:
        records = self.storage.get_setup_journal(limit=limit)
        if not records:
            return "Журнал пока пуст."

        lines = ["📘 Журнал сетапов:"]
        for index, record in enumerate(reversed(records), start=1):
            final_state = normalize_final_state(record)
            result = result_for_record(record)
            raw_result_r = raw_result_r_for_record(record)
            net_result_r = fee_adjusted_result_r_for_record(record)
            execution_quality = execution_quality_for_record(record)
            lines.extend(
                [
                    "",
                    (
                        f"{index}. {record.get('symbol', 'n/a')} | "
                        f"{translate_direction(record.get('direction'))} | "
                        f"{translate_setup_state(final_state)} | {result}"
                    ),
                    f"Вход: {self._format_price_value(record.get('entry_price_virtual'))}",
                    f"Закрыт: {self._format_alert_time_short(record.get('closed_at'))}",
                    f"Raw: {self._format_r_value(raw_result_r)}",
                    f"Fee mode: {record.get('fee_mode') or config.DEFAULT_EXECUTION_FEE_MODE}",
                    f"Fees: {fee_summary_for_record(record)}",
                    f"Slippage: {slippage_for_record(record):.2f}%",
                    f"Net estimate: {self._format_r_value(net_result_r)}",
                    f"Execution: {execution_quality_label(execution_quality)}",
                ]
            )
            if execution_quality == "FAST_MOVE":
                lines.append("⚠️ Быстрое движение: вход и TP произошли почти сразу. Руками можно было не успеть.")
        return "\n".join(lines)

    def format_setup_statistics(self) -> str:
        stats = self.storage.get_setup_statistics()
        total = stats.get("total_closed", 0)
        tp1_only = stats.get("tp1_only", 0)
        tp2_hit = stats.get("tp2_hit", 0)
        invalidated_before_tp1 = stats.get("invalidated_before_tp1", 0)
        invalidated_after_tp1 = stats.get("invalidated_after_tp1", 0)
        expired_no_entry = stats.get("expired_no_entry", 0)
        expired_after_entry = stats.get("expired_after_entry", 0)
        realistic_tp1 = stats.get("realistic_tp1", 0)
        fast_move_tp1 = stats.get("fast_move_tp1", 0)
        realistic_tp2 = stats.get("realistic_tp2", 0)
        fast_move_tp2 = stats.get("fast_move_tp2", 0)
        raw_average_r = stats.get("raw_average_r", 0.0)
        fee_adjusted_average_r = stats.get("fee_adjusted_average_r", 0.0)
        enterable_now_count = stats.get("enterable_now_count", 0)
        pending_limit_count = stats.get("pending_limit_count", 0)
        too_late_count = stats.get("too_late_count", 0)
        no_setup_count = stats.get("no_setup_count", 0)
        tp1_reached = tp1_only + tp2_hit
        total_broken = invalidated_before_tp1 + invalidated_after_tp1

        lines = [
            "📈 Статистика сетапов",
            "",
            f"Закрыто всего: {total}",
            "",
            format_dataset_progress(total),
            "",
            f"TP1 only: {tp1_only}",
            f"TP2 достигнут: {tp2_hit}",
            f"Сломано до TP1: {invalidated_before_tp1}",
            f"Сломано после TP1: {invalidated_after_tp1}",
            f"Всего сломано: {total_broken}",
            f"Истекло без входа: {expired_no_entry}",
            f"Истекло после входа: {expired_after_entry}",
            f"Realistic TP1: {realistic_tp1}",
            f"Fast-move TP1: {fast_move_tp1}",
            f"Realistic TP2: {realistic_tp2}",
            f"Fast-move TP2: {fast_move_tp2}",
            f"Raw average R: {raw_average_r:+.2f}R",
            f"Fee-adjusted average R: {fee_adjusted_average_r:+.2f}R",
            "",
            f"TP1 rate: {self._format_rate(tp1_reached, total)}",
            f"TP1 only rate: {self._format_rate(tp1_only, total)}",
            f"TP2 rate: {self._format_rate(tp2_hit, total)}",
            f"Invalidation before TP1 rate: {self._format_rate(invalidated_before_tp1, total)}",
            f"Broken after TP1 rate: {self._format_rate(invalidated_after_tp1, total)}",
            f"Total broken rate: {self._format_rate(total_broken, total)}",
            "",
            "По исполнению:",
            f"Можно сейчас: {enterable_now_count}",
            f"Только лимитка: {pending_limit_count}",
            f"Поздно / не догонять: {too_late_count}",
            f"Нет сетапа: {no_setup_count}",
            "",
            "По направлению:",
        ]

        direction_groups = stats.get("by_direction") or {}
        if direction_groups:
            lines.extend(self._format_stats_group(direction_groups, translate_keys=True))
        else:
            lines.append("нет данных")

        lines.extend(["", "По типу алерта:"])
        alert_groups = stats.get("by_alert_type") or {}
        if alert_groups:
            lines.extend(self._format_stats_group(alert_groups, limit=8))
        else:
            lines.append("нет данных")

        return "\n".join(lines)

    def _ticker_to_snapshot(
        self,
        symbol: str,
        ticker: dict[str, Any],
        timestamp: datetime,
        timestamp_ts: int,
    ) -> dict[str, Any] | None:
        price = self._to_float(ticker.get("lastPrice"))
        volume_24h = self._to_float(ticker.get("volume24h"))
        turnover_24h = self._to_float(ticker.get("turnover24h"))
        price_change_24h = self._to_float(ticker.get("price24hPcnt"))
        open_interest = self._to_float(ticker.get("openInterest"))
        funding_rate = self._to_float(ticker.get("fundingRate"))

        if price is None or volume_24h is None or open_interest is None:
            self.logger.debug("Skipping incomplete ticker for %s: %s", symbol, ticker)
            return None

        return {
            "symbol": symbol,
            "timestamp": timestamp.isoformat(),
            "ts": timestamp_ts,
            "price": price,
            "volume_24h": volume_24h,
            "turnover_24h": turnover_24h,
            "volume_metric_24h": turnover_24h if turnover_24h is not None else volume_24h,
            "price_change_24h_percent": price_change_24h * 100 if price_change_24h is not None else None,
            "open_interest": open_interest,
            "funding_rate": funding_rate,
            "funding_rate_percent": funding_rate * 100 if funding_rate is not None else None,
        }

    def _append_snapshots(self, snapshots: list[dict[str, Any]]) -> None:
        history = self.storage.state.setdefault("history", {})
        cutoff_ts = int(time.time()) - config.HISTORY_WINDOW_MINUTES * 60

        for snapshot in snapshots:
            symbol = snapshot["symbol"]
            items = history.setdefault(symbol, [])
            items.append(snapshot)
            history[symbol] = [
                item
                for item in items
                if int(item.get("ts", 0)) >= cutoff_ts
            ]

        known_symbols = set(self.storage.state.get("symbols", []))
        for symbol in list(history.keys()):
            if symbol not in known_symbols:
                history.pop(symbol, None)

    def _find_alerts(
        self,
        snapshots: list[dict[str, Any]],
        timestamp: datetime,
    ) -> list[dict[str, Any]]:
        alerts = []
        history = self.storage.state.get("history", {})
        now_ts = int(timestamp.timestamp())

        for current in snapshots:
            symbol = current["symbol"]
            symbol_history = history.get(symbol, [])
            previous = self._find_reference_snapshot(
                symbol_history,
                now_ts,
                config.LOOKBACK_MINUTES,
            )
            if previous is None:
                continue

            oi_change = self._percent_change(
                previous.get("open_interest"),
                current.get("open_interest"),
            )
            price_change = self._percent_change(previous.get("price"), current.get("price"))
            volume_spike = self._volume_spike(symbol_history, current)

            if oi_change is None or price_change is None or volume_spike is None:
                continue

            if oi_change < config.OI_THRESHOLD_PERCENT:
                continue
            if abs(price_change) < config.PRICE_CHANGE_THRESHOLD_PERCENT:
                continue
            if volume_spike < config.VOLUME_SPIKE_MULTIPLIER:
                continue
            if self._in_alert_cooldown(symbol, timestamp):
                continue

            alerts.append(
                {
                    "symbol": symbol,
                    "price": current["price"],
                    "price_change_percent": price_change,
                    "oi_change_percent": oi_change,
                    "volume_spike": volume_spike,
                    "funding_rate": current.get("funding_rate"),
                    "funding_rate_percent": current.get("funding_rate_percent"),
                    "indicator_context": None,
                }
            )

        alerts.sort(
            key=lambda item: (
                item["oi_change_percent"],
                item["volume_spike"],
                abs(item["price_change_percent"]),
            ),
            reverse=True,
        )
        return alerts

    def _attach_indicator_context(self, alert: dict[str, Any]) -> dict[str, Any]:
        if not config.ENABLE_INDICATORS:
            alert["indicator_context"] = None
            return alert

        symbol = alert["symbol"]
        try:
            klines = self.bybit.get_klines(
                symbol,
                config.KLINE_INTERVAL,
                config.KLINE_LIMIT,
            )
            indicator_context = analyze_indicator_context(klines)
            indicator_context["indicator_summary"] = self._indicator_summary_for_alert(
                alert,
                indicator_context,
            )
            alert["indicator_context"] = indicator_context
            alert["_indicator_candles"] = klines
        except Exception as exc:
            self.logger.warning("Indicator fetch/calculation failed for %s: %s", symbol, exc)
            alert["indicator_context"] = None
            alert["_indicator_candles"] = None

        return alert

    def _attach_risk_classification(self, alert: dict[str, Any]) -> dict[str, Any]:
        classification = self._classify_alert_risk(alert)
        alert.update(classification)
        return alert

    def _attach_setup_scenario(self, alert: dict[str, Any]) -> dict[str, Any]:
        if not config.ENABLE_SETUP_GENERATOR:
            alert["setup_scenario"] = None
            return alert

        try:
            setup = generate_setup(
                symbol=alert["symbol"],
                last_price=alert["price"],
                price_change_15m=alert["price_change_percent"],
                oi_change_15m=alert["oi_change_percent"],
                volume_spike=alert["volume_spike"],
                funding_rate=self._funding_rate_percent(alert),
                alert_type=alert.get("alert_type", "Radar Activity"),
                risk_level=alert.get("risk_level", "MEDIUM"),
                indicator_context=alert.get("indicator_context"),
                candles=alert.get("_indicator_candles"),
            )
            alert["setup_scenario"] = setup
            if self._is_symbol_blacklisted(alert["symbol"]):
                setup["blacklisted"] = True
                setup["bias"] = "WAIT"
                setup["setup_status"] = "NO SETUP"
                setup["execution_status"] = "NO_SETUP"
                setup["execution_label"] = "⚫ Symbol blacklisted — setup ignored"
                setup["reason"] = "Symbol blacklisted — setup ignored"
                setup["warning"] = "Tradeable setup/order plan disabled for this symbol."
        except Exception as exc:
            self.logger.warning("Setup generation failed for %s: %s", alert.get("symbol"), exc)
            alert["setup_scenario"] = None

        return alert

    def _classify_alert_risk(self, alert: dict[str, Any]) -> dict[str, Any]:
        price_change = alert.get("price_change_percent", 0)
        oi_change = alert.get("oi_change_percent", 0)
        volume_spike = alert.get("volume_spike", 0)
        funding_rate = self._funding_rate_percent(alert)
        indicator_context = alert.get("indicator_context") or {}
        rsi = self._to_float(indicator_context.get("rsi"))
        ema_trend = indicator_context.get("ema_trend")
        macd_histogram = self._to_float(indicator_context.get("macd_histogram"))

        if (
            abs(price_change) < config.SETUP_MIN_PRICE_CHANGE_PERCENT
            and oi_change < config.SETUP_MIN_OI_CHANGE_PERCENT
        ):
            alert_type = "Low-Quality Radar Activity"
            risk_level = "LOW"
            action_note = "Small price/OI movement. Watch only, no setup."
        elif rsi is not None and price_change >= 8 and rsi >= 75:
            alert_type = "Late Pump / Chase Risk"
            risk_level = "EXTREME"
            action_note = "Do not chase market buy. Wait for pullback, retest, or confirmation."
        elif price_change < 0 and oi_change > 0 and funding_rate is not None and funding_rate >= 0.02:
            alert_type = "Possible Long Squeeze"
            risk_level = "HIGH"
            action_note = "Price falling while OI rises and funding is positive. Longs may be pressured."
        elif price_change > 0 and oi_change > 0 and funding_rate is not None and funding_rate <= -0.02:
            alert_type = "Possible Short Squeeze"
            risk_level = "HIGH"
            action_note = "Price and OI rising while funding is negative. Shorts may be pressured, but avoid chasing after expansion."
        elif price_change < 0 and oi_change > 0 and funding_rate is not None and funding_rate <= -0.5:
            alert_type = "Crowded Short Pressure / Rebound Risk"
            risk_level = "HIGH"
            action_note = "Shorts are aggressive while funding is deeply negative. Breakdown may continue, but squeeze rebound risk is elevated."
        elif price_change > 0 and oi_change > 0 and funding_rate is not None and funding_rate >= 0.5:
            alert_type = "Crowded Long Pressure / Pullback Risk"
            risk_level = "HIGH"
            action_note = "Longs are aggressive while funding is highly positive. Move may continue, but pullback risk is elevated."
        elif (
            rsi is not None
            and price_change > 0
            and oi_change > 0
            and volume_spike >= 10
            and rsi >= 70
        ):
            alert_type = "Overbought Volume Breakout"
            risk_level = "HIGH"
            action_note = "Strong volume expansion with overbought RSI. Avoid late entry; wait for pullback or retest."
        elif (
            rsi is not None
            and price_change < 0
            and oi_change > 0
            and volume_spike >= 10
            and rsi <= 30
        ):
            alert_type = "Oversold Volume Breakdown"
            risk_level = "HIGH"
            action_note = "Strong selling pressure with oversold RSI. Breakdown may continue, but rebound risk is elevated."
        elif price_change > 0 and oi_change > 0 and ema_trend == "bullish" and macd_histogram is not None and macd_histogram < 0:
            alert_type = "Mixed Bullish Pressure"
            risk_level = "HIGH" if volume_spike >= 10 else "MEDIUM"
            action_note = "EMA trend supports move, but MACD momentum conflicts. Wait for confirmation."
        elif price_change < 0 and oi_change > 0 and ema_trend == "bearish" and macd_histogram is not None and macd_histogram > 0:
            alert_type = "Mixed Bearish Pressure"
            risk_level = "HIGH" if volume_spike >= 10 else "MEDIUM"
            action_note = "EMA trend is bearish, but MACD momentum conflicts. Wait for confirmation."
        elif (
            rsi is not None
            and macd_histogram is not None
            and price_change > 0
            and oi_change > 0
            and rsi < 70
            and ema_trend == "bullish"
            and macd_histogram > 0
        ):
            alert_type = "Clean Bullish Momentum"
            risk_level = "MEDIUM"
            action_note = "Momentum supported, but validate chart manually."
        elif (
            rsi is not None
            and macd_histogram is not None
            and price_change < 0
            and oi_change > 0
            and rsi > 30
            and ema_trend == "bearish"
            and macd_histogram < 0
        ):
            alert_type = "Clean Bearish Pressure"
            risk_level = "MEDIUM"
            action_note = "Bearish pressure supported, but validate support breakdown manually."
        else:
            alert_type = "Radar Activity"
            risk_level = "MEDIUM"
            action_note = "Unusual activity detected. Validate chart manually."

        if alert_type != "Low-Quality Radar Activity":
            if volume_spike >= 10:
                risk_level = self._max_risk_level(risk_level, "HIGH")
            if abs(price_change) >= 10:
                risk_level = self._max_risk_level(risk_level, "HIGH")

        return {
            "alert_type": alert_type,
            "risk_level": risk_level,
            "action_note": action_note,
            "warnings": self._risk_warnings(alert),
            "context_notes": self._context_notes(alert),
        }

    def _indicator_summary_for_alert(
        self,
        alert: dict[str, Any],
        indicator_context: dict[str, Any],
    ) -> str:
        ema_trend = indicator_context.get("ema_trend", "neutral")
        price_change = alert.get("price_change_percent", 0)

        if ema_trend == "neutral":
            first = "EMA trend neutral"
        elif (price_change > 0 and ema_trend == "bullish") or (
            price_change < 0 and ema_trend == "bearish"
        ):
            first = "Trend supports move"
        else:
            first = "Trend conflicts with move"

        macd_note = indicator_context.get("macd_note", "")
        if macd_note == "weak momentum":
            second = "MACD weak"
        else:
            second = f"RSI {indicator_context.get('rsi_note', 'n/a')}"

        third = self._risk_summary(indicator_context.get("risk_note", "normal volatility"))
        return f"{first}. {second}. {third}."

    def _find_reference_snapshot(
        self,
        history: list[dict[str, Any]],
        now_ts: int,
        lookback_minutes: int,
    ) -> dict[str, Any] | None:
        target_ts = now_ts - lookback_minutes * 60
        candidates = [
            item for item in history if int(item.get("ts", 0)) <= target_ts
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: int(item.get("ts", 0)))

    def _volume_spike(
        self,
        history: list[dict[str, Any]],
        current: dict[str, Any],
    ) -> float | None:
        ordered_history = sorted(history, key=lambda item: int(item.get("ts", 0)))
        volume_deltas: list[tuple[int, float]] = []

        for previous, next_item in zip(ordered_history, ordered_history[1:]):
            previous_volume = self._snapshot_volume_metric(previous)
            next_volume = self._snapshot_volume_metric(next_item)
            if previous_volume is None or next_volume is None:
                continue

            delta = next_volume - previous_volume
            if delta > 0:
                volume_deltas.append((int(next_item.get("ts", 0)), delta))

        if len(volume_deltas) < 2:
            return None

        current_ts = int(current.get("ts", 0))
        current_delta = None
        previous_deltas: list[float] = []

        for delta_ts, delta in volume_deltas:
            if delta_ts == current_ts:
                current_delta = delta
            elif delta_ts < current_ts:
                previous_deltas.append(delta)

        if current_delta is None or not previous_deltas:
            return None

        average_delta = sum(previous_deltas) / len(previous_deltas)
        if average_delta <= 0:
            return None

        return current_delta / average_delta

    def _record_alert(self, symbol: str, timestamp: datetime) -> None:
        self.storage.state.setdefault("last_alerts", {})[symbol] = timestamp.isoformat()
        self.storage.reset_alert_counter_if_needed()
        self.storage.state["alerts_today"]["count"] += 1

    def _record_recent_alert(self, alert: dict[str, Any], timestamp: datetime) -> None:
        setup_scenario = alert.get("setup_scenario") or {}
        execution_status = setup_scenario.get("execution_status") or "NO_SETUP"
        if execution_status not in {
            "ENTERABLE_NOW",
            "PENDING_LIMIT_ONLY",
            "TOO_LATE_DO_NOT_CHASE",
            "NO_SETUP",
        }:
            execution_status = "NO_SETUP"

        record = {
            "id": alert.get("alert_record_id") or self._alert_record_id(alert),
            "timestamp": timestamp.isoformat(),
            "symbol": alert["symbol"],
            "price": alert.get("price"),
            "price_change_percent": alert["price_change_percent"],
            "oi_change_percent": alert["oi_change_percent"],
            "volume_spike": alert["volume_spike"],
            "alert_type": alert.get("alert_type", "Radar Activity"),
            "risk_level": alert.get("risk_level", "Unknown"),
            "setup_bias": setup_scenario.get("bias", "Unknown"),
            "setup_status": setup_scenario.get("setup_status", "Unknown"),
            "execution_status": execution_status,
            "execution_short_label": setup_scenario.get("execution_short_label", "NO SETUP"),
            "source_setup_id": setup_scenario.get("setup_id") or self._source_setup_id(alert),
            "entry_zone": setup_scenario.get("entry_zone", "n/a"),
            "setup_entry_distance_percent": setup_scenario.get("setup_entry_distance_percent"),
            "invalidation": setup_scenario.get("invalidation", "n/a"),
            "tp1": setup_scenario.get("tp1", "n/a"),
            "tp2": setup_scenario.get("tp2", "n/a"),
        }
        self.storage.add_alert_record(record)

    def _alert_record_id(self, alert: dict[str, Any]) -> str:
        symbol = str(alert.get("symbol") or "UNKNOWN")
        timestamp = str(alert.get("timestamp") or int(time.time()))
        safe_timestamp = "".join(char if char.isalnum() else "_" for char in timestamp)
        return f"setup_{symbol}_{safe_timestamp}"[:48]

    def _source_setup_id(self, alert: dict[str, Any]) -> str:
        symbol = str(alert.get("symbol") or "UNKNOWN")
        timestamp = str(alert.get("timestamp") or int(time.time()))
        safe_timestamp = "".join(char if char.isalnum() else "_" for char in timestamp)
        return f"{symbol}-{safe_timestamp}"

    def _attach_setup_ids(self, alert: dict[str, Any]) -> None:
        setup = alert.get("setup_scenario")
        if not isinstance(setup, dict):
            return
        setup.setdefault("setup_id", alert.get("alert_record_id") or self._alert_record_id(alert))

    def _order_buttons_allowed(self, alert: dict[str, Any]) -> bool:
        setup = alert.get("setup_scenario") or {}
        if not isinstance(setup, dict):
            return False
        bias = str(setup.get("bias") or "")
        has_direction = bias in {"LONG", "LONG WATCH", "SHORT", "SHORT WATCH"}
        return (
            has_direction
            and alert.get("risk_level") != "EXTREME"
            and not self._is_symbol_blacklisted(alert.get("symbol"))
            and setup.get("setup_status") not in {None, "NO SETUP", "NO CHASE"}
            and setup.get("bias") != "WAIT"
            and setup.get("execution_status") in {"ENTERABLE_NOW", "PENDING_LIMIT_ONLY"}
            and setup.get("entry_zone") not in {None, "", "n/a"}
            and setup.get("invalidation") not in {None, "", "n/a"}
            and setup.get("tp1") not in {None, "", "n/a"}
            and setup.get("tp2") not in {None, "", "n/a"}
        )

    def _track_setup_if_actionable(self, alert: dict[str, Any]) -> None:
        if not config.ENABLE_SETUP_TRACKING:
            return

        setup_record = alert.get("setup_scenario")
        if not isinstance(setup_record, dict):
            return
        if self._is_symbol_blacklisted(alert.get("symbol")):
            self.logger.info("Skipping setup tracking for blacklisted symbol: %s", alert.get("symbol"))
            return
        if setup_record.get("execution_status") == "TOO_LATE_DO_NOT_CHASE":
            self.logger.info("Skipping too-late setup tracking: %s", alert.get("symbol"))
            return

        active_setup = create_active_setup_from_alert(alert, setup_record)
        if active_setup is None:
            return

        active_count = len(self.storage.get_active_setups())
        if active_count >= config.SETUP_TRACKING_MAX_ACTIVE:
            self.logger.warning(
                "Skipping active setup for %s: max active setups reached (%s)",
                alert.get("symbol"),
                config.SETUP_TRACKING_MAX_ACTIVE,
            )
            return

        if self.storage.add_active_setup(active_setup):
            self.logger.info(
                "Added active setup: %s %s",
                active_setup["symbol"],
                active_setup["direction"],
            )

    def _update_setup_tracking(self) -> None:
        if not config.ENABLE_SETUP_TRACKING:
            return

        try:
            events = update_active_setups(self.bybit, self.storage)
        except Exception as exc:
            self.logger.error("Setup tracking update failed: %s", exc)
            return

        for event in events:
            message = format_tracking_event_message(event)
            if not message:
                continue

            symbol = (event.get("setup") or {}).get("symbol")
            try:
                reply_markup = build_bybit_chart_keyboard(symbol) if symbol else None
                self.telegram.send_message(message, reply_markup=reply_markup)
            except Exception as exc:
                self.logger.error("Failed to send setup tracking notification for %s: %s", symbol, exc)

    def _in_alert_cooldown(self, symbol: str, timestamp: datetime) -> bool:
        last_alerts = self.storage.state.get("last_alerts", {})
        last_alert = last_alerts.get(symbol)
        if not last_alert:
            return False

        try:
            last_alert_dt = datetime.fromisoformat(last_alert)
        except ValueError:
            return False

        elapsed_seconds = (timestamp - last_alert_dt).total_seconds()
        return elapsed_seconds < config.ALERT_COOLDOWN_MINUTES * 60

    def _format_alert(self, alert: dict[str, Any]) -> str:
        funding = self._funding_rate_percent(alert)
        funding_text = "n/a" if funding is None else f"{funding:.4f}%"
        indicator_context = alert.get("indicator_context")
        warnings = alert.get("warnings") or []
        context_notes = alert.get("context_notes") or []
        setup_scenario = alert.get("setup_scenario")

        lines = [
            "🚨 BYBIT FUTURES RADAR",
            "",
            f"Symbol: {alert['symbol']}",
            f"Price: {alert['price']:.8g}",
            f"15m Price Change: {self._format_signed_percent(alert['price_change_percent'])}",
            f"15m OI Change: {self._format_signed_percent(alert['oi_change_percent'])}",
            f"Volume Spike: {alert['volume_spike']:.1f}x",
            f"Funding: {funding_text}",
            "",
            f"Alert Type: {alert.get('alert_type', 'Radar Activity')}",
            f"Risk Level: {alert.get('risk_level', 'MEDIUM')}",
            f"Action Note: {alert.get('action_note', 'Validate chart manually.')}",
            "",
            "Indicators:",
        ]

        if indicator_context:
            lines.extend(
                [
                    f"RSI({config.RSI_PERIOD}): {indicator_context['rsi']:.1f} - {indicator_context['rsi_note']}",
                    f"EMA Trend: {indicator_context['ema_trend']}",
                    (
                        "MACD Histogram: "
                        f"{self._format_signed_decimal(indicator_context['macd_histogram'])} - "
                        f"{indicator_context['macd_note']}"
                    ),
                    (
                        f"ATR: {indicator_context['atr']:.4g} / "
                        f"{indicator_context['atr_percent']:.2f}% - "
                        f"{indicator_context['risk_note']}"
                    ),
                ]
            )
        else:
            lines.append("unavailable")

        if context_notes:
            lines.extend(
                [
                    "",
                    "Context Notes:",
                ]
            )
            lines.extend(f"- {note}" for note in context_notes)

        if warnings:
            lines.extend(
                [
                    "",
                    "Warnings:",
                ]
            )
            lines.extend(f"- {warning}" for warning in warnings)

        if setup_scenario:
            lines.extend(["", "Setup Scenario:"])
            execution_label = setup_scenario.get("execution_label") or self._execution_label(
                setup_scenario.get("execution_status")
            )
            plan = setup_scenario.get("plan") or "Проверить график вручную."
            if setup_scenario.get("setup_status") == "NO SETUP":
                lines.extend(
                    [
                        f"Bias: {setup_scenario['bias']}",
                        f"Status: {setup_scenario['setup_status']}",
                        f"Execution: {execution_label}",
                        f"Reason: {setup_scenario['reason']}",
                        f"Warning: {setup_scenario['warning']}",
                        "",
                        "Plan:",
                    ]
                )
                lines.extend(str(plan).splitlines())
            else:
                lines.extend(
                    [
                        f"Bias: {setup_scenario['bias']}",
                        f"Status: {setup_scenario['setup_status']}",
                        f"Execution: {execution_label}",
                        f"Entry Zone: {setup_scenario['entry_zone']}",
                        f"Current Price: {setup_scenario.get('current_price', 'n/a')}",
                        f"Distance to Entry: {setup_scenario.get('distance_to_entry', setup_scenario.get('entry_distance', 'n/a'))}",
                        f"Invalidation: {setup_scenario['invalidation']}",
                        f"TP1: {setup_scenario['tp1']}",
                        f"TP2: {setup_scenario['tp2']}",
                        f"R/R: {setup_scenario['risk_reward']}",
                        f"Reason: {setup_scenario['reason']}",
                        f"Warning: {setup_scenario['warning']}",
                        "",
                        "Plan:",
                    ]
                )
                lines.extend(str(plan).splitlines())

        lines.extend(
            [
                "",
                "This is NOT a trade signal. Check chart manually.",
            ]
        )
        return "\n".join(lines)

    def _context_notes(self, alert: dict[str, Any]) -> list[str]:
        notes = []
        price_change = alert.get("price_change_percent", 0)
        funding_rate = self._funding_rate_percent(alert)
        indicator_context = alert.get("indicator_context") or {}
        ema_trend = indicator_context.get("ema_trend")
        macd_histogram = self._to_float(indicator_context.get("macd_histogram"))

        if funding_rate is not None and funding_rate <= -0.02 and price_change > 0:
            notes.append("Negative funding while price rises. Possible short squeeze pressure.")
        if funding_rate is not None and funding_rate >= 0.02 and price_change < 0:
            notes.append("Positive funding while price falls. Possible long squeeze pressure.")
        if funding_rate is not None and funding_rate <= -0.5 and price_change < 0:
            notes.append("Deep negative funding during price drop. Shorts may be crowded.")
        if funding_rate is not None and funding_rate >= 0.5 and price_change > 0:
            notes.append("High positive funding during price rise. Longs may be crowded.")
        if ema_trend == "bullish" and macd_histogram is not None and macd_histogram < 0:
            notes.append("EMA trend supports move, but MACD momentum conflicts.")
        if ema_trend == "bearish" and macd_histogram is not None and macd_histogram > 0:
            notes.append("EMA trend is bearish, but MACD momentum conflicts.")

        return notes

    def _risk_warnings(self, alert: dict[str, Any]) -> list[str]:
        warnings = []
        price_change = alert.get("price_change_percent", 0)
        volume_spike = alert.get("volume_spike", 0)
        indicator_context = alert.get("indicator_context") or {}
        rsi = self._to_float(indicator_context.get("rsi"))
        atr_percent = self._to_float(indicator_context.get("atr_percent"))

        if rsi is not None and price_change >= 8 and rsi >= 75:
            warnings.append("Strong 15m pump with overbought RSI. Late entry risk is extreme.")
        if rsi is not None and price_change > 0 and volume_spike >= 10 and rsi >= 70:
            warnings.append("RSI overbought with extreme volume anomaly.")
        if rsi is not None and price_change < 0 and volume_spike >= 10 and rsi <= 30:
            warnings.append("RSI oversold with extreme volume anomaly.")
        if volume_spike >= 20:
            warnings.append("Extreme volume anomaly. Validate liquidity and candle structure.")
        if rsi is not None and rsi >= 80:
            warnings.append("RSI extremely overbought. Pullback risk.")
        if rsi is not None and rsi <= 20:
            warnings.append("RSI extremely oversold. Rebound risk.")
        if abs(price_change) >= 10:
            warnings.append("Abnormal 15m move. High liquidation/squeeze risk.")
        if atr_percent is not None and atr_percent >= 2:
            warnings.append("High volatility environment.")

        return self._dedupe_text(warnings)

    def _max_risk_level(self, current: str, minimum: str) -> str:
        order = {
            "LOW": 0,
            "MEDIUM": 1,
            "HIGH": 2,
            "EXTREME": 3,
        }
        current_score = order.get(current, 1)
        minimum_score = order.get(minimum, 1)
        if current_score >= minimum_score:
            return current
        return minimum

    def _snapshot_volume_metric(self, snapshot: dict[str, Any]) -> float | None:
        if snapshot.get("volume_metric_24h") is not None:
            return self._to_float(snapshot.get("volume_metric_24h"))
        if snapshot.get("turnover_24h") is not None:
            return self._to_float(snapshot.get("turnover_24h"))
        return self._to_float(snapshot.get("volume_24h"))

    def _execution_label(self, status: Any) -> str:
        labels = {
            "ENTERABLE_NOW": "🟢 МОЖНО СМОТРЕТЬ ВХОД СЕЙЧАС",
            "PENDING_LIMIT_ONLY": "🟡 ТОЛЬКО ЛИМИТКА В ЗОНЕ / НЕ ВХОДИТЬ ПО РЫНКУ",
            "TOO_LATE_DO_NOT_CHASE": "🔴 ПОЕЗД УШЁЛ / НЕ ДОГОНЯТЬ",
            "NO_SETUP": "⚪ НЕТ СЕТАПА",
        }
        return labels.get(str(status or ""), labels["NO_SETUP"])

    def _is_symbol_blacklisted(self, symbol: Any) -> bool:
        symbol_text = str(symbol or "").upper()
        blacklist = self.storage.state.get("symbol_blacklist") or config.SYMBOL_BLACKLIST
        return symbol_text in {str(item).upper() for item in blacklist}

    def _execution_short_label(self, status: Any) -> str:
        labels = {
            "ENTERABLE_NOW": "ENTERABLE",
            "PENDING_LIMIT_ONLY": "LIMIT ONLY",
            "TOO_LATE_DO_NOT_CHASE": "TOO LATE",
            "NO_SETUP": "NO SETUP",
        }
        return labels.get(str(status or ""), "Unknown")

    def _alert_execution_status(self, alert: dict[str, Any]) -> str:
        status = alert.get("execution_status")
        if status in {
            "ENTERABLE_NOW",
            "PENDING_LIMIT_ONLY",
            "TOO_LATE_DO_NOT_CHASE",
            "NO_SETUP",
        }:
            return status
        return "legacy"

    def _execution_status_label_ru(self, status: str) -> str:
        labels = {
            "ENTERABLE_NOW": "ВХОД СЕЙЧАС",
            "PENDING_LIMIT_ONLY": "ТОЛЬКО ЛИМИТКА",
            "TOO_LATE_DO_NOT_CHASE": "ПОЕЗД УШЁЛ",
            "NO_SETUP": "НЕТ СЕТАПА",
            "legacy": "старый алерт",
        }
        return labels.get(status, "старый алерт")

    def _format_setup_zone(self, setup: dict[str, Any]) -> str:
        return (
            f"{self._format_price_value(setup.get('entry_low'))} - "
            f"{self._format_price_value(setup.get('entry_high'))}"
        )

    def _format_price_value(self, value: Any) -> str:
        price = self._to_float(value)
        if price is None:
            return "n/a"
        if price >= 100:
            return f"{price:.2f}"
        if price >= 1:
            return f"{price:.4f}"
        if price >= 0.01:
            return f"{price:.6f}"
        return f"{price:.8f}"

    def _format_age(self, value: Any) -> str:
        if not value:
            return "n/a"
        try:
            created_at = datetime.fromisoformat(str(value))
        except ValueError:
            return "n/a"
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        age_seconds = max(int((datetime.now(UTC) - created_at).total_seconds()), 0)
        hours = age_seconds // 3600
        minutes = (age_seconds % 3600) // 60
        if hours:
            return f"{hours}ч {minutes}м"
        return f"{minutes} мин"

    def _format_rate(self, count: int, total: int) -> str:
        if total <= 0:
            return "0%"
        return f"{(count / total) * 100:.0f}%"

    def _format_r_value(self, value: float) -> str:
        return f"{value:+.2f}R"

    def _format_stats_group(
        self,
        groups: dict[str, dict[str, int]],
        limit: int | None = None,
        translate_keys: bool = False,
    ) -> list[str]:
        rows = sorted(
            groups.items(),
            key=lambda item: item[1].get("total", 0),
            reverse=True,
        )
        if limit is not None:
            rows = rows[:limit]

        lines = []
        for name, values in rows:
            label = translate_direction(name) if translate_keys else name
            lines.append(
                (
                    f"{label}: всего {values.get('total', 0)}, "
                    f"TP1 only {values.get('tp1_only', 0)}, "
                    f"TP2 {values.get('tp2_hit', 0)}, "
                    f"сломано до TP1 {values.get('invalidated_before_tp1', 0)}, "
                    f"сломано после TP1 {values.get('invalidated_after_tp1', 0)}, "
                    f"истекло {values.get('expired', 0)}"
                )
            )
        return lines

    def _format_alert_time(self, value: Any) -> str:
        if not value:
            return "n/a"
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return str(value)
        return parsed.strftime("%Y-%m-%d %H:%M:%S UTC")

    def _format_alert_time_short(self, value: Any) -> str:
        if not value:
            return "n/a"
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return str(value)
        return parsed.strftime("%H:%M UTC")

    def _funding_rate_percent(self, alert: dict[str, Any]) -> float | None:
        value = self._to_float(alert.get("funding_rate_percent"))
        if value is not None:
            return value

        raw_value = self._to_float(alert.get("funding_rate"))
        if raw_value is not None:
            return raw_value * 100

        return None

    def _dedupe_text(self, values: list[str]) -> list[str]:
        seen = set()
        result = []
        for value in values:
            if value not in seen:
                seen.add(value)
                result.append(value)
        return result

    def _risk_summary(self, risk_note: str) -> str:
        if risk_note == "high volatility":
            return "High volatility"
        if risk_note == "low volatility":
            return "Low volatility"
        return "Volatility normal"

    def _percent_change(self, old: Any, new: Any) -> float | None:
        old_float = self._to_float(old)
        new_float = self._to_float(new)

        if old_float is None or new_float is None or old_float == 0:
            return None
        return ((new_float - old_float) / old_float) * 100

    def _to_float(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _format_signed_percent(self, value: float) -> str:
        return f"{value:+.2f}%"

    def _format_signed_decimal(self, value: float) -> str:
        if value != 0 and abs(value) < 0.0001:
            return f"{value:+.6g}"
        return f"{value:+.4f}"

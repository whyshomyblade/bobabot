import asyncio
import csv
import json
import logging
import signal
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import config
from backtester import (
    BACKTEST_EXPORT_FIELDS,
    Backtester,
    backtest_export_rows,
    format_backtest_report,
    format_backtest_result,
    format_backtest_top,
)
from bybit_client import BybitClient
from bybit_private_client import BybitPrivateClient
from health_server import start_health_server
from performance_analyzer import (
    JOURNAL_EXPORT_FIELDS,
    ORDERS_EXPORT_FIELDS,
    STATS_EXPORT_FIELDS,
    analyze_performance,
    format_dataset_progress,
    format_analytics,
    format_daily_report,
    format_recommendations,
    journal_export_rows,
    orders_export_rows,
    stats_export_rows,
)
from risk_manager import build_order_plan_from_alert, format_order_plan
from scanner import MarketScanner
from storage import Storage
from telegram_client import (
    TelegramClient,
    build_main_inline_menu_keyboard,
    build_main_menu_keyboard,
    build_order_confirmation_keyboard,
    build_radar_menu_keyboard,
    build_settings_menu_keyboard,
    build_setups_analytics_menu_keyboard,
    build_setups_core_menu_keyboard,
    build_setups_export_menu_keyboard,
    build_setups_menu_keyboard,
    build_setups_trading_menu_keyboard,
)


BUTTON_COMMANDS = {
    "📡 Радар": "__radar_menu__",
    "⚙️ Настройки": "__settings_menu__",
    "⬅️ Главное меню": "__main_menu__",
    "📊 Status": "/status",
    "🔥 Top OI": "/top",
    "🕘 Last Alerts": "/last",
    "🧪 Debug": "/debug_state",
    "⚙️ Config": "/config",
    "⏸ Pause": "/pause",
    "▶️ Resume": "/resume",
    "💾 Backup DB": "/backup_db",
    "❓ Help": "/help",
    "📒 Сетапы": "__setups_menu__",
    "📒 Активные сетапы": "/setups",
    "📘 Журнал": "/journal",
    "📈 Стата": "/stats",
    "🧠 Аналитика": "/analytics",
    "📊 Daily Report Now": "/daily_report_now",
    "🧪 Рекомендации": "/recommend_filters",
    "📋 Ордера": "/orders",
    "📊 Позиции": "/positions",
    "💰 Баланс": "/balance",
    "📤 Экспорт журнала": "/export_journal",
    "📤 Экспорт статистики": "/export_stats",
    "📤 Экспорт ордеров": "/export_orders",
    "🧪 Backtest": "/backtest",
    "📊 Backtest Report": "/backtest_report",
    "📤 Export Backtest": "/export_backtest",
    "📒 Setups": "/setups",
    "📘 Journal": "/journal",
    "📈 Stats": "/stats",
}


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def status_text(storage: Storage) -> str:
    storage.reset_alert_counter_if_needed()
    enabled = storage.state.get("monitoring_enabled", True)
    last_scan_time = storage.state.get("last_scan_time") or "never"
    symbols_count = len(storage.state.get("symbols", []))
    alerts_today = storage.state.get("alerts_today", {}).get("count", 0)
    closed_setups = len(storage.get_setup_journal(limit=None))

    return "\n".join(
        [
            "Bybit Futures Radar status",
            f"Monitoring: {'enabled' if enabled else 'disabled'}",
            f"Symbols monitored: {symbols_count}",
            f"Min 24h turnover filter: {config.MIN_24H_TURNOVER_USDT:,.0f} USDT",
            f"Last scan time: {last_scan_time}",
            f"Alerts today: {alerts_today}",
            "",
            format_dataset_progress(closed_setups),
        ]
    )


def debug_state_text(storage: Storage) -> str:
    return "\n".join(
        [
            "🧪 Debug State",
            f"База данных: {storage.database.path}",
            f"Активных сетапов: {storage.database.count_active_setups()}",
            f"Записей журнала: {storage.database.count_setup_journal()}",
            f"История алертов: {storage.database.count_alert_history()}",
            f"Paper/orders records: {storage.database.count_paper_orders()}",
            f"Backtest trades: {storage.database.count_backtest_trades()}",
            f"Последний скан: {storage.state.get('last_scan_time') or 'never'}",
            f"Hosting mode: {'ON' if config.HOSTING_MODE else 'OFF'}",
        ]
    )


def config_text() -> str:
    touch_mode_text = {
        "wick": "по high/low свечи",
        "close": "по последней цене",
    }.get(config.SETUP_TOUCH_MODE, config.SETUP_TOUCH_MODE)

    return "\n".join(
        [
            "⚙️ Настройки радара",
            "",
            "Основные фильтры:",
            f"• OI threshold: {config.OI_THRESHOLD_PERCENT}%",
            f"• Price move: {config.PRICE_CHANGE_THRESHOLD_PERCENT}%",
            f"• Volume spike: {config.VOLUME_SPIKE_MULTIPLIER}x",
            f"• Cooldown: {config.ALERT_COOLDOWN_MINUTES} min",
            f"• Scan interval: {config.SCAN_INTERVAL_SECONDS} sec",
            f"• Min 24h turnover: {config.MIN_24H_TURNOVER_USDT:,.0f} USDT",
            f"• Max symbols: {config.MAX_SYMBOLS_TO_MONITOR}",
            "",
            "Индикаторы:",
            f"• Indicators: {'ON' if config.ENABLE_INDICATORS else 'OFF'}",
            f"• RSI period: {config.RSI_PERIOD}",
            f"• EMA: {config.EMA_FAST_PERIOD} / {config.EMA_SLOW_PERIOD}",
            (
                "• MACD: "
                f"{config.MACD_FAST_PERIOD} / "
                f"{config.MACD_SLOW_PERIOD} / "
                f"{config.MACD_SIGNAL_PERIOD}"
            ),
            f"• ATR period: {config.ATR_PERIOD}",
            f"• Kline interval: {config.KLINE_INTERVAL}m",
            f"• Kline limit: {config.KLINE_LIMIT}",
            "",
            "Setup Generator:",
            f"• Setup Generator: {'ON' if config.ENABLE_SETUP_GENERATOR else 'OFF'}",
            f"• Entry ATR multiplier: {config.SETUP_ATR_ENTRY_MULTIPLIER:g}",
            f"• Stop ATR multiplier: {config.SETUP_ATR_STOP_MULTIPLIER:g}",
            f"• TP1 R: {config.SETUP_TP1_R_MULTIPLIER:g}",
            f"• TP2 R: {config.SETUP_TP2_R_MULTIPLIER:g}",
            f"• Min R/R: {config.MIN_RISK_REWARD:g}",
            f"• Min pullback normal: {config.MIN_PULLBACK_PERCENT_NORMAL:g}%",
            f"• Min pullback high risk: {config.MIN_PULLBACK_PERCENT_HIGH_RISK:g}%",
            f"• Min pullback extreme: {config.MIN_PULLBACK_PERCENT_EXTREME_RISK:g}%",
            f"• Max chase distance: {config.MAX_ENTRY_CHASE_DISTANCE_PERCENT:g}%",
            f"• Setup min price move: {config.SETUP_MIN_PRICE_CHANGE_PERCENT:g}%",
            f"• Setup min OI change: {config.SETUP_MIN_OI_CHANGE_PERCENT:g}%",
            f"• Setup min volume spike: {config.SETUP_MIN_VOLUME_SPIKE:g}x",
            f"• Setup min ATR: {config.SETUP_MIN_ATR_PERCENT:g}%",
            f"• Clean setup min price move: {config.SETUP_CLEAN_MIN_PRICE_CHANGE_PERCENT:g}%",
            f"• Clean setup min OI change: {config.SETUP_CLEAN_MIN_OI_CHANGE_PERCENT:g}%",
            f"• Clean setup min volume spike: {config.SETUP_CLEAN_MIN_VOLUME_SPIKE:g}x",
            "",
            "Трекинг сетапов:",
            f"• Трекинг сетапов: {'ВКЛ' if config.ENABLE_SETUP_TRACKING else 'ВЫКЛ'}",
            f"• Истечение: {config.SETUP_TRACKING_EXPIRATION_HOURS:g}ч",
            f"• Макс активных сетапов: {config.SETUP_TRACKING_MAX_ACTIVE}",
            f"• Режим касания: {touch_mode_text}",
            f"• Макс записей журнала: {config.SETUP_JOURNAL_MAX_RECORDS}",
            f"• Fee mode: {config.DEFAULT_EXECUTION_FEE_MODE}",
            f"• Taker fee: {config.BYBIT_TAKER_FEE_RATE * 100:.3f}%",
            f"• Maker fee: {config.BYBIT_MAKER_FEE_RATE * 100:.3f}%",
            f"• Slippage: {config.SLIPPAGE_PERCENT:g}%",
            f"• Мин. задержка входа: {config.MIN_SECONDS_AFTER_ALERT_FOR_ENTRY} сек",
            "",
            "Execution Assistant:",
            f"• Trading enabled: {str(config.BYBIT_TRADING_ENABLED).lower()}",
            f"• Testnet: {str(config.BYBIT_TESTNET).lower()}",
            f"• Account risk: {config.ACCOUNT_RISK_PERCENT:g}%",
            f"• Max position: {config.MAX_POSITION_USDT:g} USDT",
            f"• Max leverage: {config.MAX_LEVERAGE}x",
            f"• Default leverage: {config.DEFAULT_LEVERAGE}x",
            f"• Min R/R: {config.MIN_RR_TO_ALLOW_ORDER:g}",
            f"• Allow high risk orders: {str(config.ALLOW_HIGH_RISK_ORDERS).lower()}",
            "",
            "Analytics:",
            f"• Daily report: {'ON' if config.DAILY_REPORT_ENABLED else 'OFF'}",
            f"• Daily report hour UTC: {config.DAILY_REPORT_HOUR_UTC}",
            f"• Symbol blacklist: {len(config.SYMBOL_BLACKLIST)} default symbols",
            "",
            "Backtest:",
            f"• Backtest: {'ON' if config.BACKTEST_ENABLED else 'OFF'}",
            f"• Default days: {config.BACKTEST_DEFAULT_DAYS}",
            f"• Default interval: {config.BACKTEST_DEFAULT_INTERVAL}",
            f"• Max symbols: {config.BACKTEST_MAX_SYMBOLS}",
            f"• Min 24h turnover: {config.BACKTEST_MIN_24H_TURNOVER:,.0f} USDT",
            f"• Max candles/symbol: {config.BACKTEST_MAX_CANDLES_PER_SYMBOL}",
        ]
    )


def help_text() -> str:
    return "\n".join(
        [
            "🤖 Bybit Futures Radar Bot",
            "",
            "Бот сканирует Bybit USDT Perpetual и ищет резкую активность:",
            "• рост Open Interest",
            "• всплеск объёма",
            "• движение цены",
            "• funding",
            "• RSI / EMA / MACD / ATR",
            "",
            "Это НЕ торговый бот.",
            "Он НЕ открывает сделки.",
            "Он НЕ даёт финансовый совет.",
            "Он только показывает аномалии, которые надо проверять вручную на графике.",
            "",
            "Кнопки:",
            "📡 Радар — статус, топ OI, последние алерты, debug",
            "📒 Сетапы — сетапы, журнал, статистика, аналитика, экспорт, ордера",
            "⚙️ Настройки — config, pause/resume, backup DB",
            "❓ Help — помощь",
            "",
            "Как читать алерт:",
            "• OI растёт + цена растёт — в рынок заходят новые позиции по движению",
            "• OI растёт + цена падает — возможное давление шортов или лонг-сквиз",
            "• Volume Spike — объём выше среднего",
            "• Funding > 0 — лонги платят шортам",
            "• Funding < 0 — шорты платят лонгам",
            "• RSI > 70 — перегрето",
            "• RSI < 30 — перепродано",
            "• ATR% высокий — сильная волатильность",
            "",
            "Правило:",
            "Не входи по алерту вслепую. Сначала открой график.",
            "",
            "Бот отслеживает виртуальные сценарии. Это не реальные сделки и не торговый сигнал.",
            "",
            "Execution Assistant:",
            "Бот может рассчитать лимитный план, но НЕ отправляет ордер автоматически.",
            "Любой ордер требует явного подтверждения Telegram-кнопкой.",
            "По умолчанию включён paper mode: реальные ордера на Bybit не отправляются.",
            "",
            "Backtest:",
            "/backtest SYMBOL DAYS — исторический replay текущей логики сетапов.",
            "/backtest_report — последний отчёт backtest.",
            "/export_backtest — экспорт сделок backtest в CSV/JSON.",
            "Исторический тест не доказывает будущую прибыль: OI/funding, задержки, проскальзывание и лимитное исполнение могут отличаться.",
        ]
    )


def send_with_keyboard(
    telegram: TelegramClient,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> None:
    telegram.send_message(text, reply_markup=reply_markup or build_main_menu_keyboard())


def handle_update(
    update: dict[str, Any],
    storage: Storage,
    telegram: TelegramClient,
    scanner: MarketScanner,
    private_client: BybitPrivateClient,
) -> None:
    if update.get("callback_query"):
        handle_callback_query(update["callback_query"], storage, telegram, scanner, private_client)
        return

    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")

    if not telegram.is_authorized_chat(chat_id):
        logging.getLogger("CommandHandler").warning("Ignoring command from unauthorized chat_id=%s", chat_id)
        return

    text = (message.get("text") or "").strip()
    args: list[str] = []
    if text in BUTTON_COMMANDS:
        command = BUTTON_COMMANDS[text]
    elif text.startswith("/"):
        parts = text.split()
        command = parts[0].split("@")[0].lower()
        args = parts[1:]
    else:
        send_with_keyboard(
            telegram,
            "Не понял команду. Выбери действие из меню.",
            build_main_menu_keyboard(),
        )
        return

    dispatch_command(command, storage, telegram, scanner, private_client, args=args)


def dispatch_command(
    command: str,
    storage: Storage,
    telegram: TelegramClient,
    scanner: MarketScanner,
    private_client: BybitPrivateClient,
    args: list[str] | None = None,
) -> None:
    args = args or []
    if command == "__main_menu__":
        send_with_keyboard(telegram, "Главное меню", build_main_inline_menu_keyboard())
    elif command == "__radar_menu__":
        send_with_keyboard(telegram, "📡 Радар", build_radar_menu_keyboard())
    elif command == "__setups_menu__":
        send_with_keyboard(telegram, "📒 Сетапы", build_setups_menu_keyboard())
    elif command == "__settings_menu__":
        send_with_keyboard(telegram, "⚙️ Настройки", build_settings_menu_keyboard())
    elif command == "/start":
        send_with_keyboard(telegram, "Bybit futures radar bot is alive.", build_main_menu_keyboard())
    elif command == "/help":
        send_with_keyboard(telegram, help_text(), build_main_menu_keyboard())
    elif command == "/status":
        send_with_keyboard(telegram, status_text(storage), build_radar_menu_keyboard())
    elif command == "/config":
        send_with_keyboard(telegram, config_text(), build_settings_menu_keyboard())
    elif command == "/last":
        send_with_keyboard(telegram, scanner.format_last_alerts(limit=10), build_radar_menu_keyboard())
    elif command == "/setups":
        send_with_keyboard(telegram, scanner.format_active_setups(), build_setups_menu_keyboard())
    elif command == "/journal":
        send_with_keyboard(telegram, scanner.format_setup_journal(limit=10), build_setups_menu_keyboard())
    elif command in {"/statistics", "/stats"}:
        send_with_keyboard(telegram, scanner.format_setup_statistics(), build_setups_menu_keyboard())
    elif command == "/analytics":
        send_analytics(storage, telegram)
    elif command == "/daily_report_now":
        send_daily_report_now(storage, telegram)
    elif command == "/recommend_filters":
        send_recommendations(storage, telegram)
    elif command == "/export_journal":
        export_journal(storage, telegram)
    elif command == "/export_stats":
        export_stats(storage, telegram)
    elif command == "/export_orders":
        export_orders(storage, telegram)
    elif command == "/backtest":
        run_backtest_command(storage, telegram, scanner, args)
    elif command == "/backtest_report":
        send_backtest_report(storage, telegram)
    elif command == "/backtest_top":
        send_backtest_top(storage, telegram)
    elif command == "/export_backtest":
        export_backtest(storage, telegram)
    elif command == "/blacklist":
        send_with_keyboard(telegram, blacklist_text(storage), build_setups_menu_keyboard())
    elif command == "/blacklist_add":
        send_with_keyboard(telegram, blacklist_add(storage, args), build_setups_menu_keyboard())
    elif command == "/blacklist_remove":
        send_with_keyboard(telegram, blacklist_remove(storage, args), build_setups_menu_keyboard())
    elif command == "/debug_state":
        send_with_keyboard(telegram, debug_state_text(storage), build_radar_menu_keyboard())
    elif command == "/backup_db":
        backup_path = storage.backup_database()
        send_with_keyboard(telegram, f"✅ Бэкап базы создан: {backup_path}", build_settings_menu_keyboard())
    elif command == "/orders":
        send_with_keyboard(telegram, orders_text(storage, private_client), build_setups_menu_keyboard())
    elif command == "/positions":
        send_with_keyboard(telegram, positions_text(private_client), build_setups_menu_keyboard())
    elif command == "/balance":
        send_with_keyboard(telegram, balance_text(private_client), build_setups_menu_keyboard())
    elif command == "/pause":
        storage.state["monitoring_enabled"] = False
        storage.save()
        send_with_keyboard(telegram, "Monitoring paused.", build_settings_menu_keyboard())
    elif command == "/resume":
        storage.state["monitoring_enabled"] = True
        storage.save()
        send_with_keyboard(telegram, "Monitoring resumed.", build_settings_menu_keyboard())
    elif command == "/top":
        send_with_keyboard(telegram, scanner.format_top_oi_growth(), build_radar_menu_keyboard())
    else:
        send_with_keyboard(
            telegram,
            "Не понял команду. Выбери действие из меню.",
            build_main_menu_keyboard(),
        )


def handle_callback_query(
    callback_query: dict[str, Any],
    storage: Storage,
    telegram: TelegramClient,
    scanner: MarketScanner,
    private_client: BybitPrivateClient,
) -> None:
    callback_id = str(callback_query.get("id") or "")
    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if not telegram.is_authorized_chat(chat_id):
        logging.getLogger("CommandHandler").warning("Ignoring callback from unauthorized chat_id=%s", chat_id)
        return

    data = str(callback_query.get("data") or "")
    if callback_id:
        telegram.answer_callback_query(callback_id)

    if data == "menu:main":
        edit_menu_message(telegram, message, "Главное меню", build_main_inline_menu_keyboard())
    elif data == "menu:radar":
        edit_menu_message(telegram, message, "📡 Радар", build_radar_menu_keyboard())
    elif data == "menu:setups":
        edit_menu_message(telegram, message, "📒 Сетапы", build_setups_menu_keyboard())
    elif data == "menu:setups_core":
        edit_menu_message(telegram, message, "📒 Сетапы", build_setups_core_menu_keyboard())
    elif data == "menu:setups_analytics":
        edit_menu_message(telegram, message, "🧠 Аналитика", build_setups_analytics_menu_keyboard())
    elif data == "menu:setups_export":
        edit_menu_message(telegram, message, "📤 Экспорт", build_setups_export_menu_keyboard())
    elif data == "menu:setups_trading":
        edit_menu_message(telegram, message, "💰 Trading", build_setups_trading_menu_keyboard())
    elif data == "menu:backtest_help":
        edit_menu_message(
            telegram,
            message,
            "Использование: /backtest SYMBOL DAYS\nПример: /backtest BTCUSDT 3",
            build_setups_analytics_menu_keyboard(),
        )
    elif data == "menu:settings":
        edit_menu_message(telegram, message, "⚙️ Настройки", build_settings_menu_keyboard())
    elif data == "menu:close":
        close_menu_message(telegram, message)
    elif data.startswith("cmd:"):
        command = data.split(":", 1)[1]
        dispatch_command(command, storage, telegram, scanner, private_client)
    elif data.startswith("order_calc:"):
        alert_id = data.split(":", 1)[1]
        calculate_order_plan(alert_id, storage, telegram, private_client)
    elif data.startswith("order_submit:"):
        plan_id = data.split(":", 1)[1]
        submit_order_plan(plan_id, storage, telegram, private_client)
    elif data.startswith("order_cancel:"):
        plan_id = data.split(":", 1)[1]
        storage.update_paper_order(plan_id, {"status": "CANCELLED"})
        send_with_keyboard(telegram, "❌ План ордера отменён.", build_main_menu_keyboard())
    elif data.startswith("order_skip:"):
        send_with_keyboard(telegram, "🚫 Сетап пропущен.", build_main_menu_keyboard())
    else:
        send_with_keyboard(telegram, "Не понял действие. Выбери действие из меню.", build_main_menu_keyboard())


def edit_menu_message(
    telegram: TelegramClient,
    message: dict[str, Any],
    text: str,
    reply_markup: dict[str, Any] | None,
) -> None:
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if chat_id is None or message_id is None:
        telegram.send_message(text, reply_markup=reply_markup or build_main_menu_keyboard())
        return
    try:
        telegram.edit_message_text(chat_id, message_id, text, reply_markup=reply_markup)
    except Exception as exc:
        logging.getLogger("CommandHandler").warning("Could not edit menu message: %s", exc)
        telegram.send_message(text, reply_markup=reply_markup or build_main_menu_keyboard())


def close_menu_message(telegram: TelegramClient, message: dict[str, Any]) -> None:
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if chat_id is None or message_id is None:
        telegram.send_message("Меню закрыто.", reply_markup=build_main_menu_keyboard())
        return
    try:
        telegram.delete_message(chat_id, message_id)
    except Exception as exc:
        logging.getLogger("CommandHandler").warning("Could not delete menu message: %s", exc)
        edit_menu_message(telegram, message, "Меню закрыто.", None)


def calculate_order_plan(
    alert_id: str,
    storage: Storage,
    telegram: TelegramClient,
    private_client: BybitPrivateClient,
) -> None:
    logger = logging.getLogger("ExecutionAssistant")
    if config.BYBIT_TRADING_ENABLED and not private_client.has_api_keys:
        send_with_keyboard(telegram, "Bybit API keys не настроены.", build_main_menu_keyboard())
        return

    alert = storage.get_alert_record(alert_id)
    if alert is None:
        send_with_keyboard(telegram, "❌ Ордер отклонён\nПричина: алерт не найден или это старый алерт.")
        return

    symbol = str(alert.get("symbol") or "")
    try:
        instrument_info = private_client.get_instrument_info(symbol)
    except Exception as exc:
        logger.error("Order plan rejected: instrument info failed symbol=%s error=%s", symbol, exc)
        send_with_keyboard(telegram, f"❌ Ордер отклонён\nПричина: instrument info unavailable ({exc})")
        return

    try:
        account_balance = account_balance_for_planning(private_client)
    except Exception as exc:
        logger.error("Order plan rejected: balance check failed symbol=%s error=%s", symbol, exc)
        send_with_keyboard(telegram, f"❌ Ордер отклонён\nПричина: не удалось получить баланс ({exc})")
        return
    has_paper_order = has_active_paper_order(storage, symbol)
    has_real_order = False
    has_real_position = False
    if private_client.can_call_private:
        try:
            has_real_order = bool(private_client.get_open_orders(symbol))
            has_real_position = has_active_position(private_client.get_positions(symbol))
        except Exception as exc:
            logger.warning("Could not check real orders/positions for %s: %s", symbol, exc)

    plan = build_order_plan_from_alert(
        alert=alert,
        instrument_info=instrument_info,
        account_balance_usdt=account_balance,
        leverage=config.DEFAULT_LEVERAGE,
        has_existing_order=has_paper_order or has_real_order,
        has_existing_position=has_real_position,
    )
    logger.info(
        "Order decision: %s symbol=%s side=%s setup_id=%s execution_status=%s risk=%s reason=%s",
        "allowed" if plan.get("allowed") else "rejected",
        symbol,
        plan.get("side"),
        plan.get("source_setup_id"),
        plan.get("execution_status"),
        plan.get("risk_level"),
        ", ".join(plan.get("reasons") or []),
    )

    if not plan.get("allowed"):
        send_with_keyboard(telegram, format_order_plan(plan), build_main_menu_keyboard())
        return

    plan_id = f"ord_{uuid.uuid4().hex[:18]}"
    plan["id"] = plan_id
    plan["status"] = "PLANNED"
    storage.add_paper_order(plan)
    telegram.send_message(
        format_order_plan(plan),
        reply_markup=build_order_confirmation_keyboard(plan_id, private_client.can_trade_real),
    )


def submit_order_plan(
    plan_id: str,
    storage: Storage,
    telegram: TelegramClient,
    private_client: BybitPrivateClient,
) -> None:
    logger = logging.getLogger("ExecutionAssistant")
    plan = storage.get_paper_order(plan_id)
    if plan is None:
        send_with_keyboard(telegram, "❌ План ордера не найден.", build_main_menu_keyboard())
        return
    if plan.get("status") != "PLANNED":
        send_with_keyboard(telegram, f"❌ План уже имеет статус: {plan.get('status')}", build_main_menu_keyboard())
        return

    if config.BYBIT_TRADING_ENABLED and not private_client.has_api_keys:
        send_with_keyboard(telegram, "Bybit API keys не настроены.", build_main_menu_keyboard())
        return

    if not private_client.can_trade_real:
        updated = storage.update_paper_order(plan_id, {"status": "SUBMITTED", "mode": "PAPER"})
        logger.info(
            "Paper order submitted symbol=%s side=%s setup_id=%s execution_status=%s risk=%s",
            plan.get("symbol"),
            plan.get("side"),
            plan.get("source_setup_id"),
            plan.get("execution_status"),
            plan.get("risk_level"),
        )
        send_with_keyboard(
            telegram,
            "\n".join(
                [
                    "🧪 Paper order created. Реальный ордер не отправлен.",
                    "Paper mode: ордер НЕ отправлен на Bybit",
                    f"Монета: {(updated or plan).get('symbol')}",
                    f"Направление: {(updated or plan).get('side')}",
                    f"Entry limit: {(updated or plan).get('entry_price')}",
                ]
            ),
            build_main_menu_keyboard(),
        )
        return

    try:
        response = private_client.place_limit_order(
            symbol=str(plan.get("symbol")),
            side=str(plan.get("bybit_side")),
            qty=str(plan.get("qty")),
            price=str(plan.get("entry_price")),
            reduce_only=False,
        )
        result = response.get("result") or {}
        storage.update_paper_order(
            plan_id,
            {
                "status": "SUBMITTED",
                "mode": private_client.mode_label(),
                "exchange_order_id": result.get("orderId"),
                "exchange_response": response,
            },
        )
        logger.info(
            "Real/testnet limit order submitted symbol=%s side=%s order_id=%s",
            plan.get("symbol"),
            plan.get("side"),
            result.get("orderId"),
        )
        send_with_keyboard(
            telegram,
            "\n".join(
                [
                    "✅ Лимитный ордер отправлен.",
                    f"Режим: {private_client.mode_label()}",
                    f"Монета: {plan.get('symbol')}",
                    f"Order ID: {result.get('orderId', 'n/a')}",
                ]
            ),
            build_main_menu_keyboard(),
        )
    except Exception as exc:
        logger.error("Order submit failed symbol=%s error=%s", plan.get("symbol"), exc)
        storage.update_paper_order(plan_id, {"status": "REJECTED", "reject_reason": str(exc)})
        send_with_keyboard(telegram, f"❌ Ордер отклонён\nПричина: {exc}", build_main_menu_keyboard())


def send_analytics(storage: Storage, telegram: TelegramClient) -> None:
    try:
        analysis = build_analysis(storage)
        send_with_keyboard(telegram, format_analytics(analysis), build_setups_menu_keyboard())
    except Exception as exc:
        logging.getLogger("Analytics").error("Analytics failed: %s", exc)
        send_with_keyboard(telegram, "Ошибка аналитики, сканер продолжает работать.", build_setups_menu_keyboard())


def send_recommendations(storage: Storage, telegram: TelegramClient) -> None:
    try:
        analysis = build_analysis(storage)
        send_with_keyboard(telegram, format_recommendations(analysis), build_setups_menu_keyboard())
    except Exception as exc:
        logging.getLogger("Analytics").error("Recommendations failed: %s", exc)
        send_with_keyboard(telegram, "Ошибка аналитики, сканер продолжает работать.", build_setups_menu_keyboard())


def send_daily_report_now(storage: Storage, telegram: TelegramClient) -> None:
    try:
        report = build_daily_report(storage)
        send_with_keyboard(telegram, report, build_setups_menu_keyboard())
    except Exception as exc:
        logging.getLogger("Analytics").error("Daily report failed: %s", exc)
        send_with_keyboard(telegram, "Ошибка аналитики, сканер продолжает работать.", build_setups_menu_keyboard())


def build_analysis(storage: Storage, since: datetime | None = None) -> dict[str, Any]:
    return analyze_performance(
        storage.get_setup_journal(limit=None),
        active_setups=storage.get_active_setups(),
        alert_records=storage.get_alert_history(limit=None),
        since=since,
    )


def build_daily_report(storage: Storage) -> str:
    since = datetime.now(UTC) - timedelta(hours=24)
    analysis = build_analysis(storage, since=since)
    return format_daily_report(analysis, since)


def maybe_send_daily_report(storage: Storage, telegram: TelegramClient) -> None:
    if not config.DAILY_REPORT_ENABLED:
        return
    now = datetime.now(UTC)
    if now.hour < config.DAILY_REPORT_HOUR_UTC:
        return
    today = now.date().isoformat()
    if storage.state.get("last_daily_report_date") == today:
        return
    try:
        telegram.send_message(build_daily_report(storage), chat_id=config.DAILY_REPORT_CHAT_ID)
        storage.state["last_daily_report_date"] = today
        storage.save_runtime_state("last_daily_report_date", today)
    except Exception as exc:
        logging.getLogger("Analytics").error("Daily report send failed: %s", exc)


def export_journal(storage: Storage, telegram: TelegramClient) -> None:
    try:
        rows = journal_export_rows(storage.get_setup_journal(limit=None))
        send_export_files(telegram, "journal_export", rows, JOURNAL_EXPORT_FIELDS)
    except Exception as exc:
        logging.getLogger("Analytics").error("Journal export failed: %s", exc)
        send_with_keyboard(telegram, "Ошибка аналитики, сканер продолжает работать.", build_setups_menu_keyboard())


def export_stats(storage: Storage, telegram: TelegramClient) -> None:
    try:
        rows = stats_export_rows(build_analysis(storage))
        send_export_files(telegram, "stats_export", rows, STATS_EXPORT_FIELDS)
    except Exception as exc:
        logging.getLogger("Analytics").error("Stats export failed: %s", exc)
        send_with_keyboard(telegram, "Ошибка аналитики, сканер продолжает работать.", build_setups_menu_keyboard())


def export_orders(storage: Storage, telegram: TelegramClient) -> None:
    try:
        rows = orders_export_rows(storage.get_paper_orders(limit=None))
        send_export_files(telegram, "orders_export", rows, ORDERS_EXPORT_FIELDS)
    except Exception as exc:
        logging.getLogger("Analytics").error("Orders export failed: %s", exc)
        send_with_keyboard(telegram, "Ошибка аналитики, сканер продолжает работать.", build_setups_menu_keyboard())


def run_backtest_command(
    storage: Storage,
    telegram: TelegramClient,
    scanner: MarketScanner,
    args: list[str],
) -> None:
    try:
        symbol = args[0].upper() if args else "BTCUSDT"
        days = config.BACKTEST_DEFAULT_DAYS
        if len(args) >= 2:
            try:
                days = max(int(args[1]), 1)
            except ValueError:
                send_with_keyboard(telegram, "Использование: /backtest SYMBOL DAYS", build_setups_menu_keyboard())
                return

        send_with_keyboard(
            telegram,
            f"🧪 Backtest started: {symbol} {days}d. Live scanner продолжает работать.",
            build_setups_menu_keyboard(),
        )
        backtester = Backtester(scanner.bybit, storage, scanner)
        result = backtester.run([symbol], days=days, interval=config.BACKTEST_DEFAULT_INTERVAL)
        send_with_keyboard(telegram, format_backtest_result(result), build_setups_menu_keyboard())
    except Exception as exc:
        logging.getLogger("Backtest").error("Backtest command failed: %s", exc)
        send_with_keyboard(telegram, "Ошибка backtest, live scanner продолжает работать.", build_setups_menu_keyboard())


def send_backtest_report(storage: Storage, telegram: TelegramClient) -> None:
    try:
        run = storage.get_last_backtest_run()
        trades = storage.get_backtest_trades(run_id=run.get("id")) if run else []
        send_with_keyboard(telegram, format_backtest_report(run, trades), build_setups_menu_keyboard())
    except Exception as exc:
        logging.getLogger("Backtest").error("Backtest report failed: %s", exc)
        send_with_keyboard(telegram, "Ошибка backtest, live scanner продолжает работать.", build_setups_menu_keyboard())


def send_backtest_top(storage: Storage, telegram: TelegramClient) -> None:
    try:
        run = storage.get_last_backtest_run()
        trades = storage.get_backtest_trades(run_id=run.get("id")) if run else []
        send_with_keyboard(telegram, format_backtest_top(run, trades), build_setups_menu_keyboard())
    except Exception as exc:
        logging.getLogger("Backtest").error("Backtest top failed: %s", exc)
        send_with_keyboard(telegram, "Ошибка backtest, live scanner продолжает работать.", build_setups_menu_keyboard())


def export_backtest(storage: Storage, telegram: TelegramClient) -> None:
    try:
        run = storage.get_last_backtest_run()
        if not run:
            send_with_keyboard(telegram, "Backtest данных пока нет. Запусти /backtest SYMBOL DAYS.", build_setups_menu_keyboard())
            return
        rows = backtest_export_rows(storage.get_backtest_trades(run_id=run.get("id")))
        send_export_files(telegram, "backtest_export", rows, BACKTEST_EXPORT_FIELDS)
    except Exception as exc:
        logging.getLogger("Backtest").error("Backtest export failed: %s", exc)
        send_with_keyboard(telegram, "Ошибка backtest, live scanner продолжает работать.", build_setups_menu_keyboard())


def send_export_files(
    telegram: TelegramClient,
    prefix: str,
    rows: list[dict[str, Any]],
    fields: list[str],
) -> None:
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    csv_path = Path(f"{prefix}_{timestamp}.csv")
    json_path = Path(f"{prefix}_{timestamp}.json")
    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    with json_path.open("w", encoding="utf-8") as file:
        json.dump(rows, file, ensure_ascii=False, indent=2)
    telegram.send_document(csv_path, caption=f"{prefix} CSV")
    telegram.send_document(json_path, caption=f"{prefix} JSON")
    send_with_keyboard(telegram, f"✅ Экспорт готов: {csv_path.name}, {json_path.name}", build_setups_menu_keyboard())


def blacklist_text(storage: Storage) -> str:
    symbols = sorted({str(item).upper() for item in storage.state.get("symbol_blacklist", [])})
    if not symbols:
        return "⚫ Blacklist пуст."
    return "⚫ Blacklist:\n" + "\n".join(f"- {symbol}" for symbol in symbols)


def blacklist_add(storage: Storage, args: list[str]) -> str:
    if not args:
        return "Использование: /blacklist_add SYMBOL"
    symbol = args[0].upper()
    symbols = {str(item).upper() for item in storage.state.get("symbol_blacklist", [])}
    symbols.add(symbol)
    storage.state["symbol_blacklist"] = sorted(symbols)
    storage.save_runtime_state("symbol_blacklist", storage.state["symbol_blacklist"])
    return f"⚫ {symbol} добавлен в blacklist."


def blacklist_remove(storage: Storage, args: list[str]) -> str:
    if not args:
        return "Использование: /blacklist_remove SYMBOL"
    symbol = args[0].upper()
    symbols = {str(item).upper() for item in storage.state.get("symbol_blacklist", [])}
    symbols.discard(symbol)
    storage.state["symbol_blacklist"] = sorted(symbols)
    storage.save_runtime_state("symbol_blacklist", storage.state["symbol_blacklist"])
    return f"⚫ {symbol} удалён из blacklist."


def orders_text(storage: Storage, private_client: BybitPrivateClient) -> str:
    lines = ["📋 Ордера:"]
    paper_orders = storage.get_paper_orders(statuses=["PLANNED", "SUBMITTED"], limit=10)
    if paper_orders:
        lines.append("")
        lines.append("Активные paper/assistant ордера:")
        for order in paper_orders:
            lines.append(
                (
                    f"- {order.get('symbol')} | {_direction_ru(order.get('side'))} | {_order_status_ru(order.get('status'))} | "
                    f"{order.get('mode')} | Entry {order.get('entry_price')} | Qty {order.get('qty')}"
                )
            )
    else:
        lines.append("- активных paper orders нет")

    if private_client.can_call_private:
        try:
            open_orders = private_client.get_open_orders()
            lines.append("")
            lines.append("Открытые ордера Bybit:")
            if open_orders:
                for order in open_orders[:10]:
                    lines.append(
                        (
                            f"- {order.get('symbol')} | {order.get('side')} | "
                            f"{order.get('orderStatus')} | {order.get('qty')} @ {order.get('price')}"
                        )
                    )
            else:
                lines.append("- открытых ордеров нет")
        except Exception as exc:
            lines.append(f"Открытые ордера Bybit: ошибка API ({exc})")
    else:
        lines.append("")
        lines.append("Ордера Bybit: API trading disabled")
    return "\n".join(lines)


def positions_text(private_client: BybitPrivateClient) -> str:
    if not private_client.can_call_private:
        return "📊 Позиции:\nAPI trading disabled"
    try:
        positions = [
            item
            for item in private_client.get_positions()
            if abs(_to_float(item.get("size")) or 0) > 0
        ]
    except Exception as exc:
        return f"📊 Позиции:\nОшибка API: {exc}"
    if not positions:
        return "📊 Позиции:\nОткрытых позиций нет."
    lines = ["📊 Позиции:"]
    for item in positions[:10]:
        lines.append(
            (
                f"- {item.get('symbol')} | {item.get('side')} | "
                f"Размер: {item.get('size')} | Вход: {item.get('avgPrice')} | PnL: {item.get('unrealisedPnl')}"
            )
        )
    return "\n".join(lines)


def balance_text(private_client: BybitPrivateClient) -> str:
    if not private_client.can_call_private:
        return "💰 Баланс:\nAPI trading disabled"
    try:
        balance = private_client.get_account_balance()
        usdt = extract_usdt_balance(balance)
    except Exception as exc:
        return f"💰 Баланс:\nОшибка API: {exc}"
    return f"💰 Баланс:\nUSDT: {usdt:.2f}"


def account_balance_for_planning(private_client: BybitPrivateClient) -> float:
    if not private_client.can_call_private:
        return config.PAPER_ACCOUNT_BALANCE_USDT
    return extract_usdt_balance(private_client.get_account_balance())


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
                return _to_float(coin.get("equity") or coin.get("walletBalance")) or 0.0
    return 0.0


def has_active_paper_order(storage: Storage, symbol: str) -> bool:
    for order in storage.get_paper_orders(statuses=["PLANNED", "SUBMITTED"]):
        if order.get("symbol") == symbol:
            return True
    return False


def has_active_position(positions: list[dict[str, Any]]) -> bool:
    return any(abs(_to_float(item.get("size")) or 0) > 0 for item in positions)


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _direction_ru(value: Any) -> str:
    return {"LONG": "ЛОНГ", "SHORT": "ШОРТ"}.get(str(value or ""), str(value or "n/a"))


def _order_status_ru(value: Any) -> str:
    labels = {
        "PLANNED": "ЗАПЛАНИРОВАН",
        "SUBMITTED": "ОТПРАВЛЕН",
        "FILLED": "ИСПОЛНЕН",
        "CANCELLED": "ОТМЕНЁН",
        "REJECTED": "ОТКЛОНЁН",
    }
    return labels.get(str(value or ""), str(value or "n/a"))


def poll_telegram_commands(
    storage: Storage,
    telegram: TelegramClient,
    scanner: MarketScanner,
    private_client: BybitPrivateClient,
) -> None:
    logger = logging.getLogger("TelegramPoller")
    offset = storage.state.get("telegram_update_offset")

    try:
        updates = telegram.get_updates(offset=offset)
    except Exception as exc:
        logger.error("Could not poll Telegram updates: %s", exc)
        return

    if not updates:
        return

    for update in updates:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            next_offset = update_id + 1
            current_offset = storage.state.get("telegram_update_offset")
            if current_offset is None or next_offset > current_offset:
                storage.state["telegram_update_offset"] = next_offset

        try:
            handle_update(update, storage, telegram, scanner, private_client)
        except Exception as exc:
            logger.error("Failed to handle Telegram update: %s", exc)

    storage.save()


async def main_async() -> None:
    setup_logging()
    logger = logging.getLogger("main")
    config.validate_config()
    health_server = start_health_server()

    storage = Storage()
    bybit = BybitClient()
    private_client = BybitPrivateClient()
    telegram = TelegramClient(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
    scanner = MarketScanner(bybit, storage, telegram)

    logger.info("Starting Bybit Futures Radar")
    if config.HOSTING_MODE:
        logger.info("Hosting mode enabled")

    bybit_available = bybit.check_connectivity()
    if bybit_available:
        try:
            scanner.refresh_symbols_if_needed(force=True)
        except Exception as exc:
            logger.error("Initial symbol refresh failed: %s", exc)
    else:
        logger.error("Bybit is unavailable; scanner will retry connectivity before scans")

    def run_scan_job() -> None:
        nonlocal bybit_available
        if not bybit_available:
            logger.warning("Bybit unavailable; retrying connectivity check before scan")
            bybit_available = bybit.check_connectivity()
            if not bybit_available:
                return

        try:
            scanner.scan_once()
        except Exception as exc:
            logger.error("Scan failed: %s", exc)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_shutdown() -> None:
        logger.info("Shutdown requested")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_shutdown)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda _signum, _frame: request_shutdown())

    try:
        run_scan_job()
        next_scan_ts = time.monotonic() + config.SCAN_INTERVAL_SECONDS

        while not stop_event.is_set():
            if time.monotonic() >= next_scan_ts:
                run_scan_job()
                maybe_send_daily_report(storage, telegram)
                next_scan_ts = time.monotonic() + config.SCAN_INTERVAL_SECONDS

            poll_telegram_commands(storage, telegram, scanner, private_client)
            await asyncio.sleep(1)
    finally:
        if health_server is not None:
            health_server.shutdown()
            health_server.server_close()
        storage.close()
        logger.info("Bot stopped safely")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.getLogger("main").info("Bot stopped safely")

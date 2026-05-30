import asyncio
import csv
import json
import logging
import math
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
from order_lifecycle import (
    build_order_lifecycle_keyboard,
    format_order_status,
    format_order_sync_summary,
    sync_testnet_orders,
)
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
from setup_tracker import (
    execution_quality_for_record,
    fee_adjusted_result_r_for_record,
    normalize_final_state,
)
from storage import Storage
from testnet_autopilot import (
    TestnetAutopilot,
    autopilot_block_reasons,
    autopilot_debug_last_text,
    autopilot_enabled,
    autopilot_journal_text,
    autopilot_rules_text,
    autopilot_status_text,
)
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
    build_trading_api_menu_keyboard,
    build_trading_autopilot_menu_keyboard,
    build_trading_emergency_menu_keyboard,
    build_trading_fees_menu_keyboard,
    build_trading_orders_menu_keyboard,
    build_trading_risk_menu_keyboard,
    build_trading_runtime_menu_keyboard,
    build_trading_real_menu_keyboard,
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
    "🔑 API Status": "/api_status",
    "🧪 API Test": "/api_test",
    "📘 API Help": "/api_help",
    "🔐 API Set": "/api_set",
    "🔐 Внести API ключи": "/api_set",
    "🧹 API Clear": "/api_clear",
    "🧹 Clear API": "/api_clear",
    "🔁 API Mode Testnet": "/api_mode testnet",
    "🔁 Mode Testnet": "/api_mode testnet",
    "🔁 API Mode Mainnet": "/api_mode mainnet",
    "🔁 Mode Mainnet": "/api_mode mainnet",
    "🔄 API Reload": "/api_reload",
    "💸 Fee Status": "/fees",
    "🧾 Set My Fees": "/set_fees 0.1000 0.0360",
    "Maker Mode": "/set_fee_mode maker",
    "Taker Mode": "/set_fee_mode taker",
    "Worst Case Mode": "/set_fee_mode worst_case",
    "⚙️ Runtime Status": "/runtime_status",
    "🧪 Enable Testnet Trading": "/set_trading_enabled true",
    "🤖 Enable Autopilot": "/set_autopilot_enabled true",
    "⏸ Disable Autopilot": "/set_autopilot_enabled false",
    "🐺 Real Status": "/real_status",
    "🧪 Dry Run Status": "/dry_run_status",
    "🔍 Mainnet Check": "/mainnet_check",
    "🧮 Dry Run Order Help": "/dry_run_order help",
    "🔓 Enable Real": "/enable_real",
    "🔒 Disable Real": "/disable_real",
    "🚨 Panic": "/panic",
    "✅ Panic Off": "/panic_off",
    "🔒 Disable Trading": "/disable_trading",
    "🧪 Enable Testnet Trading": "/enable_testnet_trading",
    "🛡 Safety Status": "/safety_status",
    "🤖 Status": "/autopilot_status",
    "▶️ Enable TESTNET Autopilot": "/autopilot_on",
    "⏸ Disable Autopilot": "/autopilot_off",
    "📜 Rules": "/autopilot_rules",
    "📒 Journal": "/autopilot_journal",
    "🧪 Debug Last": "/autopilot_debug_last",
    "🔄 Sync Orders": "/sync_orders",
    "❌ Cancel All": "/cancel_all",
    "🚫 Cancel All TESTNET": "/cancel_all_testnet",
    "❌ Cancel All TESTNET": "/cancel_all_testnet",
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
            f"Autopilot decisions: {storage.database.count_autopilot_decisions()}",
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
            f"• Real unlock min setups: {config.REAL_MODE_UNLOCK_MIN_CLOSED_SETUPS}",
            f"• Real unlock min win rate: {config.REAL_MODE_UNLOCK_MIN_WIN_RATE:g}%",
            f"• Real unlock min Net R: {config.REAL_MODE_UNLOCK_MIN_NET_R:g}R",
            f"• Real requires realistic only: {str(config.REAL_MODE_REQUIRE_REALISTIC_ONLY).lower()}",
            f"• Real dry run: {str(config.REAL_DRY_RUN_ENABLED).lower()}",
            f"• Mainnet read-only: {str(config.MAINNET_READ_ONLY_MODE).lower()}",
            f"• Auto place TP/SL: {str(config.AUTO_PLACE_TP_SL).lower()}",
            "",
            "TESTNET Autopilot:",
            f"• Autopilot default: {str(config.TESTNET_AUTOPILOT_ENABLED).lower()}",
            f"• Require realistic: {str(config.TESTNET_AUTOPILOT_REQUIRE_REALISTIC).lower()}",
            f"• Allow HIGH risk: {str(config.TESTNET_AUTOPILOT_ALLOW_HIGH_RISK).lower()}",
            f"• Allow FAST_MOVE: {str(config.TESTNET_AUTOPILOT_ALLOW_FAST_MOVE).lower()}",
            f"• Allow AMBIGUOUS: {str(config.TESTNET_AUTOPILOT_ALLOW_AMBIGUOUS).lower()}",
            f"• Max orders/day: {config.TESTNET_AUTOPILOT_MAX_ORDERS_PER_DAY}",
            f"• Max active orders: {config.TESTNET_AUTOPILOT_MAX_ACTIVE_ORDERS}",
            f"• Min R/R: {config.TESTNET_AUTOPILOT_MIN_RR:g}",
            "",
            "Order Lifecycle:",
            f"• Order sync: {'ON' if config.ORDER_SYNC_ENABLED else 'OFF'}",
            f"• Sync interval: {config.ORDER_SYNC_INTERVAL_SECONDS} sec",
            f"• Max order lifetime: {config.ORDER_MAX_LIFETIME_MINUTES} min",
            f"• Auto cancel expired: {str(config.AUTO_CANCEL_EXPIRED_ORDERS).lower()}",
            f"• Auto cancel invalidated: {str(config.AUTO_CANCEL_INVALIDATED_BEFORE_FILL).lower()}",
            f"• Auto place TP/SL: {str(config.AUTO_PLACE_TP_SL).lower()}",
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
            "API и real-gate доступны в меню: 📒 Сетапы → 💰 Trading.",
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
    if api_set_pending_for_message(storage, message) and not text.startswith("/"):
        handle_api_set_credentials(message, text, storage, telegram, private_client)
        return

    args: list[str] = []
    if text in BUTTON_COMMANDS:
        command = BUTTON_COMMANDS[text]
        command_parts = command.split()
        command = command_parts[0]
        args = command_parts[1:]
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

    dispatch_command(command, storage, telegram, scanner, private_client, args=args, context=message)


def dispatch_command(
    command: str,
    storage: Storage,
    telegram: TelegramClient,
    scanner: MarketScanner,
    private_client: BybitPrivateClient,
    args: list[str] | None = None,
    context: dict[str, Any] | None = None,
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
        send_with_keyboard(telegram, orders_text(storage, private_client), build_trading_orders_menu_keyboard())
    elif command == "/positions":
        send_with_keyboard(telegram, positions_text(private_client), build_setups_menu_keyboard())
    elif command == "/balance":
        send_with_keyboard(telegram, balance_text(private_client), build_setups_menu_keyboard())
    elif command == "/api_status":
        send_with_keyboard(telegram, api_status_text(storage, private_client), build_setups_trading_menu_keyboard())
    elif command == "/api_test":
        send_with_keyboard(telegram, api_test_text(storage, private_client), build_setups_trading_menu_keyboard())
    elif command == "/api_help":
        send_with_keyboard(telegram, api_help_text(), build_setups_trading_menu_keyboard())
    elif command == "/api_set":
        storage.save_runtime_state("api_set_pending", api_pending_context(context))
        send_with_keyboard(
            telegram,
            api_set_prompt_text(),
            {"inline_keyboard": [[{"text": "❌ Отмена", "callback_data": "api_set_cancel"}]]},
        )
    elif command == "/api_clear":
        send_with_keyboard(telegram, api_clear_text(storage, private_client), build_setups_trading_menu_keyboard())
    elif command == "/api_mode":
        send_with_keyboard(telegram, api_mode_text(storage, private_client, args), build_setups_trading_menu_keyboard())
    elif command == "/api_reload":
        send_with_keyboard(telegram, api_reload_text(storage, private_client), build_setups_trading_menu_keyboard())
    elif command == "/fees":
        send_with_keyboard(telegram, fees_text(storage), build_trading_fees_menu_keyboard())
    elif command == "/set_fees":
        send_with_keyboard(telegram, set_fees_text(storage, args), build_trading_fees_menu_keyboard())
    elif command == "/set_fee_mode":
        send_with_keyboard(telegram, set_fee_mode_text(storage, args), build_trading_fees_menu_keyboard())
    elif command == "/runtime_status":
        send_with_keyboard(telegram, runtime_status_text(storage, private_client), build_trading_runtime_menu_keyboard())
    elif command == "/set_trading_enabled":
        send_with_keyboard(telegram, set_trading_enabled_text(storage, private_client, args), build_trading_runtime_menu_keyboard())
    elif command == "/set_testnet":
        send_with_keyboard(telegram, set_testnet_text(storage, private_client, args), build_trading_runtime_menu_keyboard())
    elif command == "/set_autopilot_enabled":
        send_with_keyboard(telegram, set_autopilot_enabled_text(storage, private_client, args), build_trading_runtime_menu_keyboard())
    elif command == "/set_scan_interval":
        send_with_keyboard(telegram, set_scan_interval_text(storage, args), build_trading_runtime_menu_keyboard())
    elif command == "/set_max_active_orders":
        send_with_keyboard(telegram, set_max_active_orders_text(storage, args), build_trading_runtime_menu_keyboard())
    elif command == "/set_risk_percent":
        send_with_keyboard(telegram, set_risk_percent_text(storage, args), build_trading_runtime_menu_keyboard())
    elif command == "/set_paper_balance":
        send_with_keyboard(telegram, set_paper_balance_text(storage, args), build_trading_runtime_menu_keyboard())
    elif command == "/real_status":
        send_with_keyboard(telegram, real_status_text(storage, private_client), build_setups_trading_menu_keyboard())
    elif command == "/dry_run_status":
        send_with_keyboard(telegram, dry_run_status_text(storage, private_client), build_trading_real_menu_keyboard())
    elif command == "/mainnet_check":
        send_with_keyboard(telegram, mainnet_check_text(storage, private_client), build_trading_real_menu_keyboard())
    elif command == "/dry_run_order":
        send_with_keyboard(telegram, dry_run_order_text(storage, private_client, args), build_trading_real_menu_keyboard())
    elif command == "/enable_real":
        handle_enable_real(storage, telegram, private_client)
    elif command == "/disable_real":
        storage.save_runtime_state("REAL_TRADING_UNLOCKED", False)
        logging.getLogger("RealGate").warning("Real trading disabled by user command")
        send_with_keyboard(telegram, "Real trading disabled.", build_setups_trading_menu_keyboard())
    elif command == "/panic":
        handle_panic(storage, telegram, private_client)
    elif command == "/panic_off":
        handle_panic_off(storage, telegram)
    elif command == "/disable_trading":
        handle_disable_trading(storage, telegram)
    elif command == "/enable_testnet_trading":
        handle_enable_testnet_trading(storage, telegram, private_client)
    elif command == "/safety_status":
        send_with_keyboard(telegram, safety_status_text(storage, private_client), build_trading_emergency_menu_keyboard())
    elif command == "/autopilot_status":
        send_with_keyboard(telegram, autopilot_status_text(storage, private_client), build_trading_autopilot_menu_keyboard())
    elif command == "/autopilot_on":
        handle_autopilot_on(storage, telegram, private_client)
    elif command == "/autopilot_off":
        handle_autopilot_off(storage, telegram)
    elif command == "/autopilot_rules":
        send_with_keyboard(telegram, autopilot_rules_text(), build_trading_autopilot_menu_keyboard())
    elif command == "/autopilot_journal":
        send_with_keyboard(telegram, autopilot_journal_text(storage), build_trading_autopilot_menu_keyboard())
    elif command == "/autopilot_debug_last":
        send_with_keyboard(telegram, autopilot_debug_last_text(storage, private_client), build_trading_autopilot_menu_keyboard())
    elif command == "/cancel_order":
        send_with_keyboard(telegram, cancel_order_text(storage, private_client, args), build_setups_trading_menu_keyboard())
    elif command == "/cancel_all_testnet":
        handle_cancel_all_testnet_request(storage, telegram, private_client)
    elif command == "/cancel_all":
        handle_cancel_all_request(storage, telegram, private_client)
    elif command == "/order_status":
        target = args[0] if args else ""
        if not target:
            send_with_keyboard(telegram, "Использование: /order_status ORDER_ID", build_trading_orders_menu_keyboard())
        else:
            send_with_keyboard(
                telegram,
                format_order_status(storage, private_client, target),
                build_trading_orders_menu_keyboard(),
            )
    elif command == "/sync_orders":
        summary = sync_testnet_orders(private_client, storage, telegram=telegram, notify=False)
        send_with_keyboard(telegram, format_order_sync_summary(summary), build_trading_orders_menu_keyboard())
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
    elif data == "menu:trading_orders":
        edit_menu_message(telegram, message, "📋 Ордера", build_trading_orders_menu_keyboard())
    elif data == "menu:trading_api":
        edit_menu_message(telegram, message, "🔑 API", build_trading_api_menu_keyboard())
    elif data == "menu:trading_risk":
        edit_menu_message(telegram, message, "🛡 Risk", build_trading_risk_menu_keyboard())
    elif data == "menu:trading_fees":
        edit_menu_message(telegram, message, "💸 Fees", build_trading_fees_menu_keyboard())
    elif data == "menu:trading_runtime":
        edit_menu_message(telegram, message, "⚙️ Runtime", build_trading_runtime_menu_keyboard())
    elif data == "menu:trading_emergency":
        edit_menu_message(telegram, message, "🚨 Emergency", build_trading_emergency_menu_keyboard())
    elif data == "menu:trading_autopilot":
        edit_menu_message(telegram, message, "🤖 Autopilot", build_trading_autopilot_menu_keyboard())
    elif data == "menu:trading_real":
        edit_menu_message(telegram, message, "🐺 Real Gate", build_trading_real_menu_keyboard())
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
        raw_command = data.split(":", 1)[1]
        parts = raw_command.split()
        command = parts[0]
        dispatch_command(command, storage, telegram, scanner, private_client, args=parts[1:], context=callback_query)
    elif data.startswith("order_calc:"):
        alert_id = data.split(":", 1)[1]
        calculate_order_plan(alert_id, storage, telegram, private_client)
    elif data.startswith("order_submit:"):
        plan_id = data.split(":", 1)[1]
        submit_order_plan(plan_id, storage, telegram, private_client)
    elif data.startswith("order_testnet_confirm:"):
        plan_id = data.split(":", 1)[1]
        submit_testnet_order_after_confirmation(plan_id, storage, telegram, private_client)
    elif data.startswith("order_real_confirm:"):
        plan_id = data.split(":", 1)[1]
        submit_real_order_after_confirmation(plan_id, storage, telegram, private_client)
    elif data.startswith("order_cancel:"):
        plan_id = data.split(":", 1)[1]
        storage.update_paper_order(plan_id, {"status": "CANCELLED"})
        send_with_keyboard(telegram, "❌ План ордера отменён.", build_main_menu_keyboard())
    elif data == "real_unlock_step1":
        handle_real_unlock_step1(storage, telegram, private_client)
    elif data == "real_unlock_step2":
        handle_real_unlock_step2(storage, telegram, private_client)
    elif data == "real_unlock_cancel":
        send_with_keyboard(telegram, "❌ REAL unlock отменён.", build_setups_trading_menu_keyboard())
    elif data == "cancel_all_testnet_confirm":
        send_with_keyboard(telegram, cancel_all_testnet_text(storage, private_client), build_trading_emergency_menu_keyboard())
    elif data == "cancel_all_testnet_cancel":
        send_with_keyboard(telegram, "❌ Отмена отмены TESTNET ордеров.", build_trading_emergency_menu_keyboard())
    elif data == "cancel_all_mainnet_confirm":
        send_with_keyboard(
            telegram,
            "⚠️ MAINNET cancel-all не реализован в этой фазе. Ордера не отменялись.",
            build_trading_emergency_menu_keyboard(),
        )
    elif data == "cancel_all_mainnet_cancel":
        send_with_keyboard(telegram, "❌ MAINNET cancel-all отменён.", build_trading_emergency_menu_keyboard())
    elif data == "api_set_cancel":
        storage.save_runtime_state("api_set_pending", False)
        edit_menu_message(telegram, message, "API setup cancelled.", build_trading_api_menu_keyboard())
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


REAL_LOCK_PHRASE = "пошел нахуй, ждем 80%+"
TESTNET_API_NOT_READY_TEXT = (
    "❌ TESTNET order rejected: API not ready. Paper fallback disabled because trading is enabled."
)


def api_status_text(storage: Storage, private_client: BybitPrivateClient) -> str:
    logging.getLogger("APIControl").info("API status checked")
    private_client.load_credentials(storage)
    checks = run_private_api_checks(private_client)
    real_unlocked = real_trading_unlocked(storage)
    effective_real = effective_real_trading(storage, private_client)
    connection_ok = checks["balance_ok"] or checks["positions_ok"] or checks["orders_ok"]
    paper_mode = not config.BYBIT_TRADING_ENABLED
    place_order_status = "disabled"
    if config.BYBIT_TRADING_ENABLED and private_client.has_api_keys:
        place_order_status = "✅" if (private_client.testnet or real_unlocked) and connection_ok else "❌"
        if not private_client.testnet and config.MAINNET_READ_ONLY_MODE:
            place_order_status = "LOCKED"
    mainnet_locked = not private_client.testnet and not real_unlocked
    dry_run_on = dry_run_enabled(storage)

    lines = [
        "🔑 Bybit API Status",
        "",
        f"Credential source: {private_client.credential_source}",
        f"API key: {_found_label(bool(private_client.api_key))}",
        f"API key masked: {private_client.masked_key or 'n/a'}",
        f"API secret: {_found_label(bool(private_client.api_secret))}",
        f"Mode: {private_client.market_mode_label()}",
        f"Trading enabled env: {str(config.BYBIT_TRADING_ENABLED).lower()}",
        f"Real trading unlocked: {str(real_unlocked).lower()}",
        f"Effective real trading: {str(effective_real).lower()}",
        "",
        f"Connection: {'OK' if connection_ok else 'ERROR'}",
        f"Balance: {'OK' if checks['balance_ok'] else 'ERROR'}",
        f"Positions: {'OK' if checks['positions_ok'] else 'ERROR'}",
        f"Orders: {'OK' if checks['orders_ok'] else 'ERROR'}",
        "",
        "Permissions:",
        f"Read balance: {_ok_label(checks['balance_ok'])}",
        f"Read positions: {_ok_label(checks['positions_ok'])}",
        f"Read orders: {_ok_label(checks['orders_ok'])}",
        f"Place order: {place_order_status}",
        "",
        "Safety:",
        f"Real orders: {'ENABLED' if effective_real else 'LOCKED'}",
        f"Paper mode: {'ON' if paper_mode else 'OFF'}",
    ]
    if mainnet_locked:
        lines.extend(
            [
                "",
                "Mainnet Dry Run:",
                f"Mainnet read: {'OK' if connection_ok else 'ERROR'}",
                "Real order placement: LOCKED",
                "Effective real trading: false",
                f"Dry run: {'ON' if dry_run_on else 'OFF'}",
            ]
        )

    if private_client.credential_source == "TELEGRAM_DB":
        lines.extend(
            [
                "",
                "⚠️ API secret хранится локально в базе. Это быстрый режим, не максимальная безопасность.",
            ]
        )
        if config.HOSTING_MODE:
            lines.append(
                "⚠️ TELEGRAM_DB credentials may disappear after Render redeploy. Use Render Environment for stable hosting."
            )

    return "\n".join(lines)


def api_test_text(storage: Storage, private_client: BybitPrivateClient) -> str:
    private_client.load_credentials(storage)
    checks = run_private_api_checks(private_client)
    logging.getLogger("APIControl").info(
        "API test %s",
        "success" if checks["balance_ok"] and checks["positions_ok"] and checks["orders_ok"] else "failure",
    )
    lines = [
        "🧪 Bybit API Test",
        "",
        f"Balance: {'OK' if checks['balance_ok'] else 'ERROR'}",
        f"Positions: {'OK' if checks['positions_ok'] else 'ERROR'}",
        f"Orders: {'OK' if checks['orders_ok'] else 'ERROR'}",
        f"Mode: {private_client.market_mode_label()}",
        f"Trading enabled env: {str(config.BYBIT_TRADING_ENABLED).lower()}",
        f"Real trading unlocked: {str(real_trading_unlocked(storage)).lower()}",
    ]
    errors = [sanitize_error(value) for value in checks["errors"] if value]
    if errors:
        lines.extend(["", "Errors:"])
        lines.extend(f"- {error}" for error in errors[:3])
    if config.BYBIT_TRADING_ENABLED and private_client.testnet:
        lines.extend(["", "Order validation: no order placed during /api_test."])
    return "\n".join(lines)


def mainnet_check_text(storage: Storage, private_client: BybitPrivateClient) -> str:
    logging.getLogger("DryRun").info("Mainnet dry-run check requested")
    private_client.load_credentials(storage)
    checks = run_private_api_checks(private_client)
    read_ok = checks["balance_ok"] and checks["positions_ok"] and checks["orders_ok"]
    trade_permission = trade_permission_label(storage, private_client)
    verdict = "READ-ONLY OK" if (not private_client.testnet and read_ok and dry_run_enabled(storage)) else "NOT READY"
    lines = [
        "🧪 Mainnet Dry Run Check",
        "",
        f"Mode: {private_client.market_mode_label()}",
        f"Real trading: {'ENABLED' if effective_real_trading(storage, private_client) else 'LOCKED'}",
        f"Read balance: {'OK' if checks['balance_ok'] else 'ERROR'}",
        f"Read positions: {'OK' if checks['positions_ok'] else 'ERROR'}",
        f"Read orders: {'OK' if checks['orders_ok'] else 'ERROR'}",
        f"Trade permission: {trade_permission}",
        f"Effective real trading: {str(effective_real_trading(storage, private_client)).lower()}",
        "",
        "Verdict:",
        verdict,
    ]
    errors = [sanitize_error(value) for value in checks["errors"] if value]
    if errors:
        lines.extend(["", "Errors:"])
        lines.extend(f"- {error}" for error in errors[:3])
    return "\n".join(lines)


def dry_run_status_text(storage: Storage, private_client: BybitPrivateClient) -> str:
    checks = run_private_api_checks(private_client) if private_client.can_call_private else {
        "balance_ok": False,
        "positions_ok": False,
        "orders_ok": False,
        "errors": ["Bybit API keys не настроены."],
    }
    api_ok = checks["balance_ok"] and checks["positions_ok"] and checks["orders_ok"]
    block_reasons = order_safety_block_reasons(storage, private_client, include_real_gate=True)
    return "\n".join(
        [
            "🧪 Dry Run Status",
            "",
            f"Dry run enabled: {str(dry_run_enabled(storage)).lower()}",
            f"Mainnet read-only mode: {str(mainnet_read_only_mode(storage)).lower()}",
            f"Real locked: {str(not real_trading_unlocked(storage)).lower()}",
            f"Env trading enabled: {str(config.BYBIT_TRADING_ENABLED).lower()}",
            f"Mode: {safety_mode_label(private_client)}",
            f"Panic mode: {'ON' if storage.get_runtime_state('PANIC_MODE', False) else 'OFF'}",
            f"Risk status: {'blocked' if block_reasons else 'ok'}",
            f"Block reason: {block_reasons[0] if block_reasons else 'none'}",
            f"API status: {'OK' if api_ok else 'ERROR'}",
        ]
    )


def dry_run_order_text(
    storage: Storage,
    private_client: BybitPrivateClient,
    args: list[str],
) -> str:
    if not args or args[0].lower() == "help" or len(args) != 6:
        return "\n".join(
            [
                "Использование: /dry_run_order SYMBOL SIDE ENTRY STOP TP1 TP2",
                "Пример: /dry_run_order BTCUSDT LONG 100000 99000 101000 102000",
                "",
                "Это только расчёт. Ордер НЕ отправляется.",
            ]
        )

    symbol = args[0].upper()
    side = args[1].upper()
    entry = _to_float(args[2])
    stop = _to_float(args[3])
    tp1 = _to_float(args[4])
    tp2 = _to_float(args[5])
    reject_reasons: list[str] = []
    if side not in {"LONG", "SHORT"}:
        reject_reasons.append("side must be LONG or SHORT")
    if None in {entry, stop, tp1, tp2}:
        reject_reasons.append("price parse error")

    balance = config.PAPER_ACCOUNT_BALANCE_USDT
    balance_source = "fallback"
    if private_client.can_call_private:
        try:
            balance = extract_usdt_balance(private_client.get_account_balance())
            balance_source = "mainnet/testnet API"
        except Exception as exc:
            reject_reasons.append(f"balance read failed: {sanitize_error(exc)}")

    instrument_info = None
    try:
        instrument_info = private_client.get_instrument_info(symbol)
    except Exception as exc:
        reject_reasons.append(f"instrument info unavailable: {sanitize_error(exc)}")

    qty = 0.0
    position_size_usdt = 0.0
    risk_usdt = balance * (config.ACCOUNT_RISK_PERCENT / 100)
    rr_tp1 = 0.0
    rr_tp2 = 0.0
    max_loss_usdt = 0.0
    tp1_profit_usdt = 0.0
    tp2_profit_usdt = 0.0
    stop_distance_percent = 0.0

    if side in {"LONG", "SHORT"} and None not in {entry, stop, tp1, tp2} and entry and stop and tp1 and tp2:
        if side == "LONG" and stop >= entry:
            reject_reasons.append("LONG stop must be below entry")
        if side == "SHORT" and stop <= entry:
            reject_reasons.append("SHORT stop must be above entry")
        stop_distance = abs(entry - stop)
        stop_distance_percent = stop_distance / entry * 100 if entry > 0 else 0
        if stop_distance <= 0 or stop_distance_percent <= 0:
            reject_reasons.append("stop distance <= 0")
        if stop_distance > 0 and stop_distance_percent > 0:
            position_size_usdt = risk_usdt / (stop_distance_percent / 100)
            if position_size_usdt > config.MAX_POSITION_USDT:
                reject_reasons.append("position size выше MAX_POSITION_USDT")
            if position_size_usdt < config.MIN_POSITION_USDT:
                reject_reasons.append("position size ниже MIN_POSITION_USDT")
            qty = _round_dry_run_qty(position_size_usdt / entry, instrument_info)
            min_qty = _dry_run_min_qty(instrument_info)
            if qty <= 0:
                reject_reasons.append("qty <= 0")
            if min_qty is not None and qty < min_qty:
                reject_reasons.append(f"qty меньше minOrderQty ({min_qty:g})")
            rr_tp1 = abs(tp1 - entry) / stop_distance
            rr_tp2 = abs(tp2 - entry) / stop_distance
            if rr_tp1 < config.MIN_RR_TO_ALLOW_ORDER:
                reject_reasons.append("R/R ниже минимального порога")
            position_size_usdt = qty * entry
            fee = position_size_usdt * _runtime_fee_rate() * 2
            slippage = position_size_usdt * (config.SLIPPAGE_PERCENT / 100)
            max_loss_usdt = risk_usdt + fee + slippage
            tp1_profit_usdt = _dry_run_profit_usdt(side, entry, tp1, position_size_usdt) - fee - slippage
            tp2_profit_usdt = _dry_run_profit_usdt(side, entry, tp2, position_size_usdt) - fee - slippage

    if not private_client.testnet and real_trading_unlocked(storage):
        reject_reasons.append("real unlocked flag exists, but dry run never submits orders")

    lines = [
        "🧪 DRY RUN REAL ORDER",
        "",
        "This order was NOT sent.",
        "",
        f"Symbol: {symbol}",
        f"Side: {side}",
        f"Entry: {_dry_fmt(entry)}",
        f"Stop: {_dry_fmt(stop)}",
        f"TP1: {_dry_fmt(tp1)}",
        f"TP2: {_dry_fmt(tp2)}",
        f"Qty: {_dry_fmt(qty)}",
        f"Position size: {position_size_usdt:.2f} USDT",
        f"Risk: {config.ACCOUNT_RISK_PERCENT:g}% = {risk_usdt:.2f} USDT",
        f"Balance source: {balance_source}",
        f"Stop distance: {stop_distance_percent:.2f}%",
        f"R/R: TP1 {rr_tp1:.2f}R | TP2 {rr_tp2:.2f}R",
        f"Max loss estimate: {max_loss_usdt:.4f} USDT",
        f"TP1 estimate: {tp1_profit_usdt:.4f} USDT",
        f"TP2 estimate: {tp2_profit_usdt:.4f} USDT",
        "",
        "Reject reasons:",
    ]
    lines.extend(f"- {reason}" for reason in reject_reasons)
    if not reject_reasons:
        lines.append("- none; dry run only")
    lines.extend(
        [
            "",
            "Real order placement: LOCKED",
            "No POST order endpoint was called.",
        ]
    )
    return "\n".join(lines)


def trade_permission_label(storage: Storage, private_client: BybitPrivateClient) -> str:
    if mainnet_read_only_mode(storage) or not config.BYBIT_TRADING_ENABLED or not real_trading_unlocked(storage):
        return "disabled"
    if private_client.testnet:
        return "testnet only"
    return "unknown"


def _runtime_fee_rate() -> float:
    mode = str(getattr(config, "FEE_MODE", config.DEFAULT_EXECUTION_FEE_MODE) or "maker").lower()
    if mode == "maker":
        return config.BYBIT_MAKER_FEE_RATE
    if mode == "worst_case":
        return max(config.BYBIT_MAKER_FEE_RATE, config.BYBIT_TAKER_FEE_RATE)
    return config.BYBIT_TAKER_FEE_RATE


def api_help_text() -> str:
    return "\n".join(
        [
            "📘 Как подключить Bybit API",
            "",
            "Stable hosting method:",
            "Render Environment:",
            "BYBIT_API_KEY",
            "BYBIT_API_SECRET",
            "BYBIT_TESTNET=true",
            "BYBIT_TRADING_ENABLED=true",
            "",
            "Для Render:",
            "1. Render → bobabot → Environment",
            "2. Add:",
            "BYBIT_API_KEY=...",
            "BYBIT_API_SECRET=...",
            "BYBIT_TESTNET=true",
            "BYBIT_TRADING_ENABLED=true",
            "3. Save",
            "4. Manual Deploy → Clear build cache & deploy",
            "",
            "Для Mac:",
            'export BYBIT_API_KEY="..."',
            'export BYBIT_API_SECRET="..."',
            "export BYBIT_TESTNET=true",
            "export BYBIT_TRADING_ENABLED=true",
            "python3 main.py",
            "",
            "Важно:",
            "- сначала только TESTNET",
            "- для paper mode оставь BYBIT_TRADING_ENABLED=false",
            "- не отправляй API secret в обычный чат",
            "- real trading включать только после paper/testnet проверки",
            "- real trading всё равно будет заблокирован, пока не пройдена статистика",
            "",
            "Быстрый способ через Telegram:",
            "/api_set",
            "или кнопка:",
            "🔑 API → 🔐 Внести API ключи",
            "потом отправить:",
            "API_KEY API_SECRET",
            "",
            "Удалить ключи:",
            "/api_clear",
            "",
            "Переключить режим:",
            "/api_mode testnet",
            "/api_mode mainnet",
            "",
            "Важно:",
            "- сначала testnet",
            "- mainnet не включает real trading автоматически",
            "- real trading всё равно заблокирован до 80%+ и 100 закрытых сетапов",
        ]
    )


def api_set_prompt_text() -> str:
    return "\n".join(
        [
            "🔑 Быстрое подключение Bybit API",
            "",
            "Отправь ключи одним сообщением в формате:",
            "",
            "API_KEY API_SECRET",
            "",
            "Пример:",
            "abcd123456 secret987654",
            "",
            "Можно и старым форматом:",
            "API_KEY|API_SECRET",
            "",
            "Сообщение с ключами бот попробует удалить.",
            "Secret не будет показан обратно.",
            "",
            "⚠️ Используй сначала TESTNET ключи.",
            "⚠️ Не кидай сюда mainnet key с доступом к выводу средств.",
        ]
    )


def handle_api_set_credentials(
    message: dict[str, Any],
    text: str,
    storage: Storage,
    telegram: TelegramClient,
    private_client: BybitPrivateClient,
) -> None:
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if chat_id is not None and message_id is not None:
        try:
            telegram.delete_message(chat_id, message_id)
        except Exception as exc:
            logging.getLogger("APIControl").warning("Could not delete API credential message: %s", exc)
    storage.save_runtime_state("api_set_pending", False)

    parsed = parse_api_credentials(text)
    if parsed is None:
        send_with_keyboard(telegram, "Формат не распознан. Используй API_KEY API_SECRET.", build_trading_api_menu_keyboard())
        return
    api_key, api_secret = parsed
    masked = mask_secret(api_key)
    mode = "TESTNET" if private_client.testnet else "MAINNET"
    storage.save_api_credentials(api_key, api_secret, masked, mode=mode)
    private_client.load_credentials(storage)
    logging.getLogger("APIControl").info("API credentials saved from Telegram source=%s key=%s", private_client.credential_source, masked)
    send_with_keyboard(
        telegram,
        "\n".join(
            [
                "✅ API ключи приняты",
                "",
                f"API key: {masked}",
                "Secret: сохранён скрыто",
                f"Mode: {private_client.market_mode_label()}",
                f"Trading enabled env: {str(config.BYBIT_TRADING_ENABLED).lower()}",
                f"Real trading unlocked: {str(real_trading_unlocked(storage)).lower()}",
                "",
                "Запускаю API test...",
            ]
        ),
        build_trading_api_menu_keyboard(),
    )
    send_with_keyboard(telegram, api_test_text(storage, private_client), build_trading_api_menu_keyboard())
    checks = run_private_api_checks(private_client)
    if checks["balance_ok"] and checks["positions_ok"] and checks["orders_ok"]:
        send_with_keyboard(
            telegram,
            "API готов. Можно включить TESTNET trading через /set_trading_enabled true",
            build_trading_api_menu_keyboard(),
        )


def api_clear_text(storage: Storage, private_client: BybitPrivateClient) -> str:
    storage.clear_api_credentials()
    private_client.load_credentials(storage)
    logging.getLogger("APIControl").info("Telegram-stored API credentials cleared")
    lines = ["🧹 Telegram-stored API credentials cleared."]
    if config.BYBIT_API_KEY and config.BYBIT_API_SECRET:
        lines.append("ENV keys still active.")
    return "\n".join(lines)


def api_mode_text(
    storage: Storage,
    private_client: BybitPrivateClient,
    args: list[str],
) -> str:
    if not args or args[0].lower() not in {"testnet", "mainnet"}:
        return "Использование: /api_mode testnet или /api_mode mainnet"
    mode = "MAINNET" if args[0].lower() == "mainnet" else "TESTNET"
    storage.save_runtime_state("BYBIT_TESTNET", mode == "TESTNET")
    storage.save_runtime_state("REAL_TRADING_UNLOCKED", False)
    if private_client.credential_source == "TELEGRAM_DB":
        storage.update_active_api_mode(mode)
        private_client.load_credentials(storage)
    else:
        private_client.set_mode(mode)
        storage.save_runtime_state("api_runtime_mode", mode)
    logging.getLogger("APIControl").info("API mode set to %s source=%s", mode, private_client.credential_source)
    if mode == "TESTNET":
        return "✅ API mode set to TESTNET"
    return "⚠️ API mode set to MAINNET.\nReal trading is still LOCKED."


def parse_api_credentials(text: str) -> tuple[str, str] | None:
    cleaned = str(text or "").strip()
    if not cleaned:
        return None

    if "|" in cleaned:
        parts = [part.strip() for part in cleaned.split("|")]
    else:
        parts = cleaned.split()

    if len(parts) != 2:
        return None
    api_key, api_secret = parts
    if len(api_key) < 8 or len(api_secret) < 8:
        return None
    if any(char.isspace() for char in api_key + api_secret):
        return None
    return api_key, api_secret


def api_pending_context(context: dict[str, Any] | None) -> dict[str, Any]:
    context = context or {}
    message = context.get("message") or context
    chat = message.get("chat") or {}
    user = context.get("from") or message.get("from") or {}
    return {
        "chat_id": chat.get("id") or config.TELEGRAM_CHAT_ID,
        "user_id": user.get("id"),
    }


def api_set_pending_for_message(storage: Storage, message: dict[str, Any]) -> bool:
    pending = storage.state.get("api_set_pending")
    if not pending:
        return False
    if pending is True:
        return True
    if not isinstance(pending, dict):
        return False
    chat_id = (message.get("chat") or {}).get("id")
    user_id = (message.get("from") or {}).get("id")
    expected_chat = pending.get("chat_id")
    expected_user = pending.get("user_id")
    if expected_chat is not None and str(expected_chat) != str(chat_id):
        return False
    if expected_user is not None and str(expected_user) != str(user_id):
        return False
    return True


def api_reload_text(storage: Storage, private_client: BybitPrivateClient) -> str:
    private_client.reload_from_environment()
    storage.apply_runtime_config_overrides()
    private_client.load_credentials(storage)
    logging.getLogger("APIControl").info("API config reload requested")
    return "\n".join(
        [
            "🔄 API config reload выполнен из текущих environment variables.",
            f"Mode: {private_client.mode_label()}",
            f"Credential source: {private_client.credential_source}",
            f"API key: {_found_label(bool(private_client.api_key))}",
            "",
            "Для применения Render Environment нужен redeploy.",
        ]
    )


def fees_text(storage: Storage) -> str:
    settings = storage.get_fee_settings()
    return "\n".join(
        [
            "💸 Fee Profile",
            "",
            "Derivatives:",
            f"Taker: {float(settings['derivatives_taker_fee_percent']):.4f}%",
            f"Maker: {float(settings['derivatives_maker_fee_percent']):.4f}%",
            "",
            "Spot:",
            f"Taker: {float(settings['spot_taker_fee_percent']):.4f}%",
            f"Maker: {float(settings['spot_maker_fee_percent']):.4f}%",
            "",
            f"Current calculation mode: {settings['fee_mode']}",
            "",
            "Used in:",
            "- order plan",
            "- estimated net R",
            "- analytics",
            "- backtest",
            "- paper/testnet results",
        ]
    )


def set_fees_text(storage: Storage, args: list[str]) -> str:
    if len(args) != 2:
        return "Использование: /set_fees DERIV_TAKER DERIV_MAKER\nПример: /set_fees 0.1000 0.0360"
    taker = _to_float(args[0])
    maker = _to_float(args[1])
    if taker is None or maker is None or taker < 0 or maker < 0 or taker > 2 or maker > 2:
        return "❌ Fee values rejected. Укажи проценты от 0 до 2."
    storage.save_fee_settings(
        {
            "derivatives_taker_fee_percent": taker,
            "derivatives_maker_fee_percent": maker,
        }
    )
    logging.getLogger("RuntimeControl").info("Fee profile updated taker=%.4f maker=%.4f", taker, maker)
    return fees_text(storage)


def set_fee_mode_text(storage: Storage, args: list[str]) -> str:
    if not args or args[0].lower() not in {"maker", "taker", "worst_case"}:
        return "Использование: /set_fee_mode maker|taker|worst_case"
    mode = args[0].lower()
    storage.save_fee_settings({"fee_mode": mode})
    logging.getLogger("RuntimeControl").info("Fee mode updated mode=%s", mode)
    return fees_text(storage)


def runtime_status_text(storage: Storage, private_client: BybitPrivateClient) -> str:
    private_client.load_credentials(storage)
    checks = run_private_api_checks(private_client) if private_client.can_call_private else {
        "balance_ok": False,
        "positions_ok": False,
        "orders_ok": False,
        "errors": ["Bybit API keys не настроены."],
    }
    api_ok = checks["balance_ok"] and checks["positions_ok"] and checks["orders_ok"]
    return "\n".join(
        [
            "⚙️ Runtime Status",
            "",
            f"Mode: {private_client.market_mode_label()}",
            f"Trading enabled: {str(config.BYBIT_TRADING_ENABLED).lower()}",
            f"Source: {storage.runtime_setting_source('BYBIT_TRADING_ENABLED')}",
            f"Autopilot enabled: {str(storage.get_runtime_state('TESTNET_AUTOPILOT_ENABLED', config.TESTNET_AUTOPILOT_ENABLED)).lower()}",
            f"Scan interval: {config.SCAN_INTERVAL_SECONDS}",
            f"Risk per trade: {config.ACCOUNT_RISK_PERCENT:g}%",
            f"Paper balance: {config.PAPER_ACCOUNT_BALANCE_USDT:g} USDT",
            f"Real trading unlocked: {str(real_trading_unlocked(storage)).lower()}",
            f"Effective real trading: {str(effective_real_trading(storage, private_client)).lower()}",
            f"API status: {'OK' if api_ok else 'ERROR'}",
        ]
    )


def set_trading_enabled_text(
    storage: Storage,
    private_client: BybitPrivateClient,
    args: list[str],
) -> str:
    value = _parse_bool_arg(args)
    if value is None:
        return "Использование: /set_trading_enabled true|false"
    if value and not private_client.testnet:
        if not real_trading_unlocked(storage):
            return REAL_LOCK_PHRASE
        return "MAINNET trading нельзя включить через runtime toggle. Используй Real Gate flow."
    storage.save_runtime_state("BYBIT_TRADING_ENABLED", value)
    storage.save_runtime_state("REAL_TRADING_UNLOCKED", False)
    private_client.load_credentials(storage)
    logging.getLogger("RuntimeControl").warning("Runtime BYBIT_TRADING_ENABLED set to %s", value)
    if value and private_client.testnet:
        return "🧪 TESTNET trading enabled.\nReal trading remains locked."
    if not value:
        return "🔒 Trading disabled. Paper mode allowed."
    return "Trading enabled."


def set_testnet_text(
    storage: Storage,
    private_client: BybitPrivateClient,
    args: list[str],
) -> str:
    value = _parse_bool_arg(args)
    if value is None:
        return "Использование: /set_testnet true|false"
    storage.save_runtime_state("BYBIT_TESTNET", value)
    mode = "TESTNET" if value else "MAINNET"
    if private_client.credential_source == "TELEGRAM_DB":
        storage.update_active_api_mode(mode)
        private_client.load_credentials(storage)
    else:
        private_client.set_mode(mode)
    storage.save_runtime_state("REAL_TRADING_UNLOCKED", False)
    logging.getLogger("RuntimeControl").warning("Runtime BYBIT_TESTNET set to %s", value)
    if value:
        return "✅ Runtime mode set to TESTNET"
    return "⚠️ Runtime mode set to MAINNET.\nReal trading is still LOCKED."


def set_autopilot_enabled_text(
    storage: Storage,
    private_client: BybitPrivateClient,
    args: list[str],
) -> str:
    value = _parse_bool_arg(args)
    if value is None:
        return "Использование: /set_autopilot_enabled true|false"
    if not value:
        storage.save_runtime_state("TESTNET_AUTOPILOT_ENABLED", False)
        return "⏸ TESTNET Autopilot disabled."
    if not private_client.testnet:
        return "TESTNET Autopilot works only in TESTNET mode."
    if not config.BYBIT_TRADING_ENABLED:
        return "❌ TESTNET Autopilot не включён: сначала /set_trading_enabled true."
    checks = run_private_api_checks(private_client)
    if not (checks["balance_ok"] and checks["positions_ok"] and checks["orders_ok"]):
        return "❌ TESTNET Autopilot не включён: API test failed."
    storage.save_runtime_state("TESTNET_AUTOPILOT_ENABLED", True)
    storage.save_runtime_state("REAL_TRADING_UNLOCKED", False)
    logging.getLogger("RuntimeControl").warning("Runtime TESTNET_AUTOPILOT_ENABLED set to true")
    return "🧪 TESTNET Autopilot enabled.\nReal trading remains locked."


def set_scan_interval_text(storage: Storage, args: list[str]) -> str:
    value = _parse_int_arg(args)
    if value is None or value < 10 or value > 3600:
        return "Использование: /set_scan_interval SECONDS\nДиапазон: 10..3600"
    storage.save_runtime_state("SCAN_INTERVAL_SECONDS", value)
    return f"✅ Scan interval set to {value} sec."


def set_max_active_orders_text(storage: Storage, args: list[str]) -> str:
    value = _parse_int_arg(args)
    if value is None or value < 1 or value > 20:
        return "Использование: /set_max_active_orders NUMBER\nДиапазон: 1..20"
    storage.save_runtime_state("TESTNET_AUTOPILOT_MAX_ACTIVE_ORDERS", value)
    return f"✅ Max active TESTNET autopilot orders set to {value}."


def set_risk_percent_text(storage: Storage, args: list[str]) -> str:
    value = _parse_float_arg(args)
    if value is None or value <= 0 or value > 5:
        return "Использование: /set_risk_percent NUMBER\nДиапазон: 0.01..5"
    storage.save_runtime_state("ACCOUNT_RISK_PERCENT", value)
    return f"✅ Risk per trade set to {value:g}%."


def set_paper_balance_text(storage: Storage, args: list[str]) -> str:
    value = _parse_float_arg(args)
    if value is None or value <= 0 or value > 1_000_000:
        return "Использование: /set_paper_balance NUMBER"
    storage.save_runtime_state("PAPER_ACCOUNT_BALANCE_USDT", value)
    return f"✅ Paper balance set to {value:g} USDT."


def _parse_bool_arg(args: list[str]) -> bool | None:
    if len(args) != 1:
        return None
    value = args[0].strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


def _parse_int_arg(args: list[str]) -> int | None:
    if len(args) != 1:
        return None
    try:
        return int(args[0])
    except ValueError:
        return None


def _parse_float_arg(args: list[str]) -> float | None:
    if len(args) != 1:
        return None
    return _to_float(args[0])


def real_status_text(storage: Storage, private_client: BybitPrivateClient) -> str:
    stats = real_gate_stats(storage)
    unlocked = real_trading_unlocked(storage)
    verdict = "Real mode can be unlocked." if stats["requirements_met"] else REAL_LOCK_PHRASE
    return "\n".join(
        [
            "🐺 Real Mode Status",
            "",
            f"Real trading: {'ENABLED' if effective_real_trading(storage, private_client) else 'LOCKED'}",
            f"Closed counted setups: {stats['counted_closed']} / {config.REAL_MODE_UNLOCK_MIN_CLOSED_SETUPS}",
            f"Win rate: {stats['win_rate']:.1f}% / {config.REAL_MODE_UNLOCK_MIN_WIN_RATE:g}%",
            f"Net R: {stats['net_r']:+.1f}R / +{config.REAL_MODE_UNLOCK_MIN_NET_R:g}R",
            f"FAST_MOVE excluded: {'yes' if not config.REAL_MODE_COUNT_FAST_MOVE else 'no'}",
            f"Mode: {private_client.mode_label()}",
            f"Real trading unlocked flag: {str(unlocked).lower()}",
            "",
            "Verdict:",
            verdict,
        ]
    )


def handle_enable_real(
    storage: Storage,
    telegram: TelegramClient,
    private_client: BybitPrivateClient,
) -> None:
    logger = logging.getLogger("RealGate")
    logger.warning("Real unlock attempted")
    stats = real_gate_stats(storage)
    if not stats["requirements_met"]:
        logger.warning("Real unlock rejected: performance requirements not met")
        telegram.send_message(REAL_LOCK_PHRASE)
        send_with_keyboard(
            telegram,
            "\n".join(
                [
                    f"Closed setups: {stats['counted_closed']} / {config.REAL_MODE_UNLOCK_MIN_CLOSED_SETUPS}",
                    f"Win rate: {stats['win_rate']:.1f}%",
                    f"Net R: {stats['net_r']:+.2f}R",
                ]
            ),
            build_setups_trading_menu_keyboard(),
        )
        return

    checks = run_private_api_checks(private_client)
    if not (checks["balance_ok"] and checks["positions_ok"] and checks["orders_ok"]):
        logger.warning("Real unlock rejected: API status failed")
        send_with_keyboard(telegram, REAL_LOCK_PHRASE, build_setups_trading_menu_keyboard())
        return
    if storage.get_runtime_state("order_lifecycle_unstable", False):
        logger.warning("Real unlock rejected: order lifecycle unstable")
        send_with_keyboard(telegram, REAL_LOCK_PHRASE, build_setups_trading_menu_keyboard())
        return
    safety_reasons = order_safety_block_reasons(storage, private_client, include_real_gate=False)
    if safety_reasons:
        logger.warning("Real unlock rejected: safety control %s", "; ".join(safety_reasons))
        send_with_keyboard(telegram, REAL_LOCK_PHRASE, build_setups_trading_menu_keyboard())
        return

    send_with_keyboard(
        telegram,
        "\n".join(
            [
                "⚠️ REAL MODE UNLOCK AVAILABLE",
                "",
                "Статистика прошла фильтр:",
                f"Closed setups: {stats['counted_closed']}",
                f"Win rate: {stats['win_rate']:.1f}%",
                f"Net R: {stats['net_r']:+.2f}R",
                "",
                "Ты реально хочешь включить торговлю на реальном рынке?",
            ]
        ),
        {
            "inline_keyboard": [
                [{"text": "🐺 Выпустить зверя в рынок", "callback_data": "real_unlock_step1"}],
                [{"text": "❌ Отмена", "callback_data": "real_unlock_cancel"}],
            ]
        },
    )


def handle_real_unlock_step1(
    storage: Storage,
    telegram: TelegramClient,
    private_client: BybitPrivateClient,
) -> None:
    if not real_gate_stats(storage)["requirements_met"]:
        telegram.send_message(REAL_LOCK_PHRASE)
        return
    if order_safety_block_reasons(storage, private_client, include_real_gate=False):
        telegram.send_message(REAL_LOCK_PHRASE)
        return
    telegram.send_message(
        "\n".join(
            [
                "Последнее подтверждение.",
                "После этого бот сможет ставить REAL ордера по разрешённым условиям.",
            ]
        ),
        reply_markup={
            "inline_keyboard": [
                [{"text": "✅ Да, включить REAL", "callback_data": "real_unlock_step2"}],
                [{"text": "❌ Нет, отмена", "callback_data": "real_unlock_cancel"}],
            ]
        },
    )


def handle_real_unlock_step2(
    storage: Storage,
    telegram: TelegramClient,
    private_client: BybitPrivateClient,
) -> None:
    logger = logging.getLogger("RealGate")
    stats = real_gate_stats(storage)
    if not stats["requirements_met"]:
        logger.warning("Real unlock rejected at second confirmation")
        telegram.send_message(REAL_LOCK_PHRASE)
        return
    checks = run_private_api_checks(private_client)
    if not (checks["balance_ok"] and checks["positions_ok"] and checks["orders_ok"]):
        logger.warning("Real unlock rejected at second confirmation: API failed")
        send_with_keyboard(telegram, REAL_LOCK_PHRASE, build_setups_trading_menu_keyboard())
        return
    if storage.get_runtime_state("order_lifecycle_unstable", False):
        logger.warning("Real unlock rejected at second confirmation: order lifecycle unstable")
        send_with_keyboard(telegram, REAL_LOCK_PHRASE, build_setups_trading_menu_keyboard())
        return
    safety_reasons = order_safety_block_reasons(storage, private_client, include_real_gate=False)
    if safety_reasons:
        logger.warning("Real unlock rejected at second confirmation: safety control %s", "; ".join(safety_reasons))
        send_with_keyboard(telegram, REAL_LOCK_PHRASE, build_setups_trading_menu_keyboard())
        return
    storage.save_runtime_state("REAL_TRADING_UNLOCKED", True)
    logger.warning("Real unlock enabled")
    send_with_keyboard(
        telegram,
        "🐺 Зверь выпущен в рынок.\nReal trading enabled with safety filters.",
        build_setups_trading_menu_keyboard(),
    )


def handle_panic(
    storage: Storage,
    telegram: TelegramClient,
    private_client: BybitPrivateClient,
) -> None:
    logger = logging.getLogger("EmergencyControl")
    storage.save_runtime_state("REAL_TRADING_UNLOCKED", False)
    storage.save_runtime_state("PANIC_MODE", True)
    cancel_result = cancel_all_testnet_now(storage, private_client)
    logger.warning(
        "Panic mode enabled; testnet_cancel_status=%s cancelled=%s",
        cancel_result["status"],
        cancel_result["cancelled"],
    )
    send_with_keyboard(
        telegram,
        "\n".join(
            [
                "🚨 PANIC MODE ENABLED",
                "",
                "Real trading: locked",
                "New orders: blocked",
                f"TESTNET cancel request: {cancel_result['status']}",
                "Scanner: still monitoring",
            ]
        ),
        build_trading_emergency_menu_keyboard(),
    )


def handle_panic_off(storage: Storage, telegram: TelegramClient) -> None:
    storage.save_runtime_state("PANIC_MODE", False)
    storage.save_runtime_state("REAL_TRADING_UNLOCKED", False)
    logging.getLogger("EmergencyControl").warning("Panic mode disabled; real trading remains locked")
    send_with_keyboard(
        telegram,
        "✅ PANIC MODE disabled.\nReal trading remains locked.",
        build_trading_emergency_menu_keyboard(),
    )


def handle_disable_trading(storage: Storage, telegram: TelegramClient) -> None:
    storage.save_runtime_state("RUNTIME_TRADING_DISABLED", True)
    logging.getLogger("EmergencyControl").warning("Runtime trading disabled")
    send_with_keyboard(
        telegram,
        "🔒 Trading disabled.\nScanner continues.",
        build_trading_emergency_menu_keyboard(),
    )


def handle_enable_testnet_trading(
    storage: Storage,
    telegram: TelegramClient,
    private_client: BybitPrivateClient,
) -> None:
    if not private_client.testnet:
        send_with_keyboard(
            telegram,
            "❌ TESTNET trading не включён: текущий API mode MAINNET. Сначала /api_mode testnet.",
            build_trading_emergency_menu_keyboard(),
        )
        return
    storage.save_runtime_state("RUNTIME_TRADING_DISABLED", False)
    storage.save_runtime_state("REAL_TRADING_UNLOCKED", False)
    logging.getLogger("EmergencyControl").warning("Testnet/paper trading enabled; real remains locked")
    send_with_keyboard(
        telegram,
        "🧪 TESTNET trading enabled.\nReal trading remains locked.",
        build_trading_emergency_menu_keyboard(),
    )


def handle_autopilot_on(
    storage: Storage,
    telegram: TelegramClient,
    private_client: BybitPrivateClient,
) -> None:
    logger = logging.getLogger("TestnetAutopilot")
    if not private_client.testnet:
        logger.warning("Autopilot enable rejected: mode MAINNET")
        if config.BYBIT_TRADING_ENABLED and not real_trading_unlocked(storage):
            telegram.send_message(REAL_LOCK_PHRASE)
            return
        send_with_keyboard(
            telegram,
            "TESTNET Autopilot works only in TESTNET mode.",
            build_trading_autopilot_menu_keyboard(),
        )
        return
    if not config.BYBIT_TRADING_ENABLED:
        send_with_keyboard(
            telegram,
            "❌ TESTNET Autopilot не включён: BYBIT_TRADING_ENABLED=false.",
            build_trading_autopilot_menu_keyboard(),
        )
        return
    if not private_client.has_api_keys:
        send_with_keyboard(
            telegram,
            "❌ TESTNET Autopilot не включён: Bybit API keys не настроены.",
            build_trading_autopilot_menu_keyboard(),
        )
        return
    checks = run_private_api_checks(private_client)
    if not (checks["balance_ok"] and checks["positions_ok"] and checks["orders_ok"]):
        logger.warning("Autopilot enable rejected: API test failed")
        send_with_keyboard(
            telegram,
            "❌ TESTNET Autopilot не включён: API test failed.",
            build_trading_autopilot_menu_keyboard(),
        )
        return
    storage.save_runtime_state("TESTNET_AUTOPILOT_ENABLED", True)
    storage.save_runtime_state("REAL_TRADING_UNLOCKED", False)
    logger.warning("TESTNET Autopilot enabled")
    send_with_keyboard(
        telegram,
        "🧪 TESTNET Autopilot enabled.\nReal trading remains locked.",
        build_trading_autopilot_menu_keyboard(),
    )


def handle_autopilot_off(storage: Storage, telegram: TelegramClient) -> None:
    storage.save_runtime_state("TESTNET_AUTOPILOT_ENABLED", False)
    logging.getLogger("TestnetAutopilot").warning("TESTNET Autopilot disabled")
    send_with_keyboard(
        telegram,
        "⏸ TESTNET Autopilot disabled.",
        build_trading_autopilot_menu_keyboard(),
    )


def run_private_api_checks(private_client: BybitPrivateClient) -> dict[str, Any]:
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


def real_trading_unlocked(storage: Storage) -> bool:
    return bool(storage.get_runtime_state("REAL_TRADING_UNLOCKED", config.REAL_TRADING_UNLOCKED_DEFAULT))


def effective_real_trading(storage: Storage, private_client: BybitPrivateClient) -> bool:
    return (
        config.BYBIT_TRADING_ENABLED
        and not private_client.testnet
        and not mainnet_read_only_mode(storage)
        and private_client.has_api_keys
        and real_trading_unlocked(storage)
    )


def dry_run_enabled(storage: Storage) -> bool:
    return bool(storage.get_runtime_state("REAL_DRY_RUN_ENABLED", config.REAL_DRY_RUN_ENABLED))


def mainnet_read_only_mode(storage: Storage) -> bool:
    return bool(storage.get_runtime_state("MAINNET_READ_ONLY_MODE", config.MAINNET_READ_ONLY_MODE))


def order_safety_block_reasons(
    storage: Storage,
    private_client: BybitPrivateClient,
    include_real_gate: bool = True,
) -> list[str]:
    reasons = []
    if storage.get_runtime_state("PANIC_MODE", False):
        reasons.append("PANIC_MODE=true")
    if storage.get_runtime_state("RUNTIME_TRADING_DISABLED", False):
        reasons.append("runtime trading disabled")
    if storage.get_runtime_state("daily_loss_limit_reached", False):
        reasons.append("daily loss limit reached")
    if storage.get_runtime_state("order_lifecycle_unstable", False):
        reasons.append("order lifecycle unstable")
    if config.BYBIT_TRADING_ENABLED and not private_client.has_api_keys:
        reasons.append("Bybit API keys не настроены.")
    if include_real_gate and config.BYBIT_TRADING_ENABLED and not private_client.testnet and not real_trading_unlocked(storage):
        reasons.append("REAL_TRADING_UNLOCKED=false")
    return reasons


def order_safety_block_text(
    storage: Storage,
    private_client: BybitPrivateClient,
    reasons: list[str],
) -> str:
    mode = safety_mode_label(private_client)
    return "\n".join(
        [
            "❌ Ордер заблокирован Safety Control",
            "",
            f"Причина: {reasons[0] if reasons else 'unknown'}",
            f"Mode: {mode}",
            f"Real locked: {str(not real_trading_unlocked(storage)).lower()}",
            f"Panic mode: {str(bool(storage.get_runtime_state('PANIC_MODE', False))).lower()}",
        ]
    )


def safety_status_text(storage: Storage, private_client: BybitPrivateClient) -> str:
    private_client.load_credentials(storage)
    panic = bool(storage.get_runtime_state("PANIC_MODE", False))
    runtime_disabled = bool(storage.get_runtime_state("RUNTIME_TRADING_DISABLED", False))
    real_unlocked = real_trading_unlocked(storage)
    block_reasons = order_safety_block_reasons(storage, private_client, include_real_gate=True)
    checks = {"balance_ok": False, "positions_ok": False, "orders_ok": False, "errors": []}
    if private_client.can_call_private:
        checks = run_private_api_checks(private_client)
    api_ok = checks["balance_ok"] and checks["positions_ok"] and checks["orders_ok"]
    daily_loss_lock = bool(storage.get_runtime_state("daily_loss_limit_reached", False))
    mode = safety_mode_label(private_client)
    execution_mode = execution_mode_label(storage, private_client)
    paper_fallback = not config.BYBIT_TRADING_ENABLED
    testnet_capability_ok = (
        config.BYBIT_TRADING_ENABLED
        and private_client.testnet
        and private_client.has_api_keys
        and api_ok
    )
    allowed = not block_reasons
    reason = block_reasons[0] if block_reasons else "none"
    return "\n".join(
        [
            "🛡 Safety Status",
            "",
            f"Panic mode: {'ON' if panic else 'OFF'}",
            f"Runtime trading disabled: {str(runtime_disabled).lower()}",
            f"Real trading unlocked: {str(real_unlocked).lower()}",
            f"BYBIT_TRADING_ENABLED env: {str(config.BYBIT_TRADING_ENABLED).lower()}",
            f"Mode: {mode}",
            f"Execution mode: {execution_mode}",
            f"Paper fallback: {'enabled' if paper_fallback else 'disabled'}",
            f"API source: {private_client.credential_source}",
            f"Testnet order capability: {'OK' if testnet_capability_ok else 'ERROR'}",
            f"New orders allowed: {'yes' if allowed else 'no'}",
            f"Reason if blocked: {reason}",
            "",
            "Risk control: active",
            f"Daily loss lock: {'yes' if daily_loss_lock else 'no'}",
            f"API status: {'OK' if api_ok else 'ERROR'}",
        ]
    )


def safety_mode_label(private_client: BybitPrivateClient) -> str:
    if not config.BYBIT_TRADING_ENABLED:
        return "PAPER"
    return "TESTNET" if private_client.testnet else "MAINNET"


def execution_mode_label(storage: Storage, private_client: BybitPrivateClient) -> str:
    if not config.BYBIT_TRADING_ENABLED:
        return "PAPER"
    if private_client.testnet:
        return "TESTNET"
    if effective_real_trading(storage, private_client):
        return "REAL"
    return "MAINNET_LOCKED"


def real_gate_stats(storage: Storage) -> dict[str, Any]:
    counted = 0
    wins = 0
    losses = 0
    net_r = 0.0
    for record in storage.get_setup_journal(limit=None):
        final_state = normalize_final_state(record)
        if final_state in {"EXPIRED_NO_ENTRY", "EXPIRED_AFTER_ENTRY", "AMBIGUOUS_INVALIDATION_FIRST", "INVALIDATED_FIRST_UNKNOWN"}:
            continue
        quality = execution_quality_for_record(record)
        if not config.REAL_MODE_COUNT_FAST_MOVE and quality in {"FAST_MOVE", "MAYBE_NOT_EXECUTABLE", "AMBIGUOUS"}:
            continue
        if config.REAL_MODE_REQUIRE_REALISTIC_ONLY and quality != "REALISTIC":
            continue
        if final_state in {"TP1_THEN_INVALIDATED", "TP1_HIT", "TP2_HIT", "INVALIDATED_BEFORE_TP1"}:
            counted += 1
            net_r += fee_adjusted_result_r_for_record(record)
        if final_state in {"TP1_THEN_INVALIDATED", "TP1_HIT", "TP2_HIT"}:
            wins += 1
        elif final_state == "INVALIDATED_BEFORE_TP1":
            losses += 1

    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0.0
    requirements_met = (
        counted >= config.REAL_MODE_UNLOCK_MIN_CLOSED_SETUPS
        and win_rate >= config.REAL_MODE_UNLOCK_MIN_WIN_RATE
        and net_r >= config.REAL_MODE_UNLOCK_MIN_NET_R
    )
    return {
        "counted_closed": counted,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "net_r": net_r,
        "requirements_met": requirements_met,
    }


def real_order_safety_rejections(
    storage: Storage,
    private_client: BybitPrivateClient,
    plan: dict[str, Any],
) -> list[str]:
    reasons = order_safety_block_reasons(storage, private_client, include_real_gate=True)
    if not config.BYBIT_TRADING_ENABLED:
        reasons.append("BYBIT_TRADING_ENABLED=false")
    if private_client.testnet:
        reasons.append("BYBIT_TESTNET=true")
    if not private_client.testnet and mainnet_read_only_mode(storage):
        reasons.append("MAINNET_READ_ONLY_MODE=true")
    if not real_trading_unlocked(storage):
        reasons.append("REAL_TRADING_UNLOCKED=false")
    if not private_client.has_api_keys:
        reasons.append("Bybit API keys не настроены.")
    if plan.get("risk_level") == "EXTREME":
        reasons.append("risk_level = EXTREME")
    if plan.get("risk_level") == "HIGH" and not config.ALLOW_HIGH_RISK_ORDERS:
        reasons.append("risk_level = HIGH, ALLOW_HIGH_RISK_ORDERS=false")
    if plan.get("execution_status") in {"TOO_LATE_DO_NOT_CHASE", "NO_SETUP"}:
        reasons.append("execution_status запрещён")
    if plan.get("setup_status") == "NO SETUP":
        reasons.append("setup_status = NO SETUP")
    if str(plan.get("execution_quality") or "") in {"FAST_MOVE", "MAYBE_NOT_EXECUTABLE", "AMBIGUOUS"}:
        reasons.append("execution_quality запрещён")
    if storage.get_runtime_state("daily_loss_limit_reached", False):
        reasons.append("daily loss limit reached")

    symbol = str(plan.get("symbol") or "")
    if symbol:
        try:
            if private_client.get_open_orders(symbol):
                reasons.append("уже есть активный ордер по монете")
            if has_active_position(private_client.get_positions(symbol)):
                reasons.append("уже есть открытая позиция по монете")
        except Exception as exc:
            reasons.append(f"API status failed: {sanitize_error(exc)}")

    checks = run_private_api_checks(private_client)
    if not (checks["balance_ok"] and checks["positions_ok"] and checks["orders_ok"]):
        reasons.append("permissions/API checks failed")
    if storage.get_runtime_state("order_lifecycle_unstable", False):
        reasons.append("order lifecycle unstable")
    return reasons


def testnet_order_safety_rejections(
    storage: Storage,
    private_client: BybitPrivateClient,
    plan: dict[str, Any],
) -> list[str]:
    reasons = order_safety_block_reasons(storage, private_client, include_real_gate=False)
    if not private_client.has_api_keys:
        reasons.append("Bybit API keys не настроены.")
    if not private_client.testnet:
        reasons.append("API mode is MAINNET")
    if not config.BYBIT_TRADING_ENABLED:
        reasons.append("BYBIT_TRADING_ENABLED=false")
    if _to_float(plan.get("qty")) is None or (_to_float(plan.get("qty")) or 0) <= 0:
        reasons.append("qty <= 0")
    if (_to_float(plan.get("rr_tp1")) or 0) < config.MIN_RR_TO_ALLOW_ORDER:
        reasons.append("R/R ниже минимального порога")
    if plan.get("execution_status") == "TOO_LATE_DO_NOT_CHASE":
        reasons.append("execution_status = TOO_LATE_DO_NOT_CHASE")
    if str(plan.get("execution_quality") or "") in {"FAST_MOVE", "MAYBE_NOT_EXECUTABLE", "AMBIGUOUS"}:
        reasons.append("execution_quality запрещён")
    if plan.get("setup_status") == "NO SETUP":
        reasons.append("setup_status = NO SETUP")
    if plan.get("risk_level") == "EXTREME":
        reasons.append("risk_level = EXTREME")
    if plan.get("risk_level") == "HIGH" and not config.ALLOW_HIGH_RISK_ORDERS:
        reasons.append("risk_level = HIGH, ALLOW_HIGH_RISK_ORDERS=false")
    symbol = str(plan.get("symbol") or "")
    if symbol:
        try:
            if private_client.get_open_orders(symbol):
                reasons.append("уже есть активный ордер по монете")
            if has_active_position(private_client.get_positions(symbol)):
                reasons.append("уже есть открытая позиция по монете")
        except Exception as exc:
            reasons.append(f"API test failed: {sanitize_error(exc)}")
    checks = run_private_api_checks(private_client)
    if not (checks["balance_ok"] and checks["positions_ok"] and checks["orders_ok"]):
        reasons.append("API test failed")
    return reasons


def format_testnet_confirmation(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "🧪 TESTNET ORDER CONFIRMATION",
            "",
            f"Монета: {plan.get('symbol')}",
            f"Направление: {_direction_ru(plan.get('side'))}",
            f"Entry limit: {plan.get('entry_price')}",
            f"Stop: {plan.get('stop_price')}",
            f"TP1: {plan.get('tp1')}",
            f"TP2: {plan.get('tp2')}",
            "",
            f"Qty: {plan.get('qty')}",
            f"Размер позиции: {plan.get('position_size_usdt', 0):.2f} USDT",
            f"Риск: {plan.get('risk_usdt', 0):.2f} USDT",
            f"Плечо: {plan.get('leverage')}x",
            "",
            "Mode: TESTNET",
            "Real market: NO",
        ]
    )


def testnet_rejection_text(plan: dict[str, Any], reason: str) -> str:
    return "\n".join(
        [
            "❌ TESTNET ордер отклонён",
            "",
            f"Причина: {reason}",
            f"Монета: {plan.get('symbol', 'n/a')}",
            f"Направление: {_direction_ru(plan.get('side'))}",
        ]
    )


def testnet_api_not_ready(reasons: list[str]) -> bool:
    api_markers = (
        "Bybit API keys",
        "API test failed",
        "permissions/API",
        "balance:",
        "positions:",
        "orders:",
    )
    return any(any(marker in reason for marker in api_markers) for reason in reasons)


def testnet_order_rejection_message(plan: dict[str, Any], reasons: list[str]) -> str:
    if testnet_api_not_ready(reasons):
        return TESTNET_API_NOT_READY_TEXT
    return testnet_rejection_text(plan, reasons[0] if reasons else "unknown")


def mask_secret(value: str) -> str:
    text = str(value or "")
    if len(text) <= 8:
        return "****" if text else ""
    return f"{text[:4]}********{text[-4:]}"


def sanitize_error(value: Any) -> str:
    text = str(value)
    for secret in (config.BYBIT_API_SECRET, config.BYBIT_API_KEY):
        if secret:
            text = text.replace(secret, "[hidden]")
    if len(text) > 180:
        return text[:177] + "..."
    return text


def _found_label(value: bool) -> str:
    return "✅ найден" if value else "❌ не найден"


def _ok_label(value: bool) -> str:
    return "✅ OK" if value else "❌ ERROR"


def calculate_order_plan(
    alert_id: str,
    storage: Storage,
    telegram: TelegramClient,
    private_client: BybitPrivateClient,
) -> None:
    logger = logging.getLogger("ExecutionAssistant")
    safety_reasons = order_safety_block_reasons(storage, private_client, include_real_gate=False)
    if safety_reasons:
        logger.warning("Order calculation blocked by safety control: %s", "; ".join(safety_reasons))
        send_with_keyboard(
            telegram,
            order_safety_block_text(storage, private_client, safety_reasons),
            build_trading_emergency_menu_keyboard(),
        )
        return

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
    private_client.load_credentials(storage)
    plan = storage.get_paper_order(plan_id)
    if plan is None:
        send_with_keyboard(telegram, "❌ План ордера не найден.", build_main_menu_keyboard())
        return
    if plan.get("status") != "PLANNED":
        send_with_keyboard(telegram, f"❌ План уже имеет статус: {plan.get('status')}", build_main_menu_keyboard())
        return

    if config.BYBIT_TRADING_ENABLED and not private_client.testnet and not real_trading_unlocked(storage):
        storage.update_paper_order(plan_id, {"status": "REJECTED", "reject_reason": "REAL_TRADING_UNLOCKED=false"})
        logger.warning("Order rejected reason=real locked symbol=%s setup_id=%s", plan.get("symbol"), plan.get("source_setup_id"))
        telegram.send_message(REAL_LOCK_PHRASE)
        return

    safety_reasons = order_safety_block_reasons(storage, private_client, include_real_gate=False)
    if safety_reasons:
        storage.update_paper_order(plan_id, {"status": "REJECTED", "reject_reason": "; ".join(safety_reasons)})
        logger.warning("Order blocked by safety control symbol=%s reason=%s", plan.get("symbol"), "; ".join(safety_reasons))
        if config.BYBIT_TRADING_ENABLED and private_client.testnet and testnet_api_not_ready(safety_reasons):
            send_with_keyboard(telegram, TESTNET_API_NOT_READY_TEXT, build_main_menu_keyboard())
            return
        send_with_keyboard(
            telegram,
            order_safety_block_text(storage, private_client, safety_reasons),
            build_trading_emergency_menu_keyboard(),
        )
        return

    if config.BYBIT_TRADING_ENABLED and not private_client.has_api_keys:
        message = TESTNET_API_NOT_READY_TEXT if private_client.testnet else "Bybit API keys не настроены."
        send_with_keyboard(telegram, message, build_main_menu_keyboard())
        return

    if not config.BYBIT_TRADING_ENABLED:
        updated = storage.update_paper_order(plan_id, {"status": "PAPER_CREATED", "mode": "PAPER"})
        logger.info(
            "Order mode PAPER: submitted symbol=%s side=%s setup_id=%s execution_status=%s risk=%s",
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

    if config.BYBIT_TRADING_ENABLED and not private_client.testnet and real_trading_unlocked(storage):
        reasons = real_order_safety_rejections(storage, private_client, plan)
        if reasons:
            storage.update_paper_order(plan_id, {"status": "REJECTED", "reject_reason": "; ".join(reasons)})
            logger.warning("Real order rejected symbol=%s reason=%s", plan.get("symbol"), "; ".join(reasons))
            send_with_keyboard(telegram, f"❌ Ордер отклонён\nПричина: {reasons[0]}", build_main_menu_keyboard())
            return
        telegram.send_message(
            "⚠️ REAL MARKET MODE. Подтверди ещё раз.",
            reply_markup={
                "inline_keyboard": [
                    [{"text": "⚠️ Да, отправить REAL ордер", "callback_data": f"order_real_confirm:{plan_id}"}],
                    [{"text": "❌ Отмена", "callback_data": f"order_cancel:{plan_id}"}],
                ]
            },
        )
        return

    if config.BYBIT_TRADING_ENABLED and private_client.testnet:
        reasons = testnet_order_safety_rejections(storage, private_client, plan)
        if reasons:
            storage.update_paper_order(plan_id, {"status": "REJECTED", "reject_reason": "; ".join(reasons)})
            logger.warning("Testnet order rejected symbol=%s reason=%s", plan.get("symbol"), "; ".join(reasons))
            send_with_keyboard(telegram, testnet_order_rejection_message(plan, reasons), build_main_menu_keyboard())
            return
        logger.info("Testnet order confirmation requested symbol=%s setup_id=%s", plan.get("symbol"), plan.get("source_setup_id"))
        telegram.send_message(
            format_testnet_confirmation(plan),
            reply_markup={
                "inline_keyboard": [
                    [{"text": "✅ Отправить TESTNET ордер", "callback_data": f"order_testnet_confirm:{plan_id}"}],
                    [{"text": "❌ Отмена", "callback_data": f"order_cancel:{plan_id}"}],
                ]
            },
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
                "submitted_at": datetime.now(UTC).isoformat(),
                "exchange_response": response,
            },
        )
        logger.info(
            "Order mode %s: limit order submitted symbol=%s side=%s order_id=%s",
            private_client.mode_label(),
            plan.get("symbol"),
            plan.get("side"),
            result.get("orderId"),
        )
        send_with_keyboard(
            telegram,
            "\n".join(
                [
                    "🧪 TESTNET order sent. Это НЕ real market." if config.BYBIT_TESTNET else "✅ Лимитный ордер отправлен.",
                    f"Режим: {private_client.mode_label()}",
                    f"Монета: {plan.get('symbol')}",
                    f"Order ID: {result.get('orderId', 'n/a')}",
                ]
            ),
            build_order_lifecycle_keyboard(plan_id),
        )
    except Exception as exc:
        logger.error("Order submit failed symbol=%s error=%s", plan.get("symbol"), exc)
        storage.update_paper_order(plan_id, {"status": "REJECTED", "reject_reason": str(exc)})
        send_with_keyboard(telegram, f"❌ Ордер отклонён\nПричина: {exc}", build_main_menu_keyboard())


def submit_testnet_order_after_confirmation(
    plan_id: str,
    storage: Storage,
    telegram: TelegramClient,
    private_client: BybitPrivateClient,
) -> None:
    logger = logging.getLogger("ExecutionAssistant")
    private_client.load_credentials(storage)
    plan = storage.get_paper_order(plan_id)
    if plan is None:
        send_with_keyboard(telegram, "❌ План ордера не найден.", build_main_menu_keyboard())
        return
    if plan.get("status") != "PLANNED":
        send_with_keyboard(telegram, f"❌ План уже имеет статус: {plan.get('status')}", build_main_menu_keyboard())
        return
    if not (config.BYBIT_TRADING_ENABLED and private_client.testnet):
        send_with_keyboard(telegram, "❌ TESTNET ордер отклонён\n\nПричина: режим не TESTNET.", build_main_menu_keyboard())
        return

    safety_reasons = order_safety_block_reasons(storage, private_client, include_real_gate=False)
    if safety_reasons:
        storage.update_paper_order(plan_id, {"status": "REJECTED", "reject_reason": "; ".join(safety_reasons)})
        logger.warning("Testnet order blocked by safety control symbol=%s reason=%s", plan.get("symbol"), "; ".join(safety_reasons))
        if testnet_api_not_ready(safety_reasons):
            send_with_keyboard(telegram, TESTNET_API_NOT_READY_TEXT, build_main_menu_keyboard())
            return
        send_with_keyboard(
            telegram,
            order_safety_block_text(storage, private_client, safety_reasons),
            build_trading_emergency_menu_keyboard(),
        )
        return

    reasons = testnet_order_safety_rejections(storage, private_client, plan)
    if reasons:
        storage.update_paper_order(plan_id, {"status": "REJECTED", "reject_reason": "; ".join(reasons)})
        logger.warning("Testnet order rejected after confirmation symbol=%s reason=%s", plan.get("symbol"), "; ".join(reasons))
        send_with_keyboard(telegram, testnet_order_rejection_message(plan, reasons), build_main_menu_keyboard())
        return

    order_link_id = str(plan.get("order_link_id") or f"bobabot-testnet-{uuid.uuid4().hex[:18]}")
    try:
        response = private_client.place_limit_order(
            symbol=str(plan.get("symbol")),
            side=str(plan.get("bybit_side")),
            qty=str(plan.get("qty")),
            price=str(plan.get("entry_price")),
            reduce_only=False,
            order_link_id=order_link_id,
        )
        result = response.get("result") or {}
        storage.update_paper_order(
            plan_id,
            {
                "status": "SUBMITTED",
                "mode": "TESTNET",
                "order_link_id": order_link_id,
                "exchange_order_id": result.get("orderId"),
                "submitted_at": datetime.now(UTC).isoformat(),
                "raw_response_json": response,
                "exchange_response": response,
            },
        )
        logger.info(
            "Testnet order submitted symbol=%s side=%s order_id=%s link_id=%s",
            plan.get("symbol"),
            plan.get("side"),
            result.get("orderId"),
            order_link_id,
        )
        send_with_keyboard(
            telegram,
            "\n".join(
                [
                    "🧪 TESTNET order sent. Это НЕ real market.",
                    f"Монета: {plan.get('symbol')}",
                    f"Order ID: {result.get('orderId', 'n/a')}",
                    f"Order Link ID: {order_link_id}",
                    "",
                    "⚠️ TP/SL пока не выставляются автоматически.",
                    "Lifecycle Manager будет отслеживать статус ордера.",
                ]
            ),
            build_order_lifecycle_keyboard(plan_id),
        )
    except Exception as exc:
        reason = sanitize_error(exc)
        logger.warning("Testnet order rejected symbol=%s reason=%s", plan.get("symbol"), reason)
        storage.update_paper_order(plan_id, {"status": "REJECTED", "reject_reason": reason})
        send_with_keyboard(telegram, testnet_rejection_text(plan, reason), build_main_menu_keyboard())


def submit_real_order_after_confirmation(
    plan_id: str,
    storage: Storage,
    telegram: TelegramClient,
    private_client: BybitPrivateClient,
) -> None:
    logger = logging.getLogger("ExecutionAssistant")
    private_client.load_credentials(storage)
    plan = storage.get_paper_order(plan_id)
    if plan is None:
        send_with_keyboard(telegram, "❌ План ордера не найден.", build_main_menu_keyboard())
        return
    if plan.get("status") != "PLANNED":
        send_with_keyboard(telegram, f"❌ План уже имеет статус: {plan.get('status')}", build_main_menu_keyboard())
        return
    if config.BYBIT_TRADING_ENABLED and not private_client.testnet and not real_trading_unlocked(storage):
        storage.update_paper_order(plan_id, {"status": "REJECTED", "reject_reason": "real gate closed"})
        telegram.send_message(REAL_LOCK_PHRASE)
        return

    safety_reasons = order_safety_block_reasons(storage, private_client, include_real_gate=False)
    if safety_reasons:
        storage.update_paper_order(plan_id, {"status": "REJECTED", "reject_reason": "; ".join(safety_reasons)})
        logger.warning("Real order blocked by safety control symbol=%s reason=%s", plan.get("symbol"), "; ".join(safety_reasons))
        send_with_keyboard(
            telegram,
            order_safety_block_text(storage, private_client, safety_reasons),
            build_trading_emergency_menu_keyboard(),
        )
        return
    if not (config.BYBIT_TRADING_ENABLED and not private_client.testnet and real_trading_unlocked(storage)):
        storage.update_paper_order(plan_id, {"status": "REJECTED", "reject_reason": "real gate closed"})
        telegram.send_message(REAL_LOCK_PHRASE)
        return

    reasons = real_order_safety_rejections(storage, private_client, plan)
    if reasons:
        storage.update_paper_order(plan_id, {"status": "REJECTED", "reject_reason": "; ".join(reasons)})
        logger.warning("Real order rejected after double confirmation symbol=%s reason=%s", plan.get("symbol"), "; ".join(reasons))
        send_with_keyboard(telegram, f"❌ Ордер отклонён\nПричина: {reasons[0]}", build_main_menu_keyboard())
        return

    order_link_id = str(plan.get("order_link_id") or f"bobabot-real-{uuid.uuid4().hex[:18]}")
    try:
        response = private_client.place_limit_order(
            symbol=str(plan.get("symbol")),
            side=str(plan.get("bybit_side")),
            qty=str(plan.get("qty")),
            price=str(plan.get("entry_price")),
            reduce_only=False,
            order_link_id=order_link_id,
        )
        result = response.get("result") or {}
        storage.update_paper_order(
            plan_id,
            {
                "status": "SUBMITTED",
                "mode": "REAL",
                "order_link_id": order_link_id,
                "exchange_order_id": result.get("orderId"),
                "raw_response_json": response,
                "exchange_response": response,
            },
        )
        logger.warning(
            "Order mode REAL: limit order submitted symbol=%s side=%s order_id=%s",
            plan.get("symbol"),
            plan.get("side"),
            result.get("orderId"),
        )
        send_with_keyboard(
            telegram,
            "\n".join(
                [
                    "✅ REAL limit order sent.",
                    f"Монета: {plan.get('symbol')}",
                    f"Order ID: {result.get('orderId', 'n/a')}",
                ]
            ),
            build_main_menu_keyboard(),
        )
    except Exception as exc:
        logger.error("Real order submit failed symbol=%s error=%s", plan.get("symbol"), sanitize_error(exc))
        storage.update_paper_order(plan_id, {"status": "REJECTED", "reject_reason": sanitize_error(exc)})
        send_with_keyboard(telegram, f"❌ Ордер отклонён\nПричина: {sanitize_error(exc)}", build_main_menu_keyboard())


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


def cancel_order_text(
    storage: Storage,
    private_client: BybitPrivateClient,
    args: list[str],
) -> str:
    if not args:
        return "Использование: /cancel_order ORDER_ID"
    target = args[0]
    if not private_client.testnet:
        return "❌ /cancel_order сейчас разрешён только для TESTNET. Переключи /api_mode testnet."
    order = find_stored_order(storage, target)
    if order is None:
        try:
            for open_order in private_client.get_open_orders():
                if target in {str(open_order.get("orderId") or ""), str(open_order.get("orderLinkId") or "")}:
                    private_client.cancel_order(
                        str(open_order.get("symbol")),
                        order_id=open_order.get("orderId"),
                        order_link_id=open_order.get("orderLinkId"),
                    )
                    return "✅ Ордер отменён."
        except Exception as exc:
            return f"❌ Не удалось найти/отменить ордер: {sanitize_error(exc)}"
        return "❌ Ордер не найден в локальной базе или открытых Bybit ордерах."
    if order.get("mode") not in {"TESTNET", "REAL"}:
        storage.update_paper_order(
            str(order.get("id")),
            {
                "status": "CANCELLED",
                "cancelled_at": datetime.now(UTC).isoformat(),
                "closed_at": datetime.now(UTC).isoformat(),
            },
        )
        return "✅ Локальный paper order отменён."
    if order.get("mode") == "TESTNET" and not private_client.testnet:
        return "❌ Сейчас API mode не TESTNET. Переключи /api_mode testnet."
    if order.get("mode") == "TESTNET" and not private_client.trading_enabled:
        return "❌ BYBIT_TRADING_ENABLED=false. TESTNET cancel не отправлен на Bybit."
    if order.get("mode") == "REAL" and not real_trading_unlocked(storage):
        return REAL_LOCK_PHRASE
    if order.get("mode") == "REAL" and mainnet_read_only_mode(storage):
        logging.getLogger("DryRun").warning("Blocked MAINNET cancel_order in read-only mode")
        return "❌ MAINNET read-only mode: cancel не отправлен."
    try:
        private_client.cancel_order(
            symbol=str(order.get("symbol")),
            order_id=str(order.get("exchange_order_id") or "") or None,
            order_link_id=str(order.get("order_link_id") or "") or None,
        )
        storage.update_paper_order(
            str(order.get("id")),
            {
                "status": "CANCELLED",
                "cancelled_at": datetime.now(UTC).isoformat(),
                "closed_at": datetime.now(UTC).isoformat(),
            },
        )
        logging.getLogger("ExecutionAssistant").info(
            "Testnet order cancelled symbol=%s order_id=%s link_id=%s",
            order.get("symbol"),
            order.get("exchange_order_id"),
            order.get("order_link_id"),
        )
        return "✅ Ордер отменён."
    except Exception as exc:
        return f"❌ Не удалось отменить ордер: {sanitize_error(exc)}"


def handle_cancel_all_testnet_request(
    storage: Storage,
    telegram: TelegramClient,
    private_client: BybitPrivateClient,
) -> None:
    logging.getLogger("EmergencyControl").warning("Cancel all TESTNET requested")
    telegram.send_message(
        "Отменить все TESTNET ордера?",
        reply_markup={
            "inline_keyboard": [
                [{"text": "✅ Да, отменить TESTNET", "callback_data": "cancel_all_testnet_confirm"}],
                [{"text": "❌ Нет", "callback_data": "cancel_all_testnet_cancel"}],
            ]
        },
    )


def handle_cancel_all_request(
    storage: Storage,
    telegram: TelegramClient,
    private_client: BybitPrivateClient,
) -> None:
    logger = logging.getLogger("EmergencyControl")
    logger.warning("Cancel all requested mode=%s", private_client.mode_label())
    if private_client.testnet:
        handle_cancel_all_testnet_request(storage, telegram, private_client)
        return
    if not real_trading_unlocked(storage):
        telegram.send_message(REAL_LOCK_PHRASE)
        return
    telegram.send_message(
        "⚠️ MAINNET cancel-all requested.\nВ этой фазе mainnet cancel-all не исполняется автоматически. Подтвердить отказ?",
        reply_markup={
            "inline_keyboard": [
                [{"text": "✅ Подтвердить: не отменять MAINNET", "callback_data": "cancel_all_mainnet_confirm"}],
                [{"text": "❌ Отмена", "callback_data": "cancel_all_mainnet_cancel"}],
            ]
        },
    )


def cancel_all_testnet_now(storage: Storage, private_client: BybitPrivateClient) -> dict[str, Any]:
    result = {
        "status": "skipped",
        "cancelled": 0,
        "errors": [],
    }
    if not private_client.has_api_keys:
        result["errors"].append("Bybit API keys не настроены.")
        return result
    if not private_client.testnet:
        result["errors"].append("API mode is not TESTNET")
        return result
    if not private_client.trading_enabled:
        result["errors"].append("BYBIT_TRADING_ENABLED=false")
        return result
    try:
        for order in private_client.get_open_orders():
            symbol = order.get("symbol")
            order_id = order.get("orderId")
            order_link_id = order.get("orderLinkId")
            if symbol and (order_id or order_link_id):
                private_client.cancel_order(str(symbol), order_id=order_id, order_link_id=order_link_id)
                result["cancelled"] += 1
    except Exception as exc:
        result["errors"].append(sanitize_error(exc))

    for local_order in storage.get_paper_orders(
        statuses=["SUBMITTED", "OPEN", "PARTIALLY_FILLED", "UNKNOWN", "CANCEL_REQUESTED"]
    ):
        if local_order.get("mode") == "TESTNET":
            storage.update_paper_order(
                str(local_order.get("id")),
                {
                    "status": "CANCELLED",
                    "cancelled_at": datetime.now(UTC).isoformat(),
                    "closed_at": datetime.now(UTC).isoformat(),
                },
            )

    result["status"] = "failed" if result["errors"] else "done"
    logging.getLogger("EmergencyControl").warning(
        "Cancel all TESTNET completed status=%s cancelled=%s errors=%s",
        result["status"],
        result["cancelled"],
        len(result["errors"]),
    )
    return result


def cancel_all_testnet_text(storage: Storage, private_client: BybitPrivateClient) -> str:
    result = cancel_all_testnet_now(storage, private_client)
    if result["status"] == "skipped":
        reason = result["errors"][0] if result["errors"] else "unknown"
        return f"⚠️ TESTNET cancel skipped.\nПричина: {reason}"
    lines = [f"✅ TESTNET cancel запрос выполнен. Отменено: {result['cancelled']}"]
    if result["errors"]:
        lines.append(f"Ошибки: {result['errors'][0]}")
    return "\n".join(lines)


def find_stored_order(storage: Storage, target: str) -> dict[str, Any] | None:
    for order in storage.get_paper_orders(limit=None):
        values = {
            str(order.get("id") or ""),
            str(order.get("exchange_order_id") or ""),
            str(order.get("order_link_id") or ""),
        }
        if target in values:
            return order
    return None


def orders_text(storage: Storage, private_client: BybitPrivateClient) -> str:
    lines = ["📋 Ордера"]
    orders = storage.get_paper_orders(limit=50)
    paper_orders = [order for order in orders if order.get("mode") in {None, "PAPER"}]
    testnet_orders = [order for order in orders if order.get("mode") == "TESTNET"]
    if not paper_orders and not testnet_orders:
        lines.append("Активных ордеров нет.")

    lines.extend(["", "Paper orders:"])
    if paper_orders:
        for order in paper_orders[:10]:
            lines.append(_format_local_order_line(order))
    else:
        lines.append("- нет")

    lines.extend(["", "Bybit TESTNET orders:"])
    if testnet_orders:
        for index, order in enumerate(testnet_orders[:10], start=1):
            lines.extend(
                [
                    f"{index}. {order.get('symbol')} | {_direction_ru(order.get('side'))} | {order.get('status')}",
                    f"Entry: {order.get('entry_price')}",
                    f"Qty: {order.get('qty')}",
                    f"Age: {_format_order_age(order.get('submitted_at') or order.get('created_at'))}",
                    f"Order ID: {order.get('exchange_order_id') or order.get('order_link_id') or order.get('id')}",
                    f"Created: {_format_order_time(order.get('created_at'))}",
                ]
            )
    else:
        lines.append("- нет")

    lines.append("")
    lines.append("Bybit open orders:")
    if private_client.can_call_private:
        try:
            open_orders = private_client.get_open_orders()
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
            lines.append(f"- ошибка API ({exc})")
    else:
        lines.append("- API credentials not configured")
    return "\n".join(lines)


def _format_local_order_line(order: dict[str, Any]) -> str:
    return (
        f"- {order.get('symbol')} | {_direction_ru(order.get('side'))} | {_order_status_ru(order.get('status'))} | "
        f"{order.get('mode') or 'PAPER'} | Entry {order.get('entry_price')} | Qty {order.get('qty')}"
    )


def _format_order_time(value: Any) -> str:
    if not value:
        return "n/a"
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    return parsed.strftime("%H:%M UTC")


def _format_order_age(value: Any) -> str:
    if not value:
        return "n/a"
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return "n/a"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    minutes = max(0, int((datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60}h {minutes % 60}m"


def positions_text(private_client: BybitPrivateClient) -> str:
    if not private_client.can_call_private:
        return "📊 Позиции:\nAPI credentials not configured"
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
        return "💰 Баланс:\nAPI credentials not configured"
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
    for order in storage.get_paper_orders(
        statuses=[
            "PLANNED",
            "PAPER_CREATED",
            "SUBMITTED",
            "OPEN",
            "PARTIALLY_FILLED",
            "FILLED",
            "POSITION_OPENED",
            "UNKNOWN",
        ]
    ):
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


def _round_dry_run_qty(qty: float, instrument_info: dict[str, Any] | None) -> float:
    if qty <= 0:
        return 0.0
    step = _dry_run_qty_step(instrument_info)
    if step is None or step <= 0:
        return qty
    rounded = math.floor(qty / step) * step
    precision = max(0, min(12, _decimal_places(step)))
    return round(rounded, precision)


def _dry_run_qty_step(instrument_info: dict[str, Any] | None) -> float | None:
    if not isinstance(instrument_info, dict):
        return None
    lot_filter = instrument_info.get("lotSizeFilter") or {}
    return _to_float(lot_filter.get("qtyStep"))


def _dry_run_min_qty(instrument_info: dict[str, Any] | None) -> float | None:
    if not isinstance(instrument_info, dict):
        return None
    lot_filter = instrument_info.get("lotSizeFilter") or {}
    return _to_float(lot_filter.get("minOrderQty"))


def _decimal_places(value: float) -> int:
    text = f"{value:.12f}".rstrip("0").rstrip(".")
    if "." not in text:
        return 0
    return len(text.split(".", 1)[1])


def _dry_run_profit_usdt(side: str, entry: float, target: float, position_size_usdt: float) -> float:
    if entry <= 0:
        return 0.0
    if side == "LONG":
        return position_size_usdt * ((target - entry) / entry)
    return position_size_usdt * ((entry - target) / entry)


def _dry_fmt(value: Any) -> str:
    parsed = _to_float(value)
    if parsed is None:
        return "n/a"
    if abs(parsed) >= 100:
        return f"{parsed:.2f}"
    if abs(parsed) >= 1:
        return f"{parsed:.4f}"
    if abs(parsed) >= 0.01:
        return f"{parsed:.6f}"
    return f"{parsed:.8f}"


def _direction_ru(value: Any) -> str:
    return {"LONG": "ЛОНГ", "SHORT": "ШОРТ"}.get(str(value or ""), str(value or "n/a"))


def _order_status_ru(value: Any) -> str:
    labels = {
        "PLANNED": "ЗАПЛАНИРОВАН",
        "PAPER_CREATED": "PAPER СОЗДАН",
        "SUBMITTED": "ОТПРАВЛЕН",
        "OPEN": "ОТКРЫТ",
        "PARTIALLY_FILLED": "ЧАСТИЧНО ИСПОЛНЕН",
        "CANCEL_REQUESTED": "ОТМЕНА ЗАПРОШЕНА",
        "FILLED": "ИСПОЛНЕН",
        "CANCELLED": "ОТМЕНЁН",
        "REJECTED": "ОТКЛОНЁН",
        "EXPIRED": "ИСТЁК",
        "INVALIDATED_BEFORE_FILL": "СЛОМАН ДО ИСПОЛНЕНИЯ",
        "POSITION_OPENED": "ПОЗИЦИЯ ОТКРЫТА",
        "POSITION_CLOSED": "ПОЗИЦИЯ ЗАКРЫТА",
        "UNKNOWN": "НЕИЗВЕСТНО",
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


def maybe_sync_orders(
    storage: Storage,
    telegram: TelegramClient,
    private_client: BybitPrivateClient,
) -> None:
    if not config.ORDER_SYNC_ENABLED:
        return
    now_ts = time.time()
    last_ts = float(storage.get_runtime_state("last_order_sync_ts", 0) or 0)
    if now_ts - last_ts < config.ORDER_SYNC_INTERVAL_SECONDS:
        return
    storage.save_runtime_state("last_order_sync_ts", now_ts)
    try:
        sync_testnet_orders(private_client, storage, telegram=telegram, notify=True)
    except Exception as exc:
        logging.getLogger("OrderLifecycle").error("Order sync loop failed: %s", exc)


async def main_async() -> None:
    setup_logging()
    logger = logging.getLogger("main")
    config.validate_config()
    health_server = start_health_server()

    storage = Storage()
    bybit = BybitClient()
    private_client = BybitPrivateClient(storage)
    telegram = TelegramClient(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
    scanner = MarketScanner(bybit, storage, telegram)
    autopilot = TestnetAutopilot(storage, private_client, telegram)
    scanner.autopilot_handler = autopilot.handle_alert
    scanner.autopilot_setup_handler = autopilot.maybe_run_testnet_autopilot

    logger.info("Starting Bybit Futures Radar")
    if config.HOSTING_MODE:
        logger.info("Hosting mode enabled")
    if config.ORDER_SYNC_ENABLED:
        logger.info("Order sync enabled interval=%ss", config.ORDER_SYNC_INTERVAL_SECONDS)

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
            maybe_sync_orders(storage, telegram, private_client)
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

import asyncio
import logging
import signal
import time
from typing import Any

import config
from bybit_client import BybitClient
from health_server import start_health_server
from scanner import MarketScanner
from storage import Storage
from telegram_client import TelegramClient, build_main_keyboard


BUTTON_COMMANDS = {
    "📊 Status": "/status",
    "🔥 Top OI": "/top",
    "🕘 Last Alerts": "/last",
    "⚙️ Config": "/config",
    "⏸ Pause": "/pause",
    "▶️ Resume": "/resume",
    "❓ Help": "/help",
    "📒 Сетапы": "/setups",
    "📘 Журнал": "/journal",
    "📈 Стата": "/stats",
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

    return "\n".join(
        [
            "Bybit Futures Radar status",
            f"Monitoring: {'enabled' if enabled else 'disabled'}",
            f"Symbols monitored: {symbols_count}",
            f"Min 24h turnover filter: {config.MIN_24H_TURNOVER_USDT:,.0f} USDT",
            f"Last scan time: {last_scan_time}",
            f"Alerts today: {alerts_today}",
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
            "📊 Status — статус бота",
            "🔥 Top OI — топ монет по росту Open Interest за 15 минут",
            "🕘 Last Alerts — последние алерты",
            "⚙️ Config — текущие настройки",
            "⏸ Pause — остановить мониторинг",
            "▶️ Resume — возобновить мониторинг",
            "❓ Help — помощь",
            "📒 Сетапы — активные сценарии, за которыми бот следит",
            "📘 Журнал — закрытые сценарии",
            "📈 Стата — статистика отработки сетапов",
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
        ]
    )


def send_with_keyboard(telegram: TelegramClient, text: str) -> None:
    telegram.send_message(text, reply_markup=build_main_keyboard())


def handle_update(
    update: dict[str, Any],
    storage: Storage,
    telegram: TelegramClient,
    scanner: MarketScanner,
) -> None:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")

    if not telegram.is_authorized_chat(chat_id):
        logging.getLogger("CommandHandler").warning("Ignoring command from unauthorized chat_id=%s", chat_id)
        return

    text = (message.get("text") or "").strip()
    if text in BUTTON_COMMANDS:
        command = BUTTON_COMMANDS[text]
    elif text.startswith("/"):
        command = text.split()[0].split("@")[0].lower()
    else:
        return

    if command == "/start":
        send_with_keyboard(telegram, "Bybit futures radar bot is alive.")
    elif command == "/help":
        send_with_keyboard(telegram, help_text())
    elif command == "/status":
        send_with_keyboard(telegram, status_text(storage))
    elif command == "/config":
        send_with_keyboard(telegram, config_text())
    elif command == "/last":
        send_with_keyboard(telegram, scanner.format_last_alerts(limit=10))
    elif command == "/setups":
        send_with_keyboard(telegram, scanner.format_active_setups())
    elif command == "/journal":
        send_with_keyboard(telegram, scanner.format_setup_journal(limit=10))
    elif command in {"/statistics", "/stats"}:
        send_with_keyboard(telegram, scanner.format_setup_statistics())
    elif command == "/debug_state":
        send_with_keyboard(telegram, debug_state_text(storage))
    elif command == "/backup_db":
        backup_path = storage.backup_database()
        send_with_keyboard(telegram, f"✅ Бэкап базы создан: {backup_path}")
    elif command == "/pause":
        storage.state["monitoring_enabled"] = False
        storage.save()
        send_with_keyboard(telegram, "Monitoring paused.")
    elif command == "/resume":
        storage.state["monitoring_enabled"] = True
        storage.save()
        send_with_keyboard(telegram, "Monitoring resumed.")
    elif command == "/top":
        send_with_keyboard(telegram, scanner.format_top_oi_growth())
    else:
        send_with_keyboard(
            telegram,
            "Unknown command. Use /status, /config, /last, /setups, /journal, /stats, /pause, /resume, /top, /help.",
        )


def poll_telegram_commands(
    storage: Storage,
    telegram: TelegramClient,
    scanner: MarketScanner,
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
            handle_update(update, storage, telegram, scanner)
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
                next_scan_ts = time.monotonic() + config.SCAN_INTERVAL_SECONDS

            poll_telegram_commands(storage, telegram, scanner)
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

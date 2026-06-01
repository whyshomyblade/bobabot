import logging
import shutil
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import config
from database import BotDatabase
from setup_tracker import (
    execution_quality_for_record,
    fee_adjusted_result_r_for_record,
    normalize_final_state,
    raw_result_r_for_record,
)


ORDER_STATE_CACHE_STATUSES = [
    "PLANNED",
    "PAPER_CREATED",
    "SUBMITTED",
    "OPEN",
    "PARTIALLY_FILLED",
    "CANCEL_REQUESTED",
    "FILLED",
    "POSITION_OPENED",
    "UNKNOWN",
]

RUNTIME_CONFIG_KEYS = {
    "BYBIT_TRADING_ENABLED",
    "BYBIT_TESTNET",
    "TESTNET_AUTOPILOT_ENABLED",
    "TESTNET_AGGRESSIVE_MODE",
    "SCAN_INTERVAL_SECONDS",
    "TESTNET_AUTOPILOT_MAX_ACTIVE_ORDERS",
    "ACCOUNT_RISK_PERCENT",
    "PAPER_ACCOUNT_BALANCE_USDT",
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat()


def today_key() -> str:
    return utc_now().date().isoformat()


class Storage:
    def __init__(
        self,
        path: Path | str = config.STATE_FILE,
        database_path: Path | str = config.DATABASE_PATH,
    ) -> None:
        self.path = Path(path)
        self.database = BotDatabase(database_path)
        self.logger = logging.getLogger(self.__class__.__name__)
        self.database.migrate_from_state_json(self.path)
        self.database.backup_before_phase7()
        self.database.backup_before_phase8()
        self.state = self.load()
        self._normalize()
        self.save()

    def default_state(self) -> dict[str, Any]:
        return {
            "monitoring_enabled": True,
            "symbols": [],
            "symbols_last_refresh_ts": 0,
            "last_scan_time": None,
            "last_alerts": {},
            "recent_alerts": [],
            "alerts_today": {
                "date": today_key(),
                "count": 0,
            },
            "history": {},
            "telegram_update_offset": None,
            "active_setups": [],
            "setup_journal": [],
            "paper_orders": [],
            "symbol_blacklist": list(config.SYMBOL_BLACKLIST),
            "last_daily_report_date": None,
            "REAL_TRADING_UNLOCKED": config.REAL_TRADING_UNLOCKED_DEFAULT,
            "REAL_DRY_RUN_ENABLED": config.REAL_DRY_RUN_ENABLED,
            "MAINNET_READ_ONLY_MODE": config.MAINNET_READ_ONLY_MODE,
            "TESTNET_AUTOPILOT_ENABLED": config.TESTNET_AUTOPILOT_ENABLED,
            "TESTNET_AGGRESSIVE_MODE": config.TESTNET_AGGRESSIVE_MODE,
            "PANIC_MODE": False,
            "RUNTIME_TRADING_DISABLED": False,
            "CLEANUP_ENABLED": config.CLEANUP_ENABLED,
            "CLEANUP_DELETE_AFTER_HOURS": config.CLEANUP_DELETE_AFTER_HOURS,
            "last_cleanup_ts": 0,
            "api_set_pending": False,
            "last_setup_tracking_ts": 0,
        }

    def load(self) -> dict[str, Any]:
        state = self.default_state()
        runtime_state = self.database.get_all_runtime_state()
        for key, value in runtime_state.items():
            if key.startswith("sqlite_"):
                continue
            state[key] = value
        self._apply_runtime_config_overrides(runtime_state)
        self.apply_fee_settings(self.get_fee_settings())

        state["recent_alerts"] = self.database.get_alert_history(limit=config.RECENT_ALERTS_LIMIT)
        state["active_setups"] = self.database.get_active_setups()
        state["setup_journal"] = self.database.get_setup_journal(limit=config.SETUP_JOURNAL_MAX_RECORDS)
        state["paper_orders"] = self.database.get_paper_orders(statuses=ORDER_STATE_CACHE_STATUSES)
        return state

    def save(self) -> None:
        self._normalize()

        self.database.replace_active_setups(self.get_active_setups())
        for record in self.state.get("recent_alerts", []):
            if isinstance(record, dict):
                self.database.add_alert_record(record)

        runtime_skip = {
            "recent_alerts",
            "active_setups",
            "setup_journal",
            "alert_history",
            "paper_orders",
        }
        for key, value in self.state.items():
            if key not in runtime_skip:
                self.database.save_runtime_state(key, value)

    def close(self) -> None:
        self.save()
        self.database.close()

    def reset_alert_counter_if_needed(self) -> None:
        alerts_today = self.state.setdefault("alerts_today", {})
        current_date = today_key()

        if alerts_today.get("date") != current_date:
            self.state["alerts_today"] = {
                "date": current_date,
                "count": 0,
            }

    def get_alert_history(self, limit: int | None = None) -> list[dict[str, Any]]:
        return self.database.get_alert_history(limit=limit)

    def get_alert_record(self, alert_id: str) -> dict[str, Any] | None:
        for record in self.database.get_alert_history(limit=None):
            if record.get("id") == alert_id:
                return record
        return None

    def add_alert_record(self, record: dict[str, Any]) -> None:
        recent_alerts = self.state.setdefault("recent_alerts", [])
        if not isinstance(recent_alerts, list):
            recent_alerts = []
        recent_alerts.append(record)
        self.state["recent_alerts"] = recent_alerts[-config.RECENT_ALERTS_LIMIT :]
        self.database.add_alert_record(record)

    def get_active_setups(self) -> list[dict[str, Any]]:
        setups = self.state.setdefault("active_setups", [])
        if not isinstance(setups, list):
            setups = []
            self.state["active_setups"] = setups
        return [item for item in setups if isinstance(item, dict)]

    def save_active_setups(self, setups: list[dict[str, Any]]) -> None:
        clean_setups = [item for item in setups if isinstance(item, dict)]
        self.state["active_setups"] = clean_setups
        self.database.replace_active_setups(clean_setups)
        self.save()

    def add_active_setup(self, setup: dict[str, Any]) -> bool:
        setups = self.get_active_setups()
        setup_id = setup.get("id")
        if setup_id and any(item.get("id") == setup_id for item in setups):
            return False

        setups.append(setup)
        self.state["active_setups"] = setups
        self.database.upsert_active_setup(setup)
        self.save()
        return True

    def update_active_setup(self, setup_id: str, updates: dict[str, Any]) -> bool:
        setups = self.get_active_setups()
        for setup in setups:
            if setup.get("id") == setup_id:
                setup.update(updates)
                self.state["active_setups"] = setups
                self.database.upsert_active_setup(setup)
                self.save()
                return True
        return False

    def remove_active_setup(self, setup_id: str) -> dict[str, Any] | None:
        setups = self.get_active_setups()
        remaining = []
        removed = None
        for setup in setups:
            if setup.get("id") == setup_id:
                removed = setup
            else:
                remaining.append(setup)

        if removed is not None:
            self.state["active_setups"] = remaining
            self.database.remove_active_setup(setup_id)
            self.save()
        return removed

    def add_setup_journal_record(self, record: dict[str, Any]) -> None:
        journal = self.state.setdefault("setup_journal", [])
        if not isinstance(journal, list):
            journal = []

        journal.append(record)
        self.state["setup_journal"] = journal[-config.SETUP_JOURNAL_MAX_RECORDS :]
        self.database.add_setup_journal_record(record)
        self.database.trim_setup_journal(config.SETUP_JOURNAL_MAX_RECORDS)

    def get_setup_journal(self, limit: int | None = None) -> list[dict[str, Any]]:
        return self.database.get_setup_journal(limit=limit)

    def add_paper_order(self, record: dict[str, Any]) -> dict[str, Any]:
        saved = self.database.add_paper_order(record)
        self.state["paper_orders"] = self.database.get_paper_orders(statuses=ORDER_STATE_CACHE_STATUSES)
        return saved

    def update_paper_order(self, order_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        updated = self.database.update_paper_order(order_id, updates)
        self.state["paper_orders"] = self.database.get_paper_orders(statuses=ORDER_STATE_CACHE_STATUSES)
        return updated

    def get_paper_order(self, order_id: str) -> dict[str, Any] | None:
        return self.database.get_paper_order(order_id)

    def get_paper_orders(
        self,
        statuses: list[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.database.get_paper_orders(statuses=statuses, limit=limit)

    def save_api_credentials(
        self,
        api_key: str,
        api_secret: str,
        masked_key: str,
        mode: str = "TESTNET",
    ) -> dict[str, Any]:
        return self.database.save_api_credentials(api_key, api_secret, masked_key, mode)

    def get_active_api_credentials(self) -> dict[str, Any] | None:
        return self.database.get_active_api_credentials()

    def clear_api_credentials(self) -> None:
        self.database.clear_api_credentials()

    def update_active_api_mode(self, mode: str) -> dict[str, Any] | None:
        return self.database.update_active_api_mode(mode)

    def add_autopilot_decision(self, record: dict[str, Any]) -> dict[str, Any]:
        return self.database.add_autopilot_decision(record)

    def get_autopilot_decisions(self, limit: int | None = None) -> list[dict[str, Any]]:
        return self.database.get_autopilot_decisions(limit=limit)

    def add_backtest_run(self, record: dict[str, Any]) -> dict[str, Any]:
        return self.database.add_backtest_run(record)

    def get_backtest_runs(self, limit: int | None = None) -> list[dict[str, Any]]:
        return self.database.get_backtest_runs(limit=limit)

    def get_last_backtest_run(self) -> dict[str, Any] | None:
        return self.database.get_last_backtest_run()

    def add_backtest_trade(self, record: dict[str, Any]) -> dict[str, Any]:
        return self.database.add_backtest_trade(record)

    def get_backtest_trades(
        self,
        run_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.database.get_backtest_trades(run_id=run_id, limit=limit)

    def save_runtime_state(self, key: str, value: Any) -> None:
        self.state[key] = value
        self.database.save_runtime_state(key, value)
        self._apply_runtime_config_key(key, value)
        self._mirror_runtime_setting(key, value)

    def get_runtime_state(self, key: str, default: Any = None) -> Any:
        if key in self.state:
            return self.state.get(key, default)
        return self.database.get_runtime_state(key, default)

    def _mirror_runtime_setting(self, key: str, value: Any) -> None:
        mapping = {
            "BYBIT_TRADING_ENABLED": "trading_enabled",
            "BYBIT_TESTNET": "testnet_mode",
            "TESTNET_AUTOPILOT_ENABLED": "autopilot_enabled",
            "TESTNET_AGGRESSIVE_MODE": "testnet_aggressive_mode",
            "SCAN_INTERVAL_SECONDS": "scan_interval_seconds",
            "TESTNET_AUTOPILOT_MAX_ACTIVE_ORDERS": "max_active_orders",
            "ACCOUNT_RISK_PERCENT": "risk_percent",
            "PAPER_ACCOUNT_BALANCE_USDT": "paper_balance",
        }
        column = mapping.get(key)
        if column:
            self.database.save_runtime_setting({column: value})

    def add_bot_message(self, record: dict[str, Any]) -> dict[str, Any]:
        return self.database.add_bot_message(record)

    def get_bot_messages(
        self,
        include_deleted: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.database.get_bot_messages(include_deleted=include_deleted, limit=limit)

    def mark_bot_message_deleted(self, record_id: str) -> None:
        self.database.mark_bot_message_deleted(record_id)

    def get_fee_settings(self) -> dict[str, Any]:
        settings = self.database.get_fee_settings() or {}
        defaults = {
            "derivatives_taker_fee_percent": config.DERIVATIVES_TAKER_FEE_PERCENT,
            "derivatives_maker_fee_percent": config.DERIVATIVES_MAKER_FEE_PERCENT,
            "spot_taker_fee_percent": config.SPOT_TAKER_FEE_PERCENT,
            "spot_maker_fee_percent": config.SPOT_MAKER_FEE_PERCENT,
            "fee_mode": config.FEE_MODE,
        }
        defaults.update({key: value for key, value in settings.items() if value is not None})
        return defaults

    def save_fee_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        current = self.get_fee_settings()
        current.update(settings)
        saved = self.database.save_fee_settings(current)
        self.apply_fee_settings(saved)
        return saved

    def apply_fee_settings(self, settings: dict[str, Any]) -> None:
        mode = str(settings.get("fee_mode") or "maker").lower()
        if mode not in {"maker", "taker", "worst_case"}:
            mode = "maker"
        config.DERIVATIVES_TAKER_FEE_PERCENT = float(settings.get("derivatives_taker_fee_percent", 0.1000))
        config.DERIVATIVES_MAKER_FEE_PERCENT = float(settings.get("derivatives_maker_fee_percent", 0.0360))
        config.SPOT_TAKER_FEE_PERCENT = float(settings.get("spot_taker_fee_percent", 0.1800))
        config.SPOT_MAKER_FEE_PERCENT = float(settings.get("spot_maker_fee_percent", 0.1000))
        config.FEE_MODE = mode
        config.DEFAULT_EXECUTION_FEE_MODE = mode
        config.BYBIT_TAKER_FEE_RATE = config.DERIVATIVES_TAKER_FEE_PERCENT / 100
        config.BYBIT_MAKER_FEE_RATE = config.DERIVATIVES_MAKER_FEE_PERCENT / 100

    def runtime_setting_source(self, key: str) -> str:
        runtime_state = self.database.get_all_runtime_state()
        return "RUNTIME_DB" if key in runtime_state else "ENV"

    def apply_runtime_config_overrides(self) -> None:
        self._apply_runtime_config_overrides(self.database.get_all_runtime_state())

    def _apply_runtime_config_overrides(self, runtime_state: dict[str, Any]) -> None:
        for key in RUNTIME_CONFIG_KEYS:
            if key in runtime_state:
                self._apply_runtime_config_key(key, runtime_state[key])

    def _apply_runtime_config_key(self, key: str, value: Any) -> None:
        if key == "BYBIT_TRADING_ENABLED":
            config.BYBIT_TRADING_ENABLED = bool(value)
        elif key == "BYBIT_TESTNET":
            config.BYBIT_TESTNET = bool(value)
        elif key == "TESTNET_AUTOPILOT_ENABLED":
            config.TESTNET_AUTOPILOT_ENABLED = bool(value)
        elif key == "TESTNET_AGGRESSIVE_MODE":
            config.TESTNET_AGGRESSIVE_MODE = bool(value)
        elif key == "SCAN_INTERVAL_SECONDS":
            config.SCAN_INTERVAL_SECONDS = int(value)
        elif key == "TESTNET_AUTOPILOT_MAX_ACTIVE_ORDERS":
            config.TESTNET_AUTOPILOT_MAX_ACTIVE_ORDERS = int(value)
        elif key == "ACCOUNT_RISK_PERCENT":
            config.ACCOUNT_RISK_PERCENT = float(value)
        elif key == "PAPER_ACCOUNT_BALANCE_USDT":
            config.PAPER_ACCOUNT_BALANCE_USDT = float(value)

    def get_setup_statistics(self) -> dict[str, Any]:
        records = self.get_setup_journal(limit=None)
        total = len(records)
        stats: dict[str, Any] = {
            "total_closed": total,
            "tp1_only": 0,
            "tp2_hit": 0,
            "invalidated_before_tp1": 0,
            "invalidated_after_tp1": 0,
            "expired_no_entry": 0,
            "expired_after_entry": 0,
            "realistic_tp1": 0,
            "fast_move_tp1": 0,
            "realistic_tp2": 0,
            "fast_move_tp2": 0,
            "enterable_now_count": 0,
            "pending_limit_count": 0,
            "too_late_count": 0,
            "no_setup_count": 0,
            "raw_average_r": 0.0,
            "fee_adjusted_average_r": 0.0,
            "by_direction": {},
            "by_alert_type": {},
        }
        raw_sum = 0.0
        net_sum = 0.0

        for record in records:
            final_state = normalize_final_state(record)
            direction = record.get("direction") or "Unknown"
            alert_type = record.get("alert_type") or "Unknown"
            execution_quality = execution_quality_for_record(record)
            raw_sum += raw_result_r_for_record(record)
            net_sum += fee_adjusted_result_r_for_record(record)

            if final_state in {"TP1_THEN_INVALIDATED", "TP1_HIT"}:
                stats["tp1_only"] += 1
                if execution_quality == "FAST_MOVE":
                    stats["fast_move_tp1"] += 1
                elif execution_quality == "REALISTIC":
                    stats["realistic_tp1"] += 1
            if final_state == "TP2_HIT":
                stats["tp2_hit"] += 1
                if execution_quality == "FAST_MOVE":
                    stats["fast_move_tp2"] += 1
                elif execution_quality == "REALISTIC":
                    stats["realistic_tp2"] += 1
            if final_state in {"INVALIDATED_BEFORE_TP1", "AMBIGUOUS_INVALIDATION_FIRST"}:
                stats["invalidated_before_tp1"] += 1
            if final_state == "TP1_THEN_INVALIDATED":
                stats["invalidated_after_tp1"] += 1
            if final_state == "EXPIRED_NO_ENTRY":
                stats["expired_no_entry"] += 1
            if final_state == "EXPIRED_AFTER_ENTRY":
                stats["expired_after_entry"] += 1

            self._increment_group_stats(stats["by_direction"], direction, final_state)
            self._increment_group_stats(stats["by_alert_type"], alert_type, final_state)

        if total > 0:
            stats["raw_average_r"] = raw_sum / total
            stats["fee_adjusted_average_r"] = net_sum / total

        for alert in self.get_alert_history(limit=None):
            execution_status = self._alert_execution_status(alert)
            if execution_status == "ENTERABLE_NOW":
                stats["enterable_now_count"] += 1
            elif execution_status == "PENDING_LIMIT_ONLY":
                stats["pending_limit_count"] += 1
            elif execution_status == "TOO_LATE_DO_NOT_CHASE":
                stats["too_late_count"] += 1
            elif execution_status == "NO_SETUP":
                stats["no_setup_count"] += 1

        return stats

    def backup_database(self) -> Path:
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        backup_path = self.database.path.with_name(f"backup_bot_state_{timestamp}.db")
        self.save()
        shutil.copy2(self.database.path, backup_path)
        return backup_path

    def _normalize(self) -> None:
        default = self.default_state()
        for key, value in default.items():
            self.state.setdefault(key, deepcopy(value))

        if not isinstance(self.state.get("history"), dict):
            self.state["history"] = {}
        if not isinstance(self.state.get("last_alerts"), dict):
            self.state["last_alerts"] = {}
        if not isinstance(self.state.get("recent_alerts"), list):
            self.state["recent_alerts"] = []
        if not isinstance(self.state.get("symbols"), list):
            self.state["symbols"] = []
        if not isinstance(self.state.get("active_setups"), list):
            self.state["active_setups"] = []
        if not isinstance(self.state.get("setup_journal"), list):
            self.state["setup_journal"] = []
        if not isinstance(self.state.get("paper_orders"), list):
            self.state["paper_orders"] = []
        if not isinstance(self.state.get("symbol_blacklist"), list):
            self.state["symbol_blacklist"] = list(config.SYMBOL_BLACKLIST)

        self.reset_alert_counter_if_needed()

    def _increment_group_stats(
        self,
        groups: dict[str, dict[str, int]],
        key: str,
        final_state: str,
    ) -> None:
        group = groups.setdefault(
            key,
            {
                "total": 0,
                "tp1_only": 0,
                "tp2_hit": 0,
                "invalidated_before_tp1": 0,
                "invalidated_after_tp1": 0,
                "expired": 0,
            },
        )
        group["total"] += 1
        if final_state in {"TP1_THEN_INVALIDATED", "TP1_HIT"}:
            group["tp1_only"] += 1
            if final_state == "TP1_THEN_INVALIDATED":
                group["invalidated_after_tp1"] += 1
        elif final_state == "TP2_HIT":
            group["tp2_hit"] += 1
        elif final_state in {"INVALIDATED_BEFORE_TP1", "AMBIGUOUS_INVALIDATION_FIRST"}:
            group["invalidated_before_tp1"] += 1
        elif final_state in {"EXPIRED_NO_ENTRY", "EXPIRED_AFTER_ENTRY"}:
            group["expired"] += 1

    def _alert_execution_status(self, alert: dict[str, Any]) -> str | None:
        status = alert.get("execution_status")
        if status in {
            "ENTERABLE_NOW",
            "PENDING_LIMIT_ONLY",
            "TOO_LATE_DO_NOT_CHASE",
            "NO_SETUP",
        }:
            return status

        short_label = str(alert.get("execution_short_label") or "").upper()
        if short_label in {"ENTERABLE", "ENTERABLE NOW"}:
            return "ENTERABLE_NOW"
        if short_label in {"LIMIT ONLY", "PENDING LIMIT ONLY"}:
            return "PENDING_LIMIT_ONLY"
        if short_label in {"TOO LATE", "DO NOT CHASE"}:
            return "TOO_LATE_DO_NOT_CHASE"
        if short_label == "NO SETUP":
            return "NO_SETUP"

        setup_status = str(alert.get("setup_status") or "").upper()
        setup_bias = str(alert.get("setup_bias") or "").upper()
        if setup_status == "NO SETUP" or setup_bias == "WAIT":
            return "NO_SETUP"
        return None

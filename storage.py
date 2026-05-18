import logging
import shutil
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import config
from database import BotDatabase
from setup_tracker import normalize_final_state


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
            "last_setup_tracking_ts": 0,
        }

    def load(self) -> dict[str, Any]:
        state = self.default_state()
        runtime_state = self.database.get_all_runtime_state()
        for key, value in runtime_state.items():
            if key.startswith("sqlite_"):
                continue
            state[key] = value

        state["recent_alerts"] = self.database.get_alert_history(limit=config.RECENT_ALERTS_LIMIT)
        state["active_setups"] = self.database.get_active_setups()
        state["setup_journal"] = self.database.get_setup_journal(limit=config.SETUP_JOURNAL_MAX_RECORDS)
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

    def save_runtime_state(self, key: str, value: Any) -> None:
        self.state[key] = value
        self.database.save_runtime_state(key, value)

    def get_runtime_state(self, key: str, default: Any = None) -> Any:
        if key in self.state:
            return self.state.get(key, default)
        return self.database.get_runtime_state(key, default)

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
            "by_direction": {},
            "by_alert_type": {},
        }

        for record in records:
            final_state = normalize_final_state(record)
            direction = record.get("direction") or "Unknown"
            alert_type = record.get("alert_type") or "Unknown"

            if final_state in {"TP1_THEN_INVALIDATED", "TP1_HIT"}:
                stats["tp1_only"] += 1
            if final_state == "TP2_HIT":
                stats["tp2_hit"] += 1
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

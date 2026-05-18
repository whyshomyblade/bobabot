import json
import logging
import shutil
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class BotDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.logger = logging.getLogger(self.__class__.__name__)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self.connection:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS alert_history (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT,
                    symbol TEXT,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS active_setups (
                    id TEXT PRIMARY KEY,
                    symbol TEXT,
                    state TEXT,
                    data TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS setup_journal (
                    id TEXT PRIMARY KEY,
                    symbol TEXT,
                    final_state TEXT,
                    closed_at TEXT,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def close(self) -> None:
        self.connection.close()

    def get_runtime_state(self, key: str, default: Any = None) -> Any:
        row = self.connection.execute(
            "SELECT value FROM bot_state WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return default
        return self._loads(row["value"], default)

    def get_all_runtime_state(self) -> dict[str, Any]:
        rows = self.connection.execute("SELECT key, value FROM bot_state").fetchall()
        return {
            row["key"]: self._loads(row["value"], None)
            for row in rows
        }

    def save_runtime_state(self, key: str, value: Any) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO bot_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, self._dumps(value), self._now()),
            )

    def get_alert_history(self, limit: int | None = None) -> list[dict[str, Any]]:
        query = "SELECT data FROM alert_history ORDER BY rowid"
        params: tuple[Any, ...] = ()
        if limit is not None and limit > 0:
            query = "SELECT data FROM alert_history ORDER BY rowid DESC LIMIT ?"
            params = (limit,)

        rows = self.connection.execute(query, params).fetchall()
        records = [self._loads(row["data"], {}) for row in rows]
        if limit is not None and limit > 0:
            records.reverse()
        return [record for record in records if isinstance(record, dict)]

    def add_alert_record(self, record: dict[str, Any]) -> None:
        record_id = str(record.get("id") or self._record_id("alert", record))
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO alert_history (id, timestamp, symbol, data, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    record.get("timestamp"),
                    record.get("symbol"),
                    self._dumps(record),
                    self._now(),
                ),
            )

    def replace_active_setups(self, setups: list[dict[str, Any]]) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM active_setups")
            for setup in setups:
                self.upsert_active_setup(setup, commit=False)

    def get_active_setups(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT data FROM active_setups ORDER BY rowid"
        ).fetchall()
        return [
            record
            for record in (self._loads(row["data"], {}) for row in rows)
            if isinstance(record, dict)
        ]

    def upsert_active_setup(self, setup: dict[str, Any], commit: bool = True) -> None:
        setup_id = str(setup.get("id") or self._record_id("setup", setup))
        setup["id"] = setup_id
        sql = """
            INSERT INTO active_setups (id, symbol, state, data, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                symbol = excluded.symbol,
                state = excluded.state,
                data = excluded.data,
                updated_at = excluded.updated_at
        """
        params = (
            setup_id,
            setup.get("symbol"),
            setup.get("state"),
            self._dumps(setup),
            self._now(),
        )
        if commit:
            with self.connection:
                self.connection.execute(sql, params)
        else:
            self.connection.execute(sql, params)

    def remove_active_setup(self, setup_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT data FROM active_setups WHERE id = ?",
            (setup_id,),
        ).fetchone()
        if row is None:
            return None
        with self.connection:
            self.connection.execute("DELETE FROM active_setups WHERE id = ?", (setup_id,))
        record = self._loads(row["data"], {})
        return record if isinstance(record, dict) else None

    def get_setup_journal(self, limit: int | None = None) -> list[dict[str, Any]]:
        query = "SELECT data FROM setup_journal ORDER BY rowid"
        params: tuple[Any, ...] = ()
        if limit is not None and limit > 0:
            query = "SELECT data FROM setup_journal ORDER BY rowid DESC LIMIT ?"
            params = (limit,)

        rows = self.connection.execute(query, params).fetchall()
        records = [self._loads(row["data"], {}) for row in rows]
        if limit is not None and limit > 0:
            records.reverse()
        return [record for record in records if isinstance(record, dict)]

    def add_setup_journal_record(self, record: dict[str, Any]) -> None:
        record_id = str(record.get("id") or self._record_id("journal", record))
        record["id"] = record_id
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO setup_journal (id, symbol, final_state, closed_at, data, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    record.get("symbol"),
                    record.get("final_state") or record.get("state"),
                    record.get("closed_at"),
                    self._dumps(record),
                    self._now(),
                ),
            )

    def trim_setup_journal(self, max_records: int) -> None:
        with self.connection:
            self.connection.execute(
                """
                DELETE FROM setup_journal
                WHERE rowid NOT IN (
                    SELECT rowid FROM setup_journal ORDER BY rowid DESC LIMIT ?
                )
                """,
                (max_records,),
            )

    def count_alert_history(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM alert_history").fetchone()[0])

    def count_active_setups(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM active_setups").fetchone()[0])

    def count_setup_journal(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM setup_journal").fetchone()[0])

    def migrate_from_state_json(self, state_path: str | Path) -> None:
        if self.get_runtime_state("sqlite_migration_completed", False):
            return

        state_path = Path(state_path)
        if not state_path.exists():
            self.save_runtime_state("sqlite_migration_completed", True)
            return

        try:
            with state_path.open("r", encoding="utf-8") as file:
                state = json.load(file)
        except (json.JSONDecodeError, OSError) as exc:
            self.logger.error("Could not migrate %s to SQLite: %s", state_path, exc)
            self.save_runtime_state("sqlite_migration_completed", True)
            return

        backup_path = state_path.with_name("state.backup.json")
        if not backup_path.exists():
            shutil.copy2(state_path, backup_path)
            self.logger.info("Created JSON state backup: %s", backup_path)

        if not isinstance(state, dict):
            self.save_runtime_state("sqlite_migration_completed", True)
            return

        active_setups = state.get("active_setups") if isinstance(state.get("active_setups"), list) else []
        setup_journal = state.get("setup_journal") if isinstance(state.get("setup_journal"), list) else []
        alert_history = state.get("alert_history")
        if not isinstance(alert_history, list):
            alert_history = state.get("recent_alerts") if isinstance(state.get("recent_alerts"), list) else []

        for key, value in state.items():
            if key in {"active_setups", "setup_journal", "alert_history", "recent_alerts"}:
                continue
            self.save_runtime_state(key, value)

        for record in alert_history:
            if isinstance(record, dict):
                self.add_alert_record(record)
        self.replace_active_setups([item for item in active_setups if isinstance(item, dict)])
        for record in setup_journal:
            if isinstance(record, dict):
                self.add_setup_journal_record(record)
        self.trim_setup_journal(500)
        self.save_runtime_state("sqlite_migration_completed", True)
        self.logger.info(
            "Migrated JSON state to SQLite: alerts=%s active_setups=%s journal=%s",
            len(alert_history),
            len(active_setups),
            len(setup_journal),
        )

    def _record_id(self, prefix: str, record: dict[str, Any]) -> str:
        timestamp = record.get("timestamp") or record.get("created_at") or record.get("closed_at")
        symbol = record.get("symbol")
        if timestamp and symbol:
            raw = re_safe(f"{prefix}-{symbol}-{timestamp}")
            return raw
        return f"{prefix}-{uuid.uuid4().hex}"

    def _dumps(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    def _loads(self, value: str, default: Any) -> Any:
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()


def re_safe(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)

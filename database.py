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
        self._backup_before_phase8_5_schema()
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
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_orders (
                    id TEXT PRIMARY KEY,
                    symbol TEXT,
                    side TEXT,
                    status TEXT,
                    source_setup_id TEXT,
                    mode TEXT,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS api_credentials (
                    id TEXT PRIMARY KEY,
                    api_key TEXT NOT NULL,
                    api_secret TEXT NOT NULL,
                    masked_key TEXT,
                    mode TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS fee_settings (
                    id TEXT PRIMARY KEY,
                    derivatives_taker_fee_percent REAL NOT NULL,
                    derivatives_maker_fee_percent REAL NOT NULL,
                    spot_taker_fee_percent REAL NOT NULL,
                    spot_maker_fee_percent REAL NOT NULL,
                    fee_mode TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS backtest_runs (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    symbols TEXT,
                    days INTEGER,
                    interval TEXT,
                    config_snapshot_json TEXT,
                    total_trades INTEGER,
                    net_r REAL,
                    raw_r REAL,
                    notes TEXT,
                    data TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS backtest_trades (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    symbol TEXT,
                    side TEXT,
                    alert_type TEXT,
                    risk_level TEXT,
                    execution_status TEXT,
                    execution_quality TEXT,
                    entry_price REAL,
                    stop_price REAL,
                    tp1 REAL,
                    tp2 REAL,
                    result_type TEXT,
                    raw_r REAL,
                    net_r REAL,
                    opened_at TEXT,
                    closed_at TEXT,
                    close_reason TEXT,
                    data_quality TEXT,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS autopilot_decisions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    symbol TEXT,
                    side TEXT,
                    alert_type TEXT,
                    risk_level TEXT,
                    execution_quality TEXT,
                    decision TEXT,
                    reason TEXT,
                    order_id TEXT,
                    setup_id TEXT,
                    mode TEXT,
                    data TEXT NOT NULL
                )
                """
            )
            self._ensure_paper_order_columns()

    def close(self) -> None:
        self.connection.close()

    def _backup_before_phase8_5_schema(self) -> Path | None:
        if not self.path.exists():
            return None
        if any(self.path.parent.glob("backup_before_phase8_5_*.db")):
            return None

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        backup_path = self.path.with_name(f"backup_before_phase8_5_{timestamp}.db")
        shutil.copy2(self.path, backup_path)
        self.logger.info("Created Phase 8.5 DB backup: %s", backup_path)
        return backup_path

    def _ensure_paper_order_columns(self) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(paper_orders)").fetchall()
        }
        migrations = {
            "exchange_order_id": "TEXT",
            "order_link_id": "TEXT",
            "entry_price": "REAL",
            "stop_price": "REAL",
            "tp1": "REAL",
            "tp2": "REAL",
            "qty": "TEXT",
            "position_size_usdt": "REAL",
            "risk_usdt": "REAL",
            "submitted_at": "TEXT",
            "filled_at": "TEXT",
            "cancelled_at": "TEXT",
            "closed_at": "TEXT",
            "last_sync_at": "TEXT",
            "raw_response_json": "TEXT",
            "last_error": "TEXT",
            "notes": "TEXT",
        }
        for name, column_type in migrations.items():
            if name in columns:
                continue
            self.connection.execute(
                f"ALTER TABLE paper_orders ADD COLUMN {name} {column_type}"
            )
            self.logger.info("Added paper_orders.%s column", name)

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

    def add_paper_order(self, record: dict[str, Any]) -> dict[str, Any]:
        record_id = str(record.get("id") or self._record_id("paper_order", record))
        now = self._now()
        record["id"] = record_id
        record.setdefault("created_at", now)
        record["updated_at"] = now
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO paper_orders
                    (id, symbol, side, status, source_setup_id, mode, data, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    record.get("symbol"),
                    record.get("side"),
                    record.get("status"),
                    record.get("source_setup_id"),
                    record.get("mode"),
                    self._dumps(record),
                    record.get("created_at"),
                    record.get("updated_at"),
                ),
            )
            self._sync_paper_order_columns(record_id, record)
        return record

    def update_paper_order(self, order_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        record = self.get_paper_order(order_id)
        if record is None:
            return None
        record.update(updates)
        record["updated_at"] = self._now()
        with self.connection:
            self.connection.execute(
                """
                UPDATE paper_orders
                SET symbol = ?, side = ?, status = ?, source_setup_id = ?, mode = ?, data = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    record.get("symbol"),
                    record.get("side"),
                    record.get("status"),
                    record.get("source_setup_id"),
                    record.get("mode"),
                    self._dumps(record),
                    record.get("updated_at"),
                    order_id,
                ),
            )
            self._sync_paper_order_columns(order_id, record)
        return record

    def _sync_paper_order_columns(self, order_id: str, record: dict[str, Any]) -> None:
        self.connection.execute(
            """
            UPDATE paper_orders
            SET
                exchange_order_id = ?,
                order_link_id = ?,
                entry_price = ?,
                stop_price = ?,
                tp1 = ?,
                tp2 = ?,
                qty = ?,
                position_size_usdt = ?,
                risk_usdt = ?,
                submitted_at = ?,
                filled_at = ?,
                cancelled_at = ?,
                closed_at = ?,
                last_sync_at = ?,
                raw_response_json = ?,
                last_error = ?,
                notes = ?
            WHERE id = ?
            """,
            (
                record.get("exchange_order_id"),
                record.get("order_link_id"),
                self._optional_float(record.get("entry_price")),
                self._optional_float(record.get("stop_price")),
                self._optional_float(record.get("tp1")),
                self._optional_float(record.get("tp2")),
                None if record.get("qty") is None else str(record.get("qty")),
                self._optional_float(record.get("position_size_usdt")),
                self._optional_float(record.get("risk_usdt")),
                record.get("submitted_at"),
                record.get("filled_at"),
                record.get("cancelled_at"),
                record.get("closed_at"),
                record.get("last_sync_at"),
                self._dumps(record.get("raw_response_json") or record.get("exchange_response"))
                if record.get("raw_response_json") is not None or record.get("exchange_response") is not None
                else None,
                record.get("last_error") or record.get("reject_reason"),
                self._dumps(record.get("notes", [])) if record.get("notes") is not None else None,
                order_id,
            ),
        )

    def get_paper_order(self, order_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT data FROM paper_orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        if row is None:
            return None
        record = self._loads(row["data"], {})
        return record if isinstance(record, dict) else None

    def get_paper_orders(
        self,
        statuses: list[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT data FROM paper_orders"
        params: list[Any] = []
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            query += f" WHERE status IN ({placeholders})"
            params.extend(statuses)
        query += " ORDER BY rowid DESC"
        if limit is not None and limit > 0:
            query += " LIMIT ?"
            params.append(limit)

        rows = self.connection.execute(query, tuple(params)).fetchall()
        records = [self._loads(row["data"], {}) for row in rows]
        return [record for record in records if isinstance(record, dict)]

    def count_paper_orders(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0])

    def add_autopilot_decision(self, record: dict[str, Any]) -> dict[str, Any]:
        record_id = str(record.get("id") or f"auto_dec_{uuid.uuid4().hex}")
        created_at = str(record.get("created_at") or self._now())
        record["id"] = record_id
        record["created_at"] = created_at
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO autopilot_decisions
                    (id, created_at, symbol, side, alert_type, risk_level,
                     execution_quality, decision, reason, order_id, setup_id, mode, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    created_at,
                    record.get("symbol"),
                    record.get("side"),
                    record.get("alert_type"),
                    record.get("risk_level"),
                    record.get("execution_quality"),
                    record.get("decision"),
                    record.get("reason"),
                    record.get("order_id"),
                    record.get("setup_id"),
                    record.get("mode"),
                    self._dumps(record),
                ),
            )
        return record

    def get_autopilot_decisions(
        self,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT data FROM autopilot_decisions ORDER BY rowid DESC"
        params: list[Any] = []
        if limit is not None and limit > 0:
            query += " LIMIT ?"
            params.append(limit)
        rows = self.connection.execute(query, tuple(params)).fetchall()
        records = [self._loads(row["data"], {}) for row in rows]
        return [record for record in records if isinstance(record, dict)]

    def count_autopilot_decisions(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM autopilot_decisions").fetchone()[0])

    def get_fee_settings(self) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT derivatives_taker_fee_percent, derivatives_maker_fee_percent,
                   spot_taker_fee_percent, spot_maker_fee_percent, fee_mode, updated_at
            FROM fee_settings
            WHERE id = 'active'
            """
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def save_fee_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        now = self._now()
        saved = {
            "derivatives_taker_fee_percent": float(settings.get("derivatives_taker_fee_percent", 0.0)),
            "derivatives_maker_fee_percent": float(settings.get("derivatives_maker_fee_percent", 0.0)),
            "spot_taker_fee_percent": float(settings.get("spot_taker_fee_percent", 0.0)),
            "spot_maker_fee_percent": float(settings.get("spot_maker_fee_percent", 0.0)),
            "fee_mode": str(settings.get("fee_mode") or "maker"),
            "updated_at": now,
        }
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO fee_settings
                    (id, derivatives_taker_fee_percent, derivatives_maker_fee_percent,
                     spot_taker_fee_percent, spot_maker_fee_percent, fee_mode, updated_at)
                VALUES ('active', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    derivatives_taker_fee_percent = excluded.derivatives_taker_fee_percent,
                    derivatives_maker_fee_percent = excluded.derivatives_maker_fee_percent,
                    spot_taker_fee_percent = excluded.spot_taker_fee_percent,
                    spot_maker_fee_percent = excluded.spot_maker_fee_percent,
                    fee_mode = excluded.fee_mode,
                    updated_at = excluded.updated_at
                """,
                (
                    saved["derivatives_taker_fee_percent"],
                    saved["derivatives_maker_fee_percent"],
                    saved["spot_taker_fee_percent"],
                    saved["spot_maker_fee_percent"],
                    saved["fee_mode"],
                    now,
                ),
            )
        return saved

    def save_api_credentials(
        self,
        api_key: str,
        api_secret: str,
        masked_key: str,
        mode: str = "TESTNET",
    ) -> dict[str, Any]:
        credential_id = f"api_{uuid.uuid4().hex}"
        now = self._now()
        mode = "MAINNET" if str(mode).upper() == "MAINNET" else "TESTNET"
        with self.connection:
            self.connection.execute("UPDATE api_credentials SET active = 0, updated_at = ?", (now,))
            self.connection.execute(
                """
                INSERT INTO api_credentials
                    (id, api_key, api_secret, masked_key, mode, created_at, updated_at, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (credential_id, api_key, api_secret, masked_key, mode, now, now),
            )
        return {
            "id": credential_id,
            "api_key": api_key,
            "api_secret": api_secret,
            "masked_key": masked_key,
            "mode": mode,
            "created_at": now,
            "updated_at": now,
            "active": True,
        }

    def get_active_api_credentials(self) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT id, api_key, api_secret, masked_key, mode, created_at, updated_at, active
            FROM api_credentials
            WHERE active = 1
            ORDER BY rowid DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "api_key": row["api_key"],
            "api_secret": row["api_secret"],
            "masked_key": row["masked_key"],
            "mode": row["mode"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "active": bool(row["active"]),
        }

    def clear_api_credentials(self) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE api_credentials SET active = 0, updated_at = ? WHERE active = 1",
                (self._now(),),
            )

    def update_active_api_mode(self, mode: str) -> dict[str, Any] | None:
        credential = self.get_active_api_credentials()
        if credential is None:
            return None
        mode = "MAINNET" if str(mode).upper() == "MAINNET" else "TESTNET"
        with self.connection:
            self.connection.execute(
                "UPDATE api_credentials SET mode = ?, updated_at = ? WHERE id = ?",
                (mode, self._now(), credential["id"]),
            )
        credential["mode"] = mode
        return credential

    def add_backtest_run(self, record: dict[str, Any]) -> dict[str, Any]:
        record_id = str(record.get("id") or self._record_id("backtest_run", record))
        created_at = str(record.get("created_at") or self._now())
        record["id"] = record_id
        record["created_at"] = created_at
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO backtest_runs
                    (id, created_at, symbols, days, interval, config_snapshot_json,
                     total_trades, net_r, raw_r, notes, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    created_at,
                    ",".join(str(symbol) for symbol in (record.get("symbols") or [])),
                    record.get("days"),
                    record.get("interval"),
                    self._dumps(record.get("config_snapshot") or {}),
                    record.get("total_trades", 0),
                    record.get("net_r", 0.0),
                    record.get("raw_r", 0.0),
                    "\n".join(record.get("notes") or []),
                    self._dumps(record),
                ),
            )
        return record

    def get_backtest_runs(self, limit: int | None = None) -> list[dict[str, Any]]:
        query = "SELECT data FROM backtest_runs ORDER BY rowid DESC"
        params: tuple[Any, ...] = ()
        if limit is not None and limit > 0:
            query += " LIMIT ?"
            params = (limit,)
        rows = self.connection.execute(query, params).fetchall()
        return [
            record
            for record in (self._loads(row["data"], {}) for row in rows)
            if isinstance(record, dict)
        ]

    def get_last_backtest_run(self) -> dict[str, Any] | None:
        runs = self.get_backtest_runs(limit=1)
        return runs[0] if runs else None

    def add_backtest_trade(self, record: dict[str, Any]) -> dict[str, Any]:
        record_id = str(record.get("id") or self._record_id("backtest_trade", record))
        record["id"] = record_id
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO backtest_trades
                    (id, run_id, symbol, side, alert_type, risk_level, execution_status,
                     execution_quality, entry_price, stop_price, tp1, tp2, result_type,
                     raw_r, net_r, opened_at, closed_at, close_reason, data_quality,
                     data, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    record.get("run_id"),
                    record.get("symbol"),
                    record.get("side"),
                    record.get("alert_type"),
                    record.get("risk_level"),
                    record.get("execution_status"),
                    record.get("execution_quality"),
                    record.get("entry_price"),
                    record.get("stop_price"),
                    record.get("tp1"),
                    record.get("tp2"),
                    record.get("result_type"),
                    record.get("raw_r"),
                    record.get("net_r"),
                    record.get("opened_at"),
                    record.get("closed_at"),
                    record.get("close_reason"),
                    record.get("data_quality"),
                    self._dumps(record),
                    self._now(),
                ),
            )
        return record

    def get_backtest_trades(
        self,
        run_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT data FROM backtest_trades"
        params: list[Any] = []
        if run_id:
            query += " WHERE run_id = ?"
            params.append(run_id)
        query += " ORDER BY rowid DESC"
        if limit is not None and limit > 0:
            query += " LIMIT ?"
            params.append(limit)
        rows = self.connection.execute(query, tuple(params)).fetchall()
        records = [self._loads(row["data"], {}) for row in rows]
        records = [record for record in records if isinstance(record, dict)]
        records.reverse()
        return records

    def count_backtest_trades(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM backtest_trades").fetchone()[0])

    def backup_before_phase7(self) -> Path | None:
        if self.get_runtime_state("phase7_backup_completed", False):
            return None
        if not self.path.exists():
            self.save_runtime_state("phase7_backup_completed", True)
            return None

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        backup_path = self.path.with_name(f"backup_before_phase7_{timestamp}.db")
        shutil.copy2(self.path, backup_path)
        self.save_runtime_state("phase7_backup_completed", True)
        self.logger.info("Created Phase 7 DB backup: %s", backup_path)
        return backup_path

    def backup_before_phase8(self) -> Path | None:
        if self.get_runtime_state("phase8_backup_completed", False):
            return None
        if not self.path.exists():
            self.save_runtime_state("phase8_backup_completed", True)
            return None

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        backup_path = self.path.with_name(f"backup_before_phase8_{timestamp}.db")
        shutil.copy2(self.path, backup_path)
        self.save_runtime_state("phase8_backup_completed", True)
        self.logger.info("Created Phase 8 DB backup: %s", backup_path)
        return backup_path

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

    def _optional_float(self, value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()


def re_safe(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)

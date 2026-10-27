"""SQLite persistence for selected markets, settings and trades.

All access happens on the asyncio event-loop thread, so one connection is enough.
"""

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from .strategy import Strategy

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS markets (
    series TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,
    enabled INTEGER NOT NULL DEFAULT 0,
    strategy TEXT NOT NULL,
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series TEXT NOT NULL,
    event_symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    contract_symbol TEXT NOT NULL,
    quantity TEXT NOT NULL,
    limit_price TEXT NOT NULL,
    filled_quantity TEXT NOT NULL DEFAULT '0',
    avg_price TEXT,
    est_fees TEXT NOT NULL DEFAULT '0',
    order_id TEXT,
    order_status TEXT NOT NULL,
    seconds_left INTEGER,
    close_time TEXT NOT NULL,
    placed_at TEXT NOT NULL,
    result TEXT NOT NULL DEFAULT 'PENDING',
    pnl TEXT,
    settled_at TEXT,
    note TEXT,
    -- one entry per market per 15-minute window
    UNIQUE (series, event_symbol)
);
"""

# Results that are final; everything else is still being tracked.
FINAL_RESULTS = ("WON", "LOST", "VOID", "UNFILLED", "REJECTED", "ERROR")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._drop_paper_trading()
        self.db.commit()

    def _drop_paper_trading(self) -> None:
        """Databases from before paper mode was removed: drop simulated trades and the mode column."""
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(trades)")}
        if "mode" in columns:
            self.db.execute("DELETE FROM trades WHERE mode != 'LIVE'")
            self.db.execute("ALTER TABLE trades DROP COLUMN mode")
        self.db.execute("DELETE FROM settings WHERE key = 'mode'")

    # --- settings ---------------------------------------------------------

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: Optional[str]) -> None:
        if value is None:
            self.db.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            self.db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        self.db.commit()

    # --- markets ----------------------------------------------------------

    def list_markets(self) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM markets ORDER BY added_at").fetchall()
        return [self._market(row) for row in rows]

    def get_market(self, series: str) -> Optional[dict[str, Any]]:
        row = self.db.execute("SELECT * FROM markets WHERE series = ?", (series,)).fetchone()
        return self._market(row) if row else None

    def add_market(self, series: str, name: str, category: Optional[str]) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO markets (series, name, category, enabled, strategy, added_at) "
            "VALUES (?, ?, ?, 0, ?, ?)",
            (series, name, category, Strategy().model_dump_json(), now_iso()),
        )
        self.db.commit()

    def remove_market(self, series: str) -> None:
        self.db.execute("DELETE FROM markets WHERE series = ?", (series,))
        self.db.commit()

    def update_market(
        self, series: str, *, enabled: Optional[bool] = None, strategy: Optional[Strategy] = None
    ) -> None:
        if enabled is not None:
            self.db.execute("UPDATE markets SET enabled = ? WHERE series = ?", (int(enabled), series))
        if strategy is not None:
            self.db.execute(
                "UPDATE markets SET strategy = ? WHERE series = ?", (strategy.model_dump_json(), series)
            )
        self.db.commit()

    @staticmethod
    def _market(row: sqlite3.Row) -> dict[str, Any]:
        market = dict(row)
        market["enabled"] = bool(market["enabled"])
        market["strategy"] = Strategy.model_validate(json.loads(market["strategy"]))
        return market

    # --- trades -----------------------------------------------------------

    def has_trade(self, series: str, event_symbol: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM trades WHERE series = ? AND event_symbol = ?", (series, event_symbol)
        ).fetchone()
        return row is not None

    def insert_trade(self, **fields: Any) -> int:
        fields.setdefault("placed_at", now_iso())
        columns = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        cursor = self.db.execute(
            f"INSERT INTO trades ({columns}) VALUES ({marks})", [_db(v) for v in fields.values()]
        )
        self.db.commit()
        return int(cursor.lastrowid)

    def update_trade(self, trade_id: int, **fields: Any) -> None:
        assignments = ", ".join(f"{name} = ?" for name in fields)
        self.db.execute(
            f"UPDATE trades SET {assignments} WHERE id = ?", [*(_db(v) for v in fields.values()), trade_id]
        )
        self.db.commit()

    def trades(self, series: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT * FROM trades"
        params: list[Any] = []
        if series:
            sql += " WHERE series = ?"
            params.append(series)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [dict(row) for row in self.db.execute(sql, params).fetchall()]

    def trade_for(self, series: str, event_symbol: str) -> Optional[dict[str, Any]]:
        """The market's trade in one window (there is at most one)."""
        row = self.db.execute(
            "SELECT * FROM trades WHERE series = ? AND event_symbol = ?", (series, event_symbol)
        ).fetchone()
        return dict(row) if row else None

    def open_trade(self, series: str) -> Optional[dict[str, Any]]:
        """The market's most recent trade that hasn't settled yet, if any."""
        row = self.db.execute(
            "SELECT * FROM trades WHERE series = ? AND result = 'PENDING' ORDER BY id DESC LIMIT 1", (series,)
        ).fetchone()
        return dict(row) if row else None

    def trades_to_track(self) -> list[dict[str, Any]]:
        """Trades whose order or settlement is still outstanding."""
        marks = ", ".join("?" for _ in FINAL_RESULTS)
        rows = self.db.execute(f"SELECT * FROM trades WHERE result NOT IN ({marks})", FINAL_RESULTS)
        return [dict(row) for row in rows.fetchall()]

    def stats(self) -> dict[str, dict[str, Any]]:
        """Per-market performance, keyed by series."""
        out: dict[str, dict[str, Any]] = {}
        rows = self.db.execute("SELECT series, result, pnl, filled_quantity, avg_price FROM trades")
        for row in rows.fetchall():
            s = out.setdefault(
                row["series"],
                {"trades": 0, "wins": 0, "losses": 0, "pending": 0, "pnl": Decimal(0), "cost": Decimal(0)},
            )
            result = row["result"]
            if result in ("UNFILLED", "REJECTED", "ERROR"):
                continue
            s["trades"] += 1
            if result == "WON":
                s["wins"] += 1
            elif result == "LOST":
                s["losses"] += 1
            elif result == "PENDING":
                s["pending"] += 1
            if row["pnl"] is not None:
                s["pnl"] += Decimal(row["pnl"])
            if row["avg_price"] is not None:
                s["cost"] += Decimal(row["filled_quantity"]) * Decimal(row["avg_price"])
        return out


def _db(value: Any) -> Any:
    return str(value) if isinstance(value, Decimal) else value

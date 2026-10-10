"""SQLite access: open connections and create the schema."""

import sqlite3
from pathlib import Path

from app import config


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a connection with foreign keys enabled and rows accessible by name."""
    path = Path(db_path) if db_path is not None else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables (idempotent) and insert the fictitious production lines."""
    conn.executescript(config.SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.executemany(
        "INSERT OR IGNORE INTO production_line (code, name) VALUES (?, ?)",
        config.PRODUCTION_LINES,
    )
    conn.commit()


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row["name"] for row in rows}

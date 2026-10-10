import sqlite3

import pytest

from app import config, db

EXPECTED_TABLES = {
    "production_line",
    "batch",
    "part",
    "gt_defect",
    "inspection",
    "detection",
    "review",
    "review_change",
}


def test_all_tables_created(conn):
    assert db.table_names(conn) == EXPECTED_TABLES


def test_init_db_is_idempotent(conn):
    db.init_db(conn)
    assert db.table_names(conn) == EXPECTED_TABLES
    assert conn.execute("SELECT COUNT(*) FROM production_line").fetchone()[0] == len(config.PRODUCTION_LINES)


def test_production_lines_seeded(conn):
    codes = [row["code"] for row in conn.execute("SELECT code FROM production_line ORDER BY code")]
    assert codes == ["L1", "L2", "L3"]


def test_review_has_no_duration_column(conn):
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(review)")}
    assert columns == {"id", "inspection_id", "decision", "final_verdict", "comment", "reviewed_at"}


def test_foreign_keys_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO batch (code, line_id, produced_on) VALUES ('CH-X', 999, '2026-10-09')"
        )


def test_verdict_check_constraint(conn):
    conn.execute("INSERT INTO batch (code, line_id, produced_on) VALUES ('CH-1', 1, '2026-10-09')")
    conn.execute(
        "INSERT INTO part (serial, batch_id, image_path, created_at) "
        "VALUES ('GT-0000001', 1, 'x.png', '2026-10-09T08:00:00')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO inspection (part_id, inspected_at, detector_version, ai_verdict, ai_reason, status) "
            "VALUES (1, '2026-10-09T08:00:00', 'mock-0.1', 'MAYBE', '-', 'AUTO_DONE')"
        )

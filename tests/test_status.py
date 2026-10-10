import ast
import re
from pathlib import Path

import pytest

from app import config, db, repository

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "ai_verdict, has_review, expected",
    [
        ("OK", False, "AUTO_DONE"),
        ("NOK", False, "PENDING_REVIEW"),
        ("MANUAL", False, "PENDING_REVIEW"),
        ("NOK", True, "REVIEWED"),
        ("MANUAL", True, "REVIEWED"),
    ],
)
def test_inspection_status(ai_verdict, has_review, expected):
    assert repository.inspection_status(ai_verdict, has_review) == expected


@pytest.mark.parametrize(
    "ai_verdict, review_verdict, expected",
    [
        ("OK", None, "OK"),
        ("NOK", None, "NOK"),
        ("MANUAL", None, None),       # open until Thomas decides
        ("MANUAL", "OK", "OK"),
        ("MANUAL", "NOK", "NOK"),
        ("NOK", "OK", "OK"),          # corrected false alarm
        ("NOK", "NOK", "NOK"),
    ],
)
def test_final_verdict(ai_verdict, review_verdict, expected):
    assert repository.final_verdict(ai_verdict, review_verdict) == expected


def test_sql_functions_match_python(tmp_path):
    conn = repository.register_functions(db.connect(tmp_path / "f.db"))
    try:
        for ai in ("OK", "NOK", "MANUAL"):
            for review in (None, "OK", "NOK"):
                row = conn.execute(
                    "SELECT inspection_status(?, ? IS NOT NULL), final_verdict(?, ?)", (ai, review, ai, review)
                ).fetchone()
                assert row[0] == repository.inspection_status(ai, review is not None)
                assert row[1] == repository.final_verdict(ai, review)
    finally:
        conn.close()


def test_manual_part_with_review_through_the_api(tmp_path, monkeypatch):
    """MANUAL with review: status REVIEWED and the reviewer's verdict, also in the history."""
    from fastapi.testclient import TestClient

    from app.main import app
    from tools import generate_images as gen

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "s.db")
    conn = db.connect()
    db.init_db(conn)
    gen.write_database(conn, config.DEMO_SEED, gen.build_labels(60, config.DEMO_SEED))
    conn.close()
    with TestClient(app) as client:
        while not client.post("/api/station/next").json()["done"]:
            pass
        manual = next(q for q in client.get("/api/station/state").json()["queue"] if q["ai_verdict"] == "MANUAL")
        before = client.get(f"/api/station/inspections/{manual['id']}").json()
        assert (before["status"], before["final_verdict"]) == ("PENDING_REVIEW", None)
        client.post(f"/api/station/inspections/{manual['id']}/review", json={"decision": "CONFIRMED", "final_verdict": "OK"})
        after = client.get(f"/api/station/inspections/{manual['id']}").json()
        assert (after["status"], after["final_verdict"]) == ("REVIEWED", "OK")

    conn = db.connect()
    try:
        rows = repository.history(conn, scope="all", status="REVIEWED")["rows"]
    finally:
        conn.close()
    assert [(r["serial"], r["final_verdict"], r["status"]) for r in rows] == [(manual["serial"], "OK", "REVIEWED")]


# --- No query reads inspection.status -------------------------------------------------

SQL_HINT = re.compile(r"\b(SELECT|WHERE|UPDATE|ORDER BY|GROUP BY)\b")


def sql_strings(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and SQL_HINT.search(node.value):
            yield node.lineno, node.value


def test_no_query_reads_the_status_column():
    offenders = []
    for path in [*(ROOT / "app").rglob("*.py"), *(ROOT / "tools").rglob("*.py")]:
        for lineno, sql in sql_strings(path):
            cleaned = sql.replace("inspection_status", "").replace("AS status", "")
            if re.search(r"\bstatus\b", cleaned):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {sql.strip()}")
    assert offenders == []


def test_scan_would_find_a_status_read(tmp_path):
    sample = tmp_path / "bad.py"
    sample.write_text('q = "SELECT id FROM inspection i WHERE i.status = \'AUTO_DONE\'"\n', encoding="utf-8")
    assert any("status" in sql for _, sql in sql_strings(sample))

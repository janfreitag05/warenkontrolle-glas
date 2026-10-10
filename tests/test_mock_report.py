from app import config, db
from app.detector import MockDetector, part_from_label
from tools import generate_images as gen
from tools import mock_report

SEED = 42


def test_report_lists_every_part_and_writes_nothing(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "report.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    labels = gen.build_labels(60, SEED)
    conn = db.connect(db_path)
    db.init_db(conn)
    gen.write_database(conn, SEED, labels)
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("inspection", "detection")}
    conn.close()

    mock_report.main([])
    out = capsys.readouterr().out

    for label in labels:
        assert label["serial"] in out
    assert "Teile: 60" in out
    for code in config.INJECTED_ERRORS:
        assert code in out

    conn = db.connect(db_path)
    after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("inspection", "detection")}
    conn.close()
    assert before == after == {"inspection": 0, "detection": 0}


def test_rows_cover_all_verdicts_consistently():
    parts = [part_from_label(label) for label in gen.build_labels(60, SEED)]
    rows = mock_report.build_rows(parts, MockDetector())
    assert len(rows) == 60
    assert {r["verdict"] for r in rows} <= set(mock_report.VERDICT_ORDER)
    for r in rows:
        if not r["errors"]:
            # without injected errors the mock reproduces the target verdict
            assert r["verdict"] == r["target"], r

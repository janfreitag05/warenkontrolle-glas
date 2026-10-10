import time
from datetime import date, datetime, timedelta

import pytest

from app import config, db, repository
from tools import generate_images as gen
from tools import reset_demo

HISTORY_SEED = 7
HISTORY_COUNT = 5
NOW = datetime(2026, 10, 9, 14, 0, 0)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "demo.db")
    connection = repository.register_functions(db.connect())
    db.init_db(connection)
    gen.write_database(connection, config.DEMO_SEED, gen.build_labels(60, config.DEMO_SEED))
    gen.write_database(connection, HISTORY_SEED, gen.build_labels(HISTORY_COUNT, HISTORY_SEED))
    yield connection
    connection.close()


def rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def history_part_ids(conn):
    prefix = repository.prefix_for(HISTORY_SEED)
    return [r["id"] for r in rows(conn, "SELECT id FROM part WHERE serial LIKE ? ORDER BY id", (prefix + "%",))]


def make_history(conn):
    """Inspections plus a review for non-demo parts; they must survive a reset."""
    ids = [repository.infeed_part(conn, part_id) for part_id in history_part_ids(conn)]
    flagged = [i for i in ids if rows(conn, "SELECT ai_verdict FROM inspection WHERE id = ?", (i,))[0]["ai_verdict"] != "OK"]
    if flagged:
        repository.save_review(conn, flagged[0], "CORRECTED", "OK", "Altbestand", [])
    return snapshot_history(conn)


def snapshot_history(conn):
    ids = history_part_ids(conn)
    marks = ",".join("?" * len(ids))
    return (
        rows(conn, f"SELECT * FROM part WHERE id IN ({marks}) ORDER BY id", ids),
        rows(conn, f"SELECT * FROM inspection WHERE part_id IN ({marks}) ORDER BY id", ids),
        rows(conn, f"SELECT d.* FROM detection d JOIN inspection i ON i.id = d.inspection_id WHERE i.part_id IN ({marks}) ORDER BY d.id", ids),
        rows(conn, f"SELECT r.* FROM review r JOIN inspection i ON i.id = r.inspection_id WHERE i.part_id IN ({marks}) ORDER BY r.id", ids),
    )


def stream_results(conn):
    """Inspection results of the stream parts without database ids."""
    return rows(
        conn,
        "SELECT p.serial, i.inspected_at, i.ai_verdict, i.ai_reason, "
        " (SELECT GROUP_CONCAT(d.defect_type || ':' || d.confidence || ':' || COALESCE(d.injected_error, ''), ',') "
        "  FROM detection d WHERE d.inspection_id = i.id) AS detections "
        "FROM inspection i JOIN part p ON p.id = i.part_id "
        "WHERE p.serial LIKE ? || '%' OR p.serial LIKE ? || '%' ORDER BY i.inspected_at, p.serial",
        (repository.demo_prefix(), repository.prefix_for(config.FILLER_SEED)),
    )


def test_reset_preinspects_exactly_the_first_stream_parts(conn):
    result = repository.reset_demo(conn, NOW)
    stream = [p.serial for p in repository.station_stream(conn)[0]]
    assert result["preinspected"] == config.DEMO_PREINSPECTED
    assert len(stream) == config.STATION_MAX_PARTS
    assert {r["serial"] for r in stream_results(conn)} == set(stream[: config.DEMO_PREINSPECTED])
    assert rows(conn, "SELECT COUNT(*) AS n FROM review")[0]["n"] == 0


def test_all_required_parts_are_preinspected(conn):
    result = repository.reset_demo(conn, NOW)
    assert len(result["required_positions"]) == len(config.MOCK_REQUIRED_SCENARIOS)
    assert all(pos <= config.DEMO_PREINSPECTED for pos in result["required_positions"].values())
    inspected = {r["serial"] for r in stream_results(conn)}
    assert set(result["required_positions"]) <= inspected


def test_queue_is_filled_after_reset(conn):
    repository.reset_demo(conn, NOW)
    queue = repository.station_state(conn)["queue"]
    assert len(queue) >= len(config.MOCK_REQUIRED_SCENARIOS)


def test_timestamps_spread_over_last_span(conn):
    repository.reset_demo(conn, NOW)
    stamps = sorted(datetime.fromisoformat(r["inspected_at"]) for r in stream_results(conn))
    span = timedelta(minutes=config.DEMO_PREINSPECTED_SPAN_MIN)
    assert all(NOW - span < s <= NOW for s in stamps)
    assert len(set(stamps)) == len(stamps)
    assert stamps[-1] - stamps[0] >= span * 0.9


def test_reset_is_deterministic(conn):
    repository.reset_demo(conn, NOW)
    first = stream_results(conn)
    for _ in range(5):
        repository.infeed_next(conn)
    repository.reset_demo(conn, NOW)
    assert stream_results(conn) == first


def test_history_unchanged_and_demo_reviews_removed(conn):
    history = make_history(conn)
    repository.reset_demo(conn, NOW)
    for _ in range(5):
        repository.infeed_next(conn)
    pending = repository.station_state(conn)["queue"][0]["id"]
    detection_id = rows(conn, "SELECT id FROM detection WHERE inspection_id = ? LIMIT 1", (pending,))[0]["id"]
    repository.save_review(conn, pending, "CORRECTED", "OK", None, [{"change": "REJECTED", "detection_id": detection_id}])

    result = repository.reset_demo(conn, NOW)
    assert result["deleted"] == config.DEMO_PREINSPECTED + 5
    assert snapshot_history(conn) == history
    assert rows(conn, "SELECT COUNT(*) AS n FROM review_change")[0]["n"] == 0
    assert rows(conn, "SELECT COUNT(*) AS n FROM review")[0]["n"] == (1 if history[3] else 0)
    assert len(stream_results(conn)) == config.DEMO_PREINSPECTED


def test_reset_runtime_below_ten_seconds(conn):
    started = time.perf_counter()
    repository.reset_demo(conn, NOW)
    assert time.perf_counter() - started < 10


@pytest.fixture
def small_history(tmp_path, monkeypatch):
    """Keep the automatic seed_history run small and inside tmp_path."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "HISTORY_PARTS_PER_DAY", {"L1": 3, "L2": 2, "L3": 2})
    monkeypatch.setattr(config, "HISTORY_POOL_SIZE", 12)


def history_days(conn):
    prefix = repository.prefix_for(config.HISTORY_SEED)
    return rows(
        conn,
        "SELECT MIN(substr(i.inspected_at, 1, 10)) AS first, MAX(substr(i.inspected_at, 1, 10)) AS last "
        "FROM inspection i JOIN part p ON p.id = i.part_id WHERE p.serial LIKE ? || '%'",
        (prefix,),
    )[0]


def test_cli(conn, small_history, capsys):
    assert reset_demo.main([]) == 0
    out = capsys.readouterr().out
    assert "Demo zurückgesetzt" in out
    assert f"Teile im Strom: {config.STATION_MAX_PARTS} (60 Demo-Teile" in out
    assert "WARNUNG" not in out


def test_cli_creates_missing_history(conn, small_history, capsys):
    assert history_days(conn)["last"] is None
    assert reset_demo.main([]) == 0
    out = capsys.readouterr().out
    assert "Vergangenheitsdaten fehlen: neu erzeugt mit seed_history" in out
    yesterday = date.today() - timedelta(days=1)
    assert history_days(conn) == {
        "first": (date.today() - timedelta(days=config.HISTORY_DAYS)).isoformat(),
        "last": yesterday.isoformat(),
    }


def test_cli_keeps_current_history(conn, small_history, capsys):
    reset_demo.main([])
    capsys.readouterr()
    before = rows(conn, "SELECT COUNT(*) AS n, MAX(i.id) AS last_id FROM inspection i JOIN part p ON p.id = i.part_id WHERE p.serial LIKE ?",
                  (repository.prefix_for(config.HISTORY_SEED) + "%",))
    assert reset_demo.main([]) == 0
    assert "Vergangenheitsdaten aktuell" in capsys.readouterr().out
    after = rows(conn, "SELECT COUNT(*) AS n, MAX(i.id) AS last_id FROM inspection i JOIN part p ON p.id = i.part_id WHERE p.serial LIKE ?",
                 (repository.prefix_for(config.HISTORY_SEED) + "%",))
    assert after == before  # not recreated


def test_cli_recreates_history_after_day_change(conn, small_history, capsys):
    reset_demo.main([])
    capsys.readouterr()
    # simulate that the history was created yesterday: everything one day older
    conn.execute("UPDATE inspection SET inspected_at = datetime(inspected_at, '-1 day') WHERE part_id IN "
                 "(SELECT id FROM part WHERE serial LIKE ? || '%')", (repository.prefix_for(config.HISTORY_SEED),))
    conn.commit()
    stale = date.today() - timedelta(days=2)
    assert reset_demo.main([]) == 0
    out = capsys.readouterr().out
    assert f"Vergangenheitsdaten reichen nur bis {stale:%d.%m.%Y}: neu erzeugt" in out
    assert history_days(conn)["last"] == (date.today() - timedelta(days=1)).isoformat()


def test_cli_without_demo_parts(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "empty.db")
    assert reset_demo.main([]) == 1
    assert "Keine Demo-Teile" in capsys.readouterr().out


def test_config_leaves_room_for_required_parts():
    slots = repository.demo_slots(config.STATION_MAX_PARTS, 60)
    early = [s for s in slots if s < config.DEMO_PREINSPECTED]
    assert len(early) >= len(config.MOCK_REQUIRED_SCENARIOS)
    assert config.DEMO_PREINSPECTED <= config.STATION_MAX_PARTS

"""The station stream: demo parts between defect-free fillers, run to the end."""

from datetime import datetime

import pytest

from app import config, db, repository
from tools import generate_images as gen

NOW = datetime(2026, 10, 9, 14, 0, 0)


@pytest.fixture(scope="module")
def full_run(tmp_path_factory):
    path = tmp_path_factory.mktemp("stream") / "stream.db"
    original = config.DB_PATH
    config.DB_PATH = path
    try:
        conn = repository.register_functions(db.connect())
        db.init_db(conn)
        gen.write_database(conn, config.DEMO_SEED, gen.build_labels(60, config.DEMO_SEED))
        result = repository.reset_demo(conn, NOW)
        stream = [p.serial for p in repository.station_stream(conn)[0]]
        live = 0
        while repository.infeed_next(conn) is not None:
            live += 1
        yield conn, result, stream, live
        conn.close()
    finally:
        config.DB_PATH = original


def rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def test_stream_stops_after_max_parts(full_run):
    conn, result, stream, live = full_run
    assert len(stream) == config.STATION_MAX_PARTS
    assert result["preinspected"] + live == config.STATION_MAX_PARTS
    assert rows(conn, "SELECT COUNT(*) AS n FROM inspection")[0]["n"] == config.STATION_MAX_PARTS
    assert repository.infeed_next(conn) is None
    assert repository.station_state(conn)["all_done"] is True


def test_no_part_inspected_twice(full_run):
    conn, *_ = full_run
    assert rows(conn, "SELECT COUNT(*) AS n FROM (SELECT part_id FROM inspection GROUP BY part_id HAVING COUNT(*) > 1)")[0]["n"] == 0


def test_flagged_share_between_3_and_5_percent(full_run):
    conn, *_ = full_run
    flagged = len(repository.station_state(conn)["queue"])
    share = flagged / config.STATION_MAX_PARTS
    assert 0.03 <= share <= 0.05, f"{flagged}/{config.STATION_MAX_PARTS} = {share:.3f}"


def test_flagged_parts_are_the_18_demo_parts(full_run):
    conn, *_ = full_run
    queue = repository.station_state(conn)["queue"]
    assert len(queue) == 18
    assert all(q["serial"].startswith(repository.demo_prefix()) for q in queue)


def test_fillers_are_stored_and_inspected(full_run):
    conn, result, stream, _ = full_run
    filler_prefix = repository.prefix_for(config.FILLER_SEED)
    fillers = rows(
        conn,
        "SELECT p.serial, p.image_path, b.code AS batch, l.code AS line, i.ai_verdict, "
        " (SELECT COUNT(*) FROM detection d WHERE d.inspection_id = i.id) AS detections, "
        " (SELECT COUNT(*) FROM gt_defect g WHERE g.part_id = p.id) AS truths "
        "FROM part p JOIN batch b ON b.id = p.batch_id JOIN production_line l ON l.id = b.line_id "
        "JOIN inspection i ON i.part_id = p.id WHERE p.serial LIKE ? || '%'",
        (filler_prefix,),
    )
    assert len(fillers) == config.STATION_MAX_PARTS - 60 == result["fillers"]
    assert len({f["serial"] for f in fillers}) == len(fillers)
    assert {f["line"] for f in fillers} == set(config.GENERATOR_LINE_SHARES)
    assert all(f["ai_verdict"] == "OK" and f["detections"] == 0 and f["truths"] == 0 for f in fillers)
    clean_demo_images = {
        r["image_path"]
        for r in rows(
            conn,
            "SELECT image_path FROM part p WHERE serial LIKE ? || '%' AND NOT EXISTS (SELECT 1 FROM gt_defect g WHERE g.part_id = p.id)",
            (repository.demo_prefix(),),
        )
    }
    assert {f["image_path"] for f in fillers} <= clean_demo_images


def test_demo_parts_spread_over_the_stream(full_run):
    _, result, stream, _ = full_run
    positions = result["demo_positions"]
    assert len(positions) == 60
    gaps = [b - a for a, b in zip(positions, positions[1:])]
    assert max(gaps) - min(gaps) <= 1
    assert positions[0] < 10 and positions[-1] > config.STATION_MAX_PARTS - 10


def test_stream_order_deterministic(full_run):
    conn, _, stream, _ = full_run
    assert [p.serial for p in repository.station_stream(conn)[0]] == stream

from datetime import date, timedelta

import pytest

from app import config, db, repository
from tools import generate_images as gen
from tools import seed_history

TODAY = date(2026, 10, 9)
TOLERANCE = 0.015


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    path = tmp_path_factory.mktemp("history") / "history.db"
    conn = repository.register_functions(db.connect(path))
    db.init_db(conn)
    gen.write_database(conn, config.DEMO_SEED, gen.build_labels(60, config.DEMO_SEED))
    stats = seed_history.seed_history(conn, today=TODAY, render=False)
    yield conn, stats
    conn.close()


def rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


HISTORY = repository.prefix_for(config.HISTORY_SEED) + "%"


def test_thirty_days_before_today(seeded):
    conn, _ = seeded
    days = [r["d"] for r in rows(conn, "SELECT DISTINCT date(i.inspected_at) AS d FROM inspection i JOIN part p ON p.id = i.part_id WHERE p.serial LIKE ? ORDER BY d", (HISTORY,))]
    assert len(days) == config.HISTORY_DAYS == 30
    assert days[0] == (TODAY - timedelta(days=30)).isoformat()
    assert days[-1] == (TODAY - timedelta(days=1)).isoformat()


def test_three_lines_with_configured_volume(seeded):
    _, stats = seeded
    assert set(stats) == {"L1", "L2", "L3"}
    for line, s in stats.items():
        assert s["inspections"] == config.HISTORY_PARTS_PER_DAY[line] * config.HISTORY_DAYS
        assert s["days"] == config.HISTORY_DAYS


@pytest.mark.parametrize("line", ["L1", "L2", "L3"])
def test_defect_rate_per_line_within_tolerance(seeded, line):
    _, stats = seeded
    s = stats[line]
    rate = s["final_nok"] / s["inspections"]
    assert abs(rate - config.HISTORY_DEFECT_RATE[line]) <= TOLERANCE, f"{line}: {rate:.4f}"


def test_every_flagged_inspection_has_a_review(seeded):
    conn, _ = seeded
    open_ = rows(
        conn,
        "SELECT COUNT(*) AS n FROM inspection i JOIN part p ON p.id = i.part_id "
        "WHERE p.serial LIKE ? AND i.ai_verdict <> 'OK' AND NOT EXISTS (SELECT 1 FROM review r WHERE r.inspection_id = i.id)",
        (HISTORY,),
    )[0]["n"]
    assert open_ == 0
    auto_reviewed = rows(
        conn,
        "SELECT COUNT(*) AS n FROM review r JOIN inspection i ON i.id = r.inspection_id JOIN part p ON p.id = i.part_id "
        "WHERE p.serial LIKE ? AND i.ai_verdict = 'OK'",
        (HISTORY,),
    )[0]["n"]
    assert auto_reviewed == 0


def test_deliberate_mock_errors_are_corrected(seeded):
    conn, _ = seeded
    wrong = rows(
        conn,
        "SELECT d.id, d.defect_type, d.injected_error FROM detection d JOIN inspection i ON i.id = d.inspection_id "
        "JOIN part p ON p.id = i.part_id WHERE p.serial LIKE ? AND i.ai_verdict <> 'OK' "
        "AND d.injected_error IN ('FALSE_POSITIVE', 'TYPE_CONFUSION')",
        (HISTORY,),
    )
    assert wrong, "history should contain deliberate mock errors"
    changes = {r["detection_id"]: r for r in rows(conn, "SELECT * FROM review_change WHERE detection_id IS NOT NULL")}
    for d in wrong:
        change = changes[d["id"]]
        assert change["change"] == "TYPE_CHANGED"
        expected = "SPIEGELUNG" if d["injected_error"] == "FALSE_POSITIVE" else config.MOCK_TYPE_CONFUSION[d["defect_type"]]
        assert change["new_defect_type"] == expected


def test_reviews_are_plausible(seeded):
    conn, _ = seeded
    reviews = rows(
        conn,
        "SELECT r.decision, r.final_verdict, r.reviewed_at, i.inspected_at, i.ai_verdict, "
        " (SELECT COUNT(*) FROM review_change c WHERE c.review_id = r.id) AS changes "
        "FROM review r JOIN inspection i ON i.id = r.inspection_id JOIN part p ON p.id = i.part_id WHERE p.serial LIKE ?",
        (HISTORY,),
    )
    assert {r["decision"] for r in reviews} == {"CONFIRMED", "CORRECTED"}
    for r in reviews:
        assert r["reviewed_at"] > r["inspected_at"]
        if r["decision"] == "CONFIRMED":
            assert r["changes"] == 0
            if r["ai_verdict"] == "NOK":
                assert r["final_verdict"] == "NOK"


def test_own_image_pool(seeded):
    conn, _ = seeded
    paths = {r["image_path"] for r in rows(conn, "SELECT image_path FROM part WHERE serial LIKE ?", (HISTORY,))}
    assert paths and all(p.startswith(config.HISTORY_POOL_DIRNAME + "/") for p in paths)
    demo_paths = {r["image_path"] for r in rows(conn, "SELECT image_path FROM part WHERE serial LIKE ?", (repository.demo_prefix() + "%",))}
    assert paths.isdisjoint(demo_paths)


def test_demo_parts_untouched(seeded):
    conn, _ = seeded
    assert rows(conn, "SELECT COUNT(*) AS n FROM part WHERE serial LIKE ?", (repository.demo_prefix() + "%",))[0]["n"] == 60
    assert rows(
        conn,
        "SELECT COUNT(*) AS n FROM inspection i JOIN part p ON p.id = i.part_id WHERE p.serial LIKE ?",
        (repository.demo_prefix() + "%",),
    )[0]["n"] == 0


def test_rerun_replaces_and_is_deterministic(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_PARTS_PER_DAY", {"L1": 6, "L2": 5, "L3": 4})
    conn = repository.register_functions(db.connect(tmp_path / "rerun.db"))
    db.init_db(conn)
    try:
        first = seed_history.seed_history(conn, today=TODAY, render=False)
        snapshot = rows(conn, "SELECT p.serial, i.inspected_at, i.ai_verdict FROM inspection i JOIN part p ON p.id = i.part_id ORDER BY p.serial")
        second = seed_history.seed_history(conn, today=TODAY, render=False)
        assert first == second
        assert rows(conn, "SELECT p.serial, i.inspected_at, i.ai_verdict FROM inspection i JOIN part p ON p.id = i.part_id ORDER BY p.serial") == snapshot
        assert rows(conn, "SELECT COUNT(*) AS n FROM part")[0]["n"] == 15 * config.HISTORY_DAYS
    finally:
        conn.close()


def test_pool_images_are_rendered(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_POOL_SIZE", 8)
    pool = seed_history.build_pool(render=True, data_dir=tmp_path)
    files = sorted((tmp_path / config.HISTORY_POOL_DIRNAME).glob("*.png"))
    assert len(files) == 8
    assert {label["target"] for label in pool} <= {"OK", "NOK"}

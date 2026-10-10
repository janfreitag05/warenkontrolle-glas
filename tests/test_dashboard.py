"""Dashboard figures against independent SQL counts (no status function in the test SQL)."""

import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, db, repository, rules
from app.main import app
from tools import generate_images as gen
from tools import seed_history

TODAY = date.today()


def rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    """History (30 days) + station stream run to the end + some reviews, fixed seeds.

    Also 5 parts of another seed produced today that never ran into the station
    (they lower the coverage), while reset fillers that have not arrived must not count.
    """
    path = tmp_path_factory.mktemp("dash") / "dash.db"
    connection = repository.register_functions(db.connect(path))
    db.init_db(connection)
    gen.write_database(connection, config.DEMO_SEED, gen.build_labels(60, config.DEMO_SEED))
    gen.write_database(connection, 7, gen.build_labels(5, 7))
    seed_history.seed_history(connection, today=TODAY, render=False)
    repository.reset_demo(connection, datetime.now())
    while repository.infeed_next(connection) is not None:
        pass
    queue = repository.station_state(connection)["queue"]
    nok = [q["id"] for q in queue if q["ai_verdict"] == "NOK"]
    manual = [q["id"] for q in queue if q["ai_verdict"] == "MANUAL"]
    repository.save_review(connection, nok[0], "CONFIRMED", None, None, [])
    repository.save_review(connection, nok[1], "CORRECTED", "OK", "Fehlalarm", [])  # verdict changed
    detection = rows(connection, "SELECT id, defect_type FROM detection WHERE inspection_id = ? LIMIT 1", (nok[2],))[0]
    other = "RISS" if detection["defect_type"] != "RISS" else "KRATZER"
    repository.save_review(  # type only, stays NOK
        connection, nok[2], "CORRECTED", "NOK", None,
        [{"change": "TYPE_CHANGED", "detection_id": detection["id"], "new_defect_type": other}],
    )
    for manual_id, final in ((manual[0], "OK"), (manual[1], "NOK")):  # two corrected MANUAL cases
        detection = rows(connection, "SELECT id FROM detection WHERE inspection_id = ? LIMIT 1", (manual_id,))[0]["id"]
        repository.save_review(connection, manual_id, "CORRECTED", final, None, [{"change": "REJECTED", "detection_id": detection}])
    yield connection
    connection.close()


# --- independent reference ---------------------------------------------------------

def reference(conn, start: date, end: date, line: str | None) -> dict:
    line_sql = " AND l.code = ?" if line else ""
    params = [start.isoformat(), end.isoformat(), *([line] if line else [])]
    base = (
        "FROM inspection i JOIN part p ON p.id = i.part_id JOIN batch b ON b.id = p.batch_id "
        "JOIN production_line l ON l.id = b.line_id LEFT JOIN review r ON r.inspection_id = i.id "
        f"WHERE substr(i.inspected_at, 1, 10) BETWEEN ? AND ?{line_sql}"
    )
    count = lambda cond: conn.execute(f"SELECT COUNT(*) {base} AND ({cond})", params).fetchone()[0]  # noqa: E731
    ref = {
        "inspected": count("1"),
        "closed": count("i.ai_verdict = 'OK' OR r.id IS NOT NULL"),
        # open inspections count provisionally with the AI verdict; open MANUAL has none
        "rated": count("NOT (i.ai_verdict = 'MANUAL' AND r.id IS NULL)"),
        "nok": count("r.final_verdict = 'NOK' OR (r.id IS NULL AND i.ai_verdict = 'NOK')"),
        "open": count("i.ai_verdict <> 'OK' AND r.id IS NULL"),
        "open_nok": count("i.ai_verdict = 'NOK' AND r.id IS NULL"),
        "open_manual": count("i.ai_verdict = 'MANUAL' AND r.id IS NULL"),
        "ai_nok_reviewed": count("i.ai_verdict = 'NOK' AND r.id IS NOT NULL"),
        "ai_nok_corrected": count("i.ai_verdict = 'NOK' AND r.final_verdict = 'OK'"),
    }
    # produced = parts that entered the line: stream parts only once inspected
    produced = conn.execute(
        "SELECT COUNT(*), SUM(EXISTS (SELECT 1 FROM inspection x WHERE x.part_id = p.id)) "
        "FROM part p JOIN batch b ON b.id = p.batch_id JOIN production_line l ON l.id = b.line_id "
        f"WHERE b.produced_on BETWEEN ? AND ?{line_sql} "
        "AND (EXISTS (SELECT 1 FROM inspection x WHERE x.part_id = p.id) "
        "     OR NOT (substr(p.serial, 1, 6) IN (?, ?)))",
        [*params, f"GT-{config.DEMO_SEED:03d}", f"GT-{config.FILLER_SEED:03d}"],
    ).fetchone()
    ref["produced"], ref["produced_inspected"] = produced[0], produced[1] or 0
    ref["defect_rate"] = ref["nok"] / ref["rated"] if ref["rated"] else None
    ref["provisional"] = ref["open"] > 0
    ref["correction_rate"] = ref["ai_nok_corrected"] / ref["ai_nok_reviewed"] if ref["ai_nok_reviewed"] else None
    ref["coverage"] = ref["produced_inspected"] / ref["produced"] if ref["produced"] else None
    return ref


FILTERS = [
    ("30d", None, None, None),
    ("today", "L2", None, None),
    ("7d", "L1", None, None),
    ("custom", "L3", (TODAY - timedelta(days=12)).isoformat(), (TODAY - timedelta(days=3)).isoformat()),
]


@pytest.mark.parametrize("period, line, date_from, date_to", FILTERS)
def test_every_kpi_matches_independent_sql(conn, period, line, date_from, date_to):
    data = repository.dashboard(conn, period, line, date_from, date_to, today=TODAY)
    expected = reference(conn, data["start"], data["end"], line)
    assert data["kpis"]["inspected"] > 0
    for key, value in expected.items():
        assert data["kpis"][key] == pytest.approx(value), key


def test_period_bounds(conn):
    assert repository.resolve_period("today", None, None, TODAY)[1:] == (TODAY, TODAY)
    assert repository.resolve_period("7d", None, None, TODAY)[1:] == (TODAY - timedelta(days=6), TODAY)
    assert repository.resolve_period("30d", None, None, TODAY)[1:] == (TODAY - timedelta(days=29), TODAY)
    assert repository.resolve_period("custom", "2026-10-05", "2026-10-01", TODAY)[1:] == (date(2026, 10, 1), date(2026, 10, 5))
    assert repository.resolve_period("custom", "kaputt", None, TODAY)[0] == config.DASHBOARD_DEFAULT_PERIOD
    assert repository.resolve_period("unbekannt", None, None, TODAY)[0] == config.DASHBOARD_DEFAULT_PERIOD


def test_correction_rate_counts_only_changed_verdicts_and_ignores_manual(conn):
    kpis = repository.dashboard(conn, "today", today=TODAY)["kpis"]
    decisions = rows(
        conn,
        "SELECT i.ai_verdict, r.decision FROM review r JOIN inspection i ON i.id = r.inspection_id "
        "WHERE substr(i.inspected_at, 1, 10) = ?",
        (TODAY.isoformat(),),
    )
    corrected_nok = [d for d in decisions if d["ai_verdict"] == "NOK" and d["decision"] == "CORRECTED"]
    corrected_manual = [d for d in decisions if d["ai_verdict"] == "MANUAL" and d["decision"] == "CORRECTED"]
    assert len(corrected_nok) == 2 and len(corrected_manual) == 2  # the fixture's cases are really there
    # only NOK -> OK counts; the type correction (still NOK) and the MANUAL cases do not
    assert (kpis["ai_nok_corrected"], kpis["ai_nok_reviewed"]) == (1, 3)
    assert kpis["correction_rate"] == pytest.approx(1 / 3)


def test_coverage_counts_only_parts_that_entered_the_line(tmp_path):
    connection = repository.register_functions(db.connect(tmp_path / "coverage.db"))
    db.init_db(connection)
    try:
        gen.write_database(connection, config.DEMO_SEED, gen.build_labels(60, config.DEMO_SEED))
        repository.reset_demo(connection, datetime.now())
        kpis = repository.dashboard(connection, "today", today=TODAY)["kpis"]
        assert kpis["produced"] == config.DEMO_PREINSPECTED  # not all 450 parts created by the reset
        assert kpis["coverage"] == 1.0
        gen.write_database(connection, 7, gen.build_labels(5, 7))  # produced today, never inspected
        kpis = repository.dashboard(connection, "today", today=TODAY)["kpis"]
        assert kpis["produced"] == config.DEMO_PREINSPECTED + 5
        assert kpis["coverage"] == pytest.approx(config.DEMO_PREINSPECTED / (config.DEMO_PREINSPECTED + 5))
    finally:
        connection.close()


def test_defect_rate_with_open_inspections(tmp_path):
    connection = repository.register_functions(db.connect(tmp_path / "open.db"))
    db.init_db(connection)
    try:
        gen.write_database(connection, config.DEMO_SEED, gen.build_labels(60, config.DEMO_SEED))
        repository.reset_demo(connection, datetime.now())
        kpi = lambda: repository.dashboard(connection, "today", today=TODAY)["kpis"]  # noqa: E731

        # open NOK counts provisionally with the AI verdict
        start = kpi()
        assert start["provisional"] and start["open_nok"] > 0
        assert start["defect_rate"] == pytest.approx(start["nok"] / start["rated"])
        assert start["rated"] == start["closed"] + start["open_nok"]

        # close everything except one MANUAL case: still provisional, the MANUAL case is not rated
        queue = repository.station_state(connection)["queue"]
        manual = [q for q in queue if q["ai_verdict"] == "MANUAL"]
        for q in queue:
            if q is not manual[-1]:
                final = "NOK" if q["ai_verdict"] == "NOK" else "OK"
                repository.save_review(connection, q["id"], "CONFIRMED", final, None, [])
        one_open = kpi()
        assert one_open["provisional"] and one_open["open"] == 1
        assert one_open["rated"] == one_open["closed"]

        # nothing open: the previous definition (NOK / closed), no longer provisional
        repository.save_review(connection, manual[-1]["id"], "CONFIRMED", "OK", None, [])
        closed = kpi()
        assert not closed["provisional"]
        assert closed["defect_rate"] == pytest.approx(closed["nok"] / closed["closed"])

        # a new open NOK part raises the provisional rate
        stream, detector = repository.station_stream(connection)
        inspected = {r["part_id"] for r in rows(connection, "SELECT part_id FROM inspection")}
        flagged = next(p for p in stream if p.part_id not in inspected and rules.evaluate(detector.analyze(p))[0] == "NOK")
        repository.infeed_part(connection, flagged.part_id)
        after = kpi()
        assert after["provisional"]
        assert (after["nok"], after["rated"]) == (closed["nok"] + 1, closed["rated"] + 1)
    finally:
        connection.close()


def test_defect_types_use_type_correction(tmp_path):
    connection = repository.register_functions(db.connect(tmp_path / "types.db"))
    db.init_db(connection)
    try:
        gen.write_database(connection, config.DEMO_SEED, gen.build_labels(60, config.DEMO_SEED))
        repository.reset_demo(connection, datetime.now())
        while repository.infeed_next(connection) is not None:
            pass
        # a pending part whose only finding is a scratch
        candidate = next(
            q["id"]
            for q in repository.station_state(connection)["queue"]
            if [d["defect_type"] for d in rows(connection, "SELECT defect_type FROM detection WHERE inspection_id = ?", (q["id"],))] == ["KRATZER"]
        )
        detection = rows(connection, "SELECT id FROM detection WHERE inspection_id = ?", (candidate,))[0]["id"]
        before = dict(repository.dashboard(connection, "today", today=TODAY)["defect_types"]["defects"])
        repository.save_review(
            connection, candidate, "CORRECTED", "NOK", None,
            [{"change": "TYPE_CHANGED", "detection_id": detection, "new_defect_type": "RISS"}],
        )
        after = dict(repository.dashboard(connection, "today", today=TODAY)["defect_types"]["defects"])
    finally:
        connection.close()
    assert after["RISS"] == before["RISS"] + 1
    assert after["KRATZER"] == before["KRATZER"]   # counted as a crack, not as a scratch


def test_defect_types_against_independent_count(conn):
    data = repository.dashboard(conn, "30d", today=TODAY)
    start, end = data["start"].isoformat(), data["end"].isoformat()
    closed = {
        r["id"]
        for r in rows(
            conn,
            "SELECT i.id FROM inspection i LEFT JOIN review r ON r.inspection_id = i.id "
            "WHERE substr(i.inspected_at, 1, 10) BETWEEN ? AND ? AND (i.ai_verdict = 'OK' OR r.id IS NOT NULL)",
            (start, end),
        )
    }
    types: dict[int, dict] = {}
    for d in rows(conn, "SELECT id, inspection_id, defect_type FROM detection"):
        if d["inspection_id"] in closed:
            types.setdefault(d["inspection_id"], {})[d["id"]] = d["defect_type"]
    for c in rows(conn, "SELECT c.*, r.inspection_id FROM review_change c JOIN review r ON r.id = c.review_id ORDER BY c.id"):
        if c["inspection_id"] not in closed:
            continue
        part = types.setdefault(c["inspection_id"], {})
        if c["change"] == "REJECTED":
            part.pop(c["detection_id"], None)
        elif c["change"] == "TYPE_CHANGED":
            part[c["detection_id"]] = c["new_defect_type"]
        else:
            part[("missed", c["id"])] = c["new_defect_type"]
    expected = {code: 0 for code in config.DEFECT_TYPES}
    for part in types.values():
        for t in set(part.values()):
            expected[t] += 1
    got = dict(data["defect_types"]["defects"]) | dict(data["defect_types"]["filtered"])
    assert got == expected
    assert [code for code, _ in data["defect_types"]["filtered"]] == ["SPIEGELUNG"]


def test_trend_matches_daily_sql(conn):
    data = repository.dashboard(conn, "7d", by_line=True, today=TODAY)
    trend = data["trend"]
    assert trend["days"][-1] == TODAY.isoformat() and len(trend["days"]) == 7
    assert set(trend["lines"]) == {"L1", "L2", "L3"}
    for index, day in enumerate(trend["days"]):
        ref = reference(conn, date.fromisoformat(day), date.fromisoformat(day), None)
        assert trend["total"]["rate"][index] == pytest.approx(ref["defect_rate"])
        assert trend["total"]["provisional"][index] == ref["provisional"]
        ref_l2 = reference(conn, date.fromisoformat(day), date.fromisoformat(day), "L2")
        assert trend["lines"]["L2"]["rate"][index] == pytest.approx(ref_l2["defect_rate"])
        assert trend["lines"]["L2"]["provisional"][index] == ref_l2["provisional"]
    # history days are final, today is provisional (open inspections in the queue)
    assert trend["total"]["provisional"] == [False] * 6 + [True]
    assert repository.dashboard(conn, "7d", line="L1", by_line=True, today=TODAY)["trend"]["lines"] == {}


def test_daily_volume_matches_station_run(conn):
    per_day = rows(
        conn,
        "SELECT substr(inspected_at, 1, 10) AS day, COUNT(*) AS n FROM inspection GROUP BY day ORDER BY day DESC LIMIT 4",
    )
    assert per_day[0]["day"] == TODAY.isoformat()
    for r in per_day:
        assert r["n"] == config.STATION_MAX_PARTS


# --- page ------------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "page.db")
    monkeypatch.setattr(config, "HISTORY_PARTS_PER_DAY", {"L1": 6, "L2": 5, "L3": 4})
    connection = repository.register_functions(db.connect())
    db.init_db(connection)
    gen.write_database(connection, config.DEMO_SEED, gen.build_labels(60, config.DEMO_SEED))
    seed_history.seed_history(connection, today=TODAY, render=False)
    repository.reset_demo(connection)
    connection.close()
    with TestClient(app) as test_client:
        yield test_client


def test_page_shows_german_kpis_and_filters_from_url(client):
    html = client.get("/dashboard?period=7d&line=L2").text
    for text in ("Geprüfte Teile", "Prüfabdeckung", "Fehlerhafte Teile", "Fehlerquote", "Offene manuelle Prüfungen",
                 "KI-Korrekturquote", "Fehlertypen", "gefiltert, kein Fehler"):
        assert text in html
    assert re.search(r'<option value="7d"\s+selected>7 Tage</option>', html)
    assert re.search(r'<option value="L2"\s+selected>', html)
    assert 'method="get"' in html and 'action="/dashboard"' in html


def test_chart_js_is_served_locally(client):
    html = client.get("/dashboard").text
    assert '<script src="/static/vendor/chart.umd.min.js"></script>' in html
    # every absolute URL points to the app itself, nothing to a CDN or another host
    hosts = {m.group(1) for m in re.finditer(r'(?:src|href)="(?:https?:)?//([^/"]+)', html)}
    assert hosts <= {"testserver"}
    assert "cdn" not in html.lower()
    response = client.get("/static/vendor/chart.umd.min.js")
    assert response.status_code == 200
    assert "Chart.js v4.4.6" in response.text[:200]
    assert (config.STATIC_DIR / "vendor" / "chart.js-LICENSE.md").read_text(encoding="utf-8").startswith("The MIT License")


def test_start_page_links_dashboard(client):
    assert 'href="/dashboard"' in client.get("/").text


def test_page_hides_internal_data(client):
    html = client.get("/dashboard?period=30d&by_line=1").text
    for word in ("injected_error", "gt_defect", *config.INJECTED_ERRORS):
        assert word not in html


def test_history_volume_config():
    assert sum(config.HISTORY_PARTS_PER_DAY.values()) == config.STATION_MAX_PARTS
    assert config.HISTORY_SERIAL_TEMPLATE.startswith(config.SERIAL_PREFIX_TEMPLATE)
    assert config.HISTORY_SERIAL_TEMPLATE.format(seed=1, index=13500).startswith(config.SERIAL_PREFIX_TEMPLATE.format(seed=1))

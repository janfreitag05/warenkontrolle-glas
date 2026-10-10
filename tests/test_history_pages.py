"""/station/history and /station/inspection/{id}."""

import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import config, db, repository
from app.main import app
from tools import generate_images as gen
from tools import seed_history

HIDDEN = ["injected_error", "gt_defect", *config.INJECTED_ERRORS]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pages")
    saved = (config.DATA_DIR, config.DB_PATH, config.HISTORY_PARTS_PER_DAY)
    config.DATA_DIR, config.DB_PATH = tmp, tmp / "pages.db"
    config.HISTORY_PARTS_PER_DAY = {"L1": 20, "L2": 15, "L3": 10}
    try:
        gen.generate(60, config.DEMO_SEED, data_dir=tmp, db_path=config.DB_PATH)
        conn = repository.register_functions(db.connect())
        seed_history.seed_history(conn, today=date.today(), render=False)
        repository.reset_demo(conn)
        conn.close()
        with TestClient(app) as test_client:
            yield test_client
    finally:
        config.DATA_DIR, config.DB_PATH, config.HISTORY_PARTS_PER_DAY = saved


def query(sql, params=()):
    conn = repository.register_functions(db.connect())
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def detail_ids(html):
    return [int(i) for i in re.findall(r'href="/station/inspection/(\d+)', html)]


def test_default_shows_only_flagged(client):
    response = client.get("/station/history")
    assert response.status_code == 200
    html = response.text
    for header in ("Zeit", "Seriennummer", "Linie", "Charge", "KI-Urteil", "Endurteil", "Status"):
        assert f"<th>{header}</th>" in html
    assert re.search(r'<option value="flagged"\s+selected>Nur auffällige</option>', html)
    ids = detail_ids(html)
    assert len(ids) == config.HISTORY_PAGE_SIZE
    marks = ",".join("?" * len(ids))
    assert {r["ai_verdict"] for r in query(f"SELECT ai_verdict FROM inspection WHERE id IN ({marks})", ids)} <= {"NOK", "MANUAL"}


def test_scope_all_includes_automatic_ok(client):
    total_flagged = repository_total(scope="flagged")
    total_all = repository_total(scope="all")
    assert total_all == query("SELECT COUNT(*) AS n FROM inspection")[0]["n"]
    assert total_all > total_flagged
    assert f"{total_all} Treffer" in client.get("/station/history?scope=all").text


def repository_total(**filters):
    conn = repository.register_functions(db.connect())
    try:
        return repository.history(conn, **filters)["total"]
    finally:
        conn.close()


def test_corrected_filter_and_highlight(client):
    html = client.get("/station/history?corrected=1").text
    ids = detail_ids(html)
    assert ids
    marks = ",".join("?" * len(ids))
    decisions = {r["decision"] for r in query(f"SELECT decision FROM review WHERE inspection_id IN ({marks})", ids)}
    assert decisions == {"CORRECTED"}
    assert html.count('class="row-corrected"') == len(ids)
    assert "korrigiert</span>" in html


@pytest.mark.parametrize("status", ["AUTO_DONE", "PENDING_REVIEW", "REVIEWED"])
def test_status_filter(client, status):
    conn = repository.register_functions(db.connect())
    try:
        result = repository.history(conn, scope="all", status=status)
    finally:
        conn.close()
    assert result["total"] > 0
    assert {r["status"] for r in result["rows"]} == {status}


@pytest.mark.parametrize("final, expected", [("OK", {"OK"}), ("NOK", {"NOK"}), ("OPEN", {None})])
def test_final_filter(client, final, expected):
    conn = repository.register_functions(db.connect())
    try:
        result = repository.history(conn, scope="all", final=final)
    finally:
        conn.close()
    assert result["total"] > 0
    assert {r["final_verdict"] for r in result["rows"]} == expected


def test_pagination(client):
    first = detail_ids(client.get("/station/history?scope=all&page=1").text)
    second = detail_ids(client.get("/station/history?scope=all&page=2").text)
    assert first and second and not set(first) & set(second)
    assert "Seite 2 von" in client.get("/station/history?scope=all&page=2").text


def test_detail_page_corrected(client):
    inspection_id = detail_ids(client.get("/station/history?corrected=1").text)[0]
    html = client.get(f"/station/inspection/{inspection_id}").text
    for text in ("KI-Ergebnis", "Review", "Korrigiert", "Endurteil", f"/api/station/inspections/{inspection_id}/image", "<svg"):
        assert text in html
    assert "<button" not in html and "<form" not in html  # read only


def test_detail_page_auto_ok(client):
    auto = query("SELECT id FROM inspection WHERE ai_verdict = 'OK' LIMIT 1")[0]["id"]
    html = client.get(f"/station/inspection/{auto}").text
    assert "automatisch freigegeben" in html


def test_pages_hide_internal_data(client):
    pages = [client.get("/station/history?scope=all"), client.get("/station/history?corrected=1")]
    for inspection_id in detail_ids(pages[1].text)[:10]:
        pages.append(client.get(f"/station/inspection/{inspection_id}"))
    for page in pages:
        for word in HIDDEN:
            assert word not in page.text, (page.url, word)


def test_unknown_inspection_404(client):
    assert client.get("/station/inspection/999999").status_code == 404

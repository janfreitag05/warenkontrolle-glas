from fastapi.testclient import TestClient

from app import db
from app.main import app


def test_startup_creates_database(isolated_db):
    with TestClient(app):
        pass
    assert isolated_db.exists()
    conn = db.connect(isolated_db)
    try:
        assert "inspection" in db.table_names(conn)
    finally:
        conn.close()


def test_start_page_is_german_with_links(isolated_db):
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert '<html lang="de">' in html
    assert "Prüfstation" in html
    assert "Dashboard" in html
    assert 'href="/station"' in html
    assert 'href="/dashboard"' in html


def test_stylesheet_served(isolated_db):
    with TestClient(app) as client:
        response = client.get("/static/css/app.css")
    assert response.status_code == 200

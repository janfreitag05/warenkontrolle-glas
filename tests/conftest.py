import pytest

from app import config, db


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "test.db")
    db.init_db(connection)
    yield connection
    connection.close()


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point the app's default database at a temporary file."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    return config.DB_PATH

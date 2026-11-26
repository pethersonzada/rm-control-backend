import hashlib
import os

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app.config import Identity, Settings
from app.database import connect
from app.main import create_app


def token(name):
    return hashlib.sha256(("synthetic-test-" + name).encode()).hexdigest()


def headers(name="editor"):
    return {"Authorization": "Bearer " + token(name)}


@pytest.fixture(params=["sqlite", "postgres"])
def workspace(request, tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'qa.db'}" if request.param == "sqlite" else os.getenv("RM_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("RM_TEST_POSTGRES_URL ausente")
    if request.param == "postgres" and not (make_url(url).database or "").endswith("_test"):
        pytest.fail("Use um banco descartável com nome terminado em _test.")
    monkeypatch.setenv("DATABASE_URL", url)
    migration = Config("alembic.ini")
    if request.param == "postgres":
        command.downgrade(migration, "base")
    command.upgrade(migration, "head")
    command.check(migration)
    identities = {
        name: Identity(name, organization, role, frozenset(stores))
        for name, organization, role, stores in [
            ("editor", "alpha", "importer", ["2", "3"]),
            ("viewer", "alpha", "viewer", ["2"]),
            ("manager", "alpha", "admin", ["3"]),
            ("other", "beta", "importer", ["2"]),
        ]
    }
    hashed = {hashlib.sha256(token(name).encode()).hexdigest(): value for name, value in identities.items()}
    engine = connect(url)
    app = create_app(Settings("local", url, hashed, ()), engine)
    yield TestClient(app), engine
    engine.dispose()

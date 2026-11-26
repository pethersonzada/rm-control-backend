from fastapi import HTTPException, Request
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool


def connect(url):
    options = {"pool_pre_ping": True, "hide_parameters": True}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False, "timeout": 10}
        if url in {"sqlite://", "sqlite:///:memory:"}:
            options["poolclass"] = StaticPool
    else:
        options.update(pool_size=3, max_overflow=2, pool_timeout=5, connect_args={"connect_timeout": 5})
    engine = create_engine(url, **options)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def sqlite_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
    return engine


def database(request: Request):
    if request.app.state.engine is None:
        raise HTTPException(503, "Ambiente operacional indisponível.")
    return request.app.state.engine

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base


def make_session_factory(db_url: str) -> sessionmaker[Session]:
    kwargs: dict = {}
    if db_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in db_url:
            kwargs["poolclass"] = StaticPool
        else:
            path = db_url.split("///", 1)[-1]
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(db_url, **kwargs)
    if db_url.startswith("sqlite") and ":memory:" not in db_url:

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)

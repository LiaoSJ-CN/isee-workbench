"""Database setup for application metadata."""

import logging
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

_connect_args: dict[str, object] = {}
_engine_kwargs: dict[str, int | bool] = {}

if settings.database_url.startswith("sqlite"):
    _connect_args["check_same_thread"] = False
else:
    _engine_kwargs["pool_size"] = settings.db_pool_size
    _engine_kwargs["max_overflow"] = settings.db_max_overflow
    _engine_kwargs["pool_pre_ping"] = True

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    **_engine_kwargs,
)


@event.listens_for(engine, "connect")
def _receive_connect(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
    """Log pool checkouts so pool exhaustion is debuggable.

    Only emits when the logger is at DEBUG level — avoids per-connection
    overhead in production (INFO / WARNING default).
    """
    if not logger.isEnabledFor(logging.DEBUG):
        return
    pool = engine.pool
    if pool is not None:
        logger.debug(
            "DB connection checked out — pool size=%s overflow=%s checked_in=%s",
            pool.size(),  # type: ignore[attr-defined]
            pool.overflow(),  # type: ignore[attr-defined]
            pool.checkedin(),  # type: ignore[attr-defined]
        )


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db() -> Iterator[Session]:
    """Yield a database session for dependency injection."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

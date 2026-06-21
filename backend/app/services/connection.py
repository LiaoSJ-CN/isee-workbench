"""Service for managing connections to external data sources."""

import logging
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.crypto import decrypt as crypto_decrypt
from app.models.data_source import DataSource

logger = logging.getLogger(__name__)


class ConnectionError(Exception):
    """Raised when a connection test fails."""


SUPPORTED_DRIVERS = {
    "opengauss": "postgresql+psycopg2",
    "dws": "postgresql+psycopg2",
    "postgresql": "postgresql+psycopg2",
    "sqlite": "sqlite",
}


def build_connection_url(source: DataSource) -> str:
    """Build a SQLAlchemy connection URL from a DataSource record."""
    driver = SUPPORTED_DRIVERS.get(str(source.db_type))
    if driver is None:
        raise ConnectionError(f"Unsupported database type: {source.db_type}")

    if source.db_type == "sqlite":
        # SQLite uses file path as database
        return f"sqlite:///{source.database}"

    plaintext = crypto_decrypt(str(source.password))
    password = quote_plus(plaintext)
    url = f"{driver}://{source.username}:{password}@{source.host}:{source.port}/{source.database}"
    return url


def test_connection(source: DataSource) -> dict[str, str | bool]:
    """Attempt to connect to the data source and return server version info."""
    url = build_connection_url(source)
    try:
        if source.db_type == "sqlite":
            engine = create_engine(url)
            with engine.connect() as conn:
                result = conn.execute(text("SELECT sqlite_version()"))
                version: str = f"SQLite {result.scalar()}"
        else:
            engine = create_engine(url, connect_args={"connect_timeout": 10})
            with engine.connect() as conn:
                result = conn.execute(text("SELECT version()"))
                version = str(result.scalar())
        engine.dispose()
        return {"success": True, "version": version}
    except SQLAlchemyError as exc:
        logger.error("Connection test failed for source %s: %s", source.id, exc)
        raise ConnectionError("Failed to connect to the data source") from exc

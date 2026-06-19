"""Service for managing connections to external data sources."""

from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.models.data_source import DataSource


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
    driver = SUPPORTED_DRIVERS.get(source.db_type)
    if driver is None:
        raise ConnectionError(f"Unsupported database type: {source.db_type}")

    if source.db_type == "sqlite":
        # SQLite uses file path as database
        return f"sqlite:///{source.database}"

    password = quote_plus(source.password)
    url = f"{driver}://{source.username}:{password}@{source.host}:{source.port}/{source.database}"
    return url


def test_connection(source: DataSource) -> dict:
    """Attempt to connect to the data source and return server version info."""
    url = build_connection_url(source)
    try:
        if source.db_type == "sqlite":
            engine = create_engine(url)
            with engine.connect() as conn:
                result = conn.execute(text("SELECT sqlite_version()"))
                version = f"SQLite {result.scalar()}"
        else:
            engine = create_engine(url, connect_args={"connect_timeout": 10})
            with engine.connect() as conn:
                result = conn.execute(text("SELECT version()"))
                version = result.scalar()
        engine.dispose()
        return {"success": True, "version": version}
    except SQLAlchemyError as exc:
        raise ConnectionError(f"Failed to connect: {exc}") from exc

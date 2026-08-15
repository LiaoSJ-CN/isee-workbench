from sqlalchemy import engine_from_config, pool

from alembic import context

# Load our application config (reads backend/.env) before importing
# app.database — the engine/session factories depend on settings.
from app.config import settings

# Import all models so Base.metadata is fully populated for autogenerate.
from app.database import Base
from app.models import data_source  # noqa: F401
from app.models import rate_limit  # noqa: F401
from app.models import report  # noqa: F401
from app.models import report_parameter  # noqa: F401
from app.models import revoked_token  # noqa: F401
from app.models import user  # noqa: F401

# Alembic Config object
config = context.config

# Note: we intentionally do NOT call ``logging.config.fileConfig`` here.
# The alembic.ini [loggers] section replaces root-logger handlers on
# load, which clobbers the FastAPI lifespan's request-id log factory
# and pytest's ``caplog`` handler. The web process configures its own
# logging in ``app.main._configure_logging`` (called from lifespan);
# CLI runs (``alembic upgrade head``) inherit the user's shell
# logging config, which is fine for one-off DB work.

# Set the database URL dynamically from our app config
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

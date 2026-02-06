"""Alembic environment for CI smoke migrations."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool


def _get_database_url() -> str:
    return os.getenv("DATABASE_URL", "sqlite:///./local.db")


def run_migrations_offline() -> None:
    url = _get_database_url()
    context.configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(
        _get_database_url(),
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

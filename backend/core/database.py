"""Optional PostgreSQL engine factory for the multi-user foundation.

The application's existing SQLite runtime is deliberately unaffected until a
separate, reviewed PostgreSQL cutover.
"""
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from core.config import settings


def create_postgres_engine(database_url: str | None = None):
    url_value = database_url or settings.database_url
    if not url_value:
        raise RuntimeError("FLOWPILOT_DATABASE_URL is required for PostgreSQL operations")
    url = make_url(url_value)
    if url.get_backend_name() != "postgresql":
        raise ValueError("The multi-user database foundation requires PostgreSQL")
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10)


def create_session_factory(engine=None):
    engine = engine or create_postgres_engine()
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

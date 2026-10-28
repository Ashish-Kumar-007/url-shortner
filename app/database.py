from urllib.parse import urlsplit

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings


def _normalize_database_url(database_url: str) -> str:
    scheme = urlsplit(database_url).scheme.lower()
    if scheme not in {"sqlite", "postgres", "postgresql", "postgresql+psycopg"}:
        raise ValueError(
            "DATABASE_URL must be a PostgreSQL connection string beginning with "
            "'postgresql://' (or 'postgres://'), not a Supabase HTTPS project/API "
            "URL. In Supabase, open Connect and copy the PostgreSQL connection "
            "string; do not use the Project URL."
        )
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgres://")
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgresql://")
    return database_url


def create_database(
    settings: Settings,
) -> tuple[Engine, sessionmaker[Session]]:
    database_url = _normalize_database_url(settings.database_url)
    if database_url.startswith("sqlite"):
        connect_args = {
            "check_same_thread": False,
            "timeout": settings.db_pool_timeout_seconds,
        }
    else:
        connect_args = {
            "connect_timeout": settings.db_connect_timeout_seconds,
            "options": f"-c statement_timeout={settings.db_statement_timeout_ms}",
            "application_name": "url-shortener",
        }
    engine = create_engine(
        database_url,
        connect_args=connect_args,
        pool_pre_ping=True,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    return engine, session_factory

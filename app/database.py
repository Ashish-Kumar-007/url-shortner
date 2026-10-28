from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings


def create_database(
    settings: Settings,
) -> tuple[Engine, sessionmaker[Session]]:
    connect_args = {"check_same_thread": False} if settings.database_url.startswith(
        "sqlite"
    ) else {}
    engine = create_engine(
        settings.database_url,
        connect_args=connect_args,
        pool_pre_ping=True,
    )
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    return engine, session_factory

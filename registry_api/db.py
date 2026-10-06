from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from registry_api.settings import get_settings


class Base(DeclarativeBase):
    pass


def make_engine():
    settings = get_settings()
    lock_timeout_ms = round(settings.database_lock_timeout_seconds * 1000)
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"options": f"-c lock_timeout={lock_timeout_ms}"},
    )


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


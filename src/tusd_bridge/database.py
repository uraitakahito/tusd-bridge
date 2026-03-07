"""Database engine and session configuration."""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DB_PATH = Path("data/tusd_bridge.db")


def get_database_url(db_path: Path = DEFAULT_DB_PATH) -> str:
    return f"sqlite:///{db_path}"


def get_engine(db_path: Path = DEFAULT_DB_PATH) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(get_database_url(db_path), echo=False)


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine)

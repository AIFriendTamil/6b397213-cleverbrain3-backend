"""Declarative ORM Base and database session configuration."""

import typing
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
import sqlalchemy.util.typing


def _fixed_make_union_type(*types):
    if not types:
        return typing.Any
    if len(types) == 1:
        return types[0]
    return typing.Union[types]


sqlalchemy.util.typing.make_union_type = _fixed_make_union_type

from src.lib.config import settings


def create_db_engine(url: str | None = None) -> Engine:
    """Create and configure a synchronous SQLAlchemy database engine."""
    db_url = url or settings.DATABASE_URL
    connect_args = {}
    if db_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    try:
        return create_engine(
            db_url,
            connect_args=connect_args,
            pool_pre_ping=True,
        )
    except (ImportError, ModuleNotFoundError):
        if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
            pg_psycopg_url = db_url.replace("postgres://", "postgresql+psycopg://", 1).replace(
                "postgresql://", "postgresql+psycopg://", 1
            )
            try:
                return create_engine(
                    pg_psycopg_url,
                    connect_args={},
                    pool_pre_ping=True,
                )
            except (ImportError, ModuleNotFoundError):
                pass

        fallback_url = "sqlite:////workspace/backend/src/lib/portal.db"
        return create_engine(
            fallback_url,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
        )


# Synchronous engine instance using settings configuration
engine = create_db_engine()

# Synchronous session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


class Base(DeclarativeBase):
    """Declarative base class for all SQLAlchemy ORM models."""

    pass


def get_db() -> Generator[Session, None, None]:
    """Provide a database session lifecycle generator for FastAPI dependencies."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

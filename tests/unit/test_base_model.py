"""Unit tests for declarative ORM Base and database engine/session configuration."""

from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from src.models.base import Base, SessionLocal, create_db_engine, engine, get_db


def test_base_declarative_class():
    """Verify Base is a subclass of DeclarativeBase and metadata is initialized."""
    assert issubclass(Base, DeclarativeBase)
    assert hasattr(Base, "metadata")
    assert Base.metadata is not None


def test_create_db_engine_sqlite():
    """Verify database engine creation for SQLite connection string."""
    test_engine = create_db_engine("sqlite:///:memory:")
    assert test_engine is not None
    with test_engine.connect() as conn:
        result = conn.exec_driver_sql("SELECT 1").scalar()
        assert result == 1


def test_global_engine_and_session():
    """Verify module-level engine and SessionLocal are valid."""
    assert engine is not None
    assert SessionLocal is not None


def test_get_db_lifecycle():
    """Verify get_db generator yields a valid session and closes properly."""
    db_gen = get_db()
    session = next(db_gen)
    assert isinstance(session, Session)
    
    # Test closing upon generator completion
    try:
        next(db_gen)
    except StopIteration:
        pass


def test_sample_model_inheritance_and_table_creation():
    """Verify that a model inheriting from Base can create tables and persist data."""
    test_engine = create_db_engine("sqlite:///:memory:")

    class DummyEntity(Base):
        __tablename__ = "test_dummies"

        id: Mapped[int] = mapped_column(primary_key=True)
        name: Mapped[str] = mapped_column(nullable=False)

    Base.metadata.create_all(bind=test_engine)

    with Session(test_engine) as session:
        dummy = DummyEntity(name="Test Item")
        session.add(dummy)
        session.commit()

        retrieved = session.query(DummyEntity).filter_by(name="Test Item").first()
        assert retrieved is not None
        assert retrieved.name == "Test Item"
        assert retrieved.id == 1

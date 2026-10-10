"""SQLite-verbinding (één bestand: sfdojo.db = makkelijk te back-uppen)."""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

engine = create_engine(os.getenv("DATABASE_URL", "sqlite:///./sfdojo.db"),
                       connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()


def get_db():
    """Eén database-sessie per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

"""Databasetabellen."""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text

from .database import Base


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    """Exact 3 beheerders (aangemaakt door seed.py; er is geen registratiepagina)."""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(100), nullable=False)


class Section(Base):
    """Een tekstblok op een pagina. lang = nl/fr/en/es/ja of 'all' (in elke taal tonen)."""
    __tablename__ = "sections"
    id = Column(Integer, primary_key=True)
    page = Column(String(30), nullable=False)
    lang = Column(String(5), nullable=False, default="nl")
    title = Column(String(200), nullable=False)
    content = Column(Text, default="")
    position = Column(Integer, default=0)


class Slide(Base):
    """Hero-slideshow: bestand in app/static/images/."""
    __tablename__ = "slides"
    id = Column(Integer, primary_key=True)
    filename = Column(String(100), nullable=False)
    caption = Column(String(200), default="")


class LoginBlock(Base):
    """Mislukte logins per IP + escalerende ban (24u, 48u, 96u, ...)."""
    __tablename__ = "login_blocks"
    ip = Column(String(64), primary_key=True)
    fails = Column(Integer, default=0)      # mislukte pogingen in de huidige ronde
    strikes = Column(Integer, default=0)    # hoeveel keer dit IP al geband werd
    banned_until = Column(DateTime, nullable=True)

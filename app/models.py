"""Databasetabellen."""
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, String, Text

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


class Media(Base):
    """Geüploade foto of video (map uploads/). in_hero = tonen in de slideshow op de home."""
    __tablename__ = "media"
    id = Column(Integer, primary_key=True)
    name = Column(String(40), unique=True, nullable=False)   # willekeurige bestandsnaam, nooit die van de gebruiker
    kind = Column(String(10), nullable=False)                # image | video
    caption = Column(String(200), default="")
    in_hero = Column(Boolean, default=False)


class Post(Base):
    """Blogbericht. lang = nl/fr/en/es/ja of 'all'."""
    __tablename__ = "posts"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, default="")
    lang = Column(String(5), nullable=False, default="nl")
    created = Column(DateTime, default=utcnow)


class Totp(Base):
    """Tweestapsverificatie (authenticator-app) per beheerder. enabled pas na een geslaagde bevestiging."""
    __tablename__ = "totp"
    user_id = Column(Integer, primary_key=True)
    secret = Column(String(64), nullable=False)
    enabled = Column(Boolean, default=False)
    last_step = Column(Integer, default=0)       # laatst gebruikte tijdsblok: een code werkt maar één keer
    recovery = Column(Text, default="")          # hashes van de herstelcodes, komma-gescheiden


class Closure(Base):
    """Vakantie of uitzondering op het lessenrooster. kind: closed (geen training) | reduced (minder lessen)."""
    __tablename__ = "closures"
    id = Column(Integer, primary_key=True)
    title = Column(String(120), nullable=False)
    date_from = Column(Date, nullable=False)
    date_to = Column(Date, nullable=False)
    kind = Column(String(10), nullable=False, default="closed")
    note = Column(String(300), default="")
    classes = Column(String(200), default="")    # bij 'reduced': lessen die WEL doorgaan (woorden uit de lesnaam, komma-gescheiden)

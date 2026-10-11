"""Login, sessies, CSRF en de escalerende IP-ban."""
import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from urllib.parse import quote
from datetime import timedelta

import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .database import get_db
from .models import LoginBlock, User, utcnow

MAX_FAILS = 3            # 3 fout = ban
BASE_BAN_HOURS = 24      # 1e ban 24u, 2e 48u, 3e 96u, ... (verdubbelt)
MAX_BAN_HOURS = 24 * 365
_DUMMY = bcrypt.hashpw(b"dummy", bcrypt.gensalt()).decode()  # zelfde rekentijd bij onbekende gebruiker


class LoginRequired(Exception):
    pass


class SetupRequired(Exception):
    """Ingelogd, maar tweestapsverificatie moet nog ingesteld worden voor de rest van het beheer."""


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 15
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 10**6:06d}"


def verify_totp(secret: str, code: str, last_step: int = 0, now=None):
    """Controleert een 6-cijferige code (RFC 6238, SHA1, 30s, 1 blok marge). Geeft het tijdsblok of None; hergebruik wordt geweigerd."""
    code = "".join(c for c in code if c.isdigit())
    if len(code) != 6:
        return None
    step = int((now if now is not None else time.time()) // 30)
    for s in (step - 1, step, step + 1):
        if s > last_step and hmac.compare_digest(_hotp(secret, s), code):
            return s
    return None


def otpauth_uri(secret: str, username: str, issuer: str = "SF Dojo") -> str:
    return (f"otpauth://totp/{quote(issuer)}:{quote(username)}?secret={secret}&issuer={quote(issuer)}"
            "&algorithm=SHA1&digits=6&period=30")


def new_recovery_codes(n: int = 8):
    return [f"{(h := secrets.token_hex(5))[:5]}-{h[5:]}" for _ in range(n)]


def hash_recovery(code: str) -> str:
    norm = "".join(c for c in code.lower() if c.isalnum())
    return hmac.new(os.environ["SECRET_KEY"].encode(), norm.encode(), hashlib.sha256).hexdigest()


def use_recovery(totp, code: str) -> bool:
    """Gebruikt een herstelcode (eenmalig). Past totp.recovery aan; de aanroeper commit."""
    h, left = hash_recovery(code), [x for x in (totp.recovery or "").split(",") if x]
    hit = next((x for x in left if hmac.compare_digest(x, h)), None)
    if hit is None:
        return False
    left.remove(hit)
    totp.recovery = ",".join(left)
    return True


def _b(pw: str) -> bytes:
    return pw.encode()[:72]          # bcrypt 5 weigert > 72 bytes


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(_b(pw), bcrypt.gensalt()).decode()


def session_mark(user) -> str:
    """Stempel in de sessie: verandert het wachtwoord, dan zijn alle oude sessies meteen ongeldig."""
    return user.password_hash[-16:]


def client_ip(request: Request) -> str:
    """Achter nginx/Caddy: zet TRUST_PROXY=1 zodat X-Forwarded-For gebruikt wordt."""
    if os.getenv("TRUST_PROXY") == "1":
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()[:64]
    return request.client.host if request.client else "unknown"


def banned_until(db: Session, ip: str):
    b = db.get(LoginBlock, ip)
    return b.banned_until if b and b.banned_until and b.banned_until > utcnow() else None


def record_failure(db: Session, ip: str):
    """Telt een mislukte poging; na MAX_FAILS volgt een ban die telkens verdubbelt. Geeft (status, extra)."""
    b = db.get(LoginBlock, ip) or LoginBlock(ip=ip, fails=0, strikes=0)
    db.add(b)
    b.fails += 1
    if b.fails >= MAX_FAILS:
        b.strikes += 1
        b.fails = 0
        hours = min(BASE_BAN_HOURS * 2 ** (b.strikes - 1), MAX_BAN_HOURS)
        b.banned_until = utcnow() + timedelta(hours=hours)
        db.commit()
        return "banned", b.banned_until
    db.commit()
    return "fail", MAX_FAILS - b.fails


def clear_failures(db: Session, ip: str):
    b = db.get(LoginBlock, ip)
    if b:
        db.delete(b)
        db.commit()


def try_login(db: Session, ip: str, username: str, password: str, clear: bool = True):
    """Geeft (user, status, extra). status: ok | fail (extra = pogingen over) | banned (extra = tot wanneer).
    clear=False: de teller niet wissen na een goed wachtwoord (nodig als daarna nog Google/Microsoft volgt)."""
    until = banned_until(db, ip)
    if until:                                   # geband IP: wachtwoord wordt niet eens gecontroleerd
        return None, "banned", until
    user = db.query(User).filter_by(username=username.strip().lower()).first()
    ok = bcrypt.checkpw(_b(password), (user.password_hash if user else _DUMMY).encode())
    if user and ok:
        if clear:
            clear_failures(db, ip)
        return user, "ok", None
    status, extra = record_failure(db, ip)
    return None, status, extra


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    if not user or request.session.get("ph") != session_mark(user):
        raise LoginRequired()
    if request.session.get("must_2fa") and not request.url.path.startswith(("/admin/password", "/admin/security")):
        raise SetupRequired()
    return user


def csrf_token(request: Request) -> str:
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(24)
    return request.session["csrf"]


def csrf_guard(request: Request):
    """HTMX stuurt de token mee als header (zie hx-headers in base.html)."""
    real = request.session.get("csrf")
    if not real or not secrets.compare_digest(request.headers.get("x-csrf-token", ""), real):
        raise HTTPException(403, "CSRF-controle mislukt")

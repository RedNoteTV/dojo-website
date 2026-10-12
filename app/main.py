"""SF Dojo – FastAPI-app: publieke site + beheerpaneel. Alle routes staan in dit bestand."""
import calendar
import logging
import warnings
import os
import re
import time
from html import escape
import secrets
from datetime import date, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
warnings.filterwarnings("ignore", message="The httpx module is deprecated")   # ruis van Authlib, onschadelijk
import segno
from authlib.integrations.starlette_client import OAuth
from markupsafe import Markup
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import auth, crud
from .database import Base, SessionLocal, engine, get_db
from .i18n import LANGS, NAMES, TEXT
from .models import Book, Closure, Media, Post, Section, Totp, User

BASE = Path(__file__).parent
HTTPS = os.getenv("HTTPS_ONLY", "0") == "1"
SECRET = os.getenv("SECRET_KEY")
if not SECRET:
    raise RuntimeError("SECRET_KEY ontbreekt: kopieer .env.example naar .env en vul die in.")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                    handlers=[logging.StreamHandler(), logging.FileHandler(os.getenv("LOG_FILE") or "sfdojo-events.log")])
Base.metadata.create_all(engine)

# ---- inloggen: AUTH_MODE = password | password_sso (wachtwoord + Google/Microsoft) | sso (alleen Google/Microsoft)
AUTH_MODE = (os.getenv("AUTH_MODE") or "auto").strip()
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
oauth, PROVIDERS = OAuth(), {}
if os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"):
    oauth.register("google", client_id=os.environ["GOOGLE_CLIENT_ID"], client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
                   server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
                   client_kwargs={"scope": "openid email", "code_challenge_method": "S256"})
    PROVIDERS["google"] = "Google"
if os.getenv("MICROSOFT_CLIENT_ID") and os.getenv("MICROSOFT_CLIENT_SECRET"):
    tenant = os.getenv("MICROSOFT_TENANT", "consumers")        # 'consumers' = privé-accounts, of de tenant-id van een werkaccount
    oauth.register("microsoft", client_id=os.environ["MICROSOFT_CLIENT_ID"], client_secret=os.environ["MICROSOFT_CLIENT_SECRET"],
                   server_metadata_url=f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration",
                   client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"})
    PROVIDERS["microsoft"] = "Microsoft"


def admin_emails():
    """E-mailadres -> gebruikersnaam, uit ADMIN1_EMAIL.. (per beheerder mag je meerdere adressen scheiden met een komma)."""
    out = {}
    for i, default in ((1, "dev"), (2, "righthand"), (3, "leader")):
        name = os.getenv(f"ADMIN{i}_USER", default).strip().lower()
        for e in os.getenv(f"ADMIN{i}_EMAIL", "").split(","):
            if e.strip():
                out[e.strip().lower()] = name
    return out


if AUTH_MODE == "auto":                    # Google/Microsoft staat uit, tot client-id, secret, adres en e-mails ingevuld zijn
    AUTH_MODE = "password_sso" if (PROVIDERS and PUBLIC_URL and admin_emails()) else "password"
    if PROVIDERS and AUTH_MODE == "password":
        logging.getLogger("sfdojo").warning("Google/Microsoft is ingevuld maar PUBLIC_URL of ADMINx_EMAIL ontbreekt: SSO blijft uit.")
USE_TOTP = AUTH_MODE == "password"         # bij Google/Microsoft-login zorgt dat account zelf voor de tweede stap
REQUIRE_2FA = USE_TOTP and os.getenv("REQUIRE_2FA", "1") == "1"      # beheerders moeten tweestapsverificatie instellen
if AUTH_MODE not in ("password", "password_sso", "sso"):
    raise RuntimeError("AUTH_MODE moet password, password_sso of sso zijn.")
if AUTH_MODE != "password":                # liever meteen een duidelijke fout dan een beheer waar niemand meer in kan
    missing = [t for t, ok in (("GOOGLE_* of MICROSOFT_* (client id en secret)", PROVIDERS), ("PUBLIC_URL", PUBLIC_URL),
                               ("ADMIN1_EMAIL / ADMIN2_EMAIL / ADMIN3_EMAIL", admin_emails())) if not ok]
    if missing:
        raise RuntimeError(f"AUTH_MODE={AUTH_MODE} maar in .env ontbreekt: {', '.join(missing)}")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET, session_cookie="sfdojo_session",
                   max_age=8 * 3600, same_site="lax", https_only=HTTPS)
UPLOADS = Path(os.getenv("UPLOAD_DIR") or BASE.parent / "uploads")           # geüploade foto's en video's (back-up mee nemen!)
UPLOADS.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
app.mount("/media", StaticFiles(directory=UPLOADS), name="media")
templates = Jinja2Templates(directory=BASE / "templates")

PAGES = {"about": "page.html", "disciplines": "page.html", "schedule": "schedule.html",
         "prices": "prices.html", "contact": "contact.html", "payments": "payments.html"}
# Paginanamen zoals de beheerder ze ziet (home en footer hebben geen eigen route)
PAGE_LABELS = {"home": "Home", "promo": "Home: banner onderaan (bv. Skool-course)", "faq": "FAQ: veelgestelde vragen", "about": "Over ons", "disciplines": "Disciplines", "schedule": "Lessenrooster",
               "prices": "Prijzen", "contact": "Contact", "payments": "Betalingen",
               "nav": "Menu: extra items", "footerlinks": "Footer: extra links",
               "footer": "Footer: social media"}
THEMES = ["", "light", "dark"]          # "" = automatisch (volgt het toestel)


MEDIA_RE = re.compile(r"^\[(foto|video|bestand):\s*([0-9a-f]{16}\.(?:jpg|png|gif|webp|mp4|webm|pdf))(?:\s+([^\[\]|]{1,80}))?\]$")


YT_RE = re.compile(r"^\[youtube:\s*([A-Za-z0-9_-]{11})\]$")
YT_URL = re.compile(r"(?:youtu\.be/|youtube(?:-nocookie)?\.com/(?:watch\?(?:[^\s#]*&)?v=|embed/|shorts/|live/|v/))([A-Za-z0-9_-]{11})")
URL_RE = re.compile(r"(https?://[^\s<>\"']+[^\s<>\"'.,;:!?)])")


def youtube_id(s):
    """Plak-link (watch, youtu.be, embed, shorts, live) of kale video-id -> 11-tekens id, anders None."""
    s = (s or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    m = YT_URL.search(s)
    return m.group(1) if m else None


def linkify(text):
    """Maakt gewone https://-adressen in een tekst klikbaar. Eerst ge-escaped, dus geen HTML mogelijk."""
    esc = str(escape(text))
    return Markup(URL_RE.sub(lambda m: f'<a href="{m.group(1)}" target="_blank" rel="noopener nofollow">{m.group(1)}</a>', esc))


def _media_item(line):
    m = MEDIA_RE.match(line)
    if m:
        return m.groups()
    y = YT_RE.match(line)
    return ("youtube", y.group(1), None) if y else None


def blocks(text):
    """Tekst -> blokken. Regels met '|' = tabel; [foto: x] / [video: x] / [bestand: x] = media (opeenvolgend = galerij)."""
    out = []
    for para in (text or "").replace("\r", "").split("\n\n"):
        lines = [l for l in para.split("\n") if l.strip()]
        if not lines:
            continue
        ms = [_media_item(l.strip()) for l in lines]
        if all(ms):
            items = ms
            if out and "media" in out[-1]:
                out[-1]["media"] += items
            else:
                out.append({"media": items})
        elif all("|" in l for l in lines):
            out.append({"rows": [[c.strip() for c in l.split("|")] for l in lines]})
        else:
            out.append({"text": "\n".join(lines)})
    return out


# ---- lessenrooster: tabel "Dag | Uur | Les | Voor wie" wordt een weekraster
DAYS_NL = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
AUDIENCE = {"iedereen": "everyone", "volwassenen": "adults"}
KIND_LABELS = {"karate": "Karate", "striking": "Striking / MMA", "grappling": "Grappling",
               "gkarate": "G-Karate", "open": "Open Dojo", "other": "Overig"}


def kind_of(name):
    n = name.lower()
    if "open dojo" in n:
        return "open"
    if "g karate" in n or "g-karate" in n:
        return "gkarate"
    if "grappling" in n:
        return "grappling"
    if any(w in n for w in ("striking", "mma", "kickbox")):
        return "striking"
    return "karate" if "karate" in n else "other"


def today_date():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Brussels")).date()
    except Exception:
        return datetime.now().date()


def today_idx():
    return today_date().weekday()


def schedule(text, lang):
    """Geeft het raster voor schedule.html, of None als de tekst geen rooster is."""
    t = TEXT[lang]
    for b in blocks(text):
        rows = b.get("rows")
        if not rows or rows[0][0].strip().lower() != "dag":
            continue
        items = []
        for r in rows[1:]:
            r = r + [""] * (4 - len(r))
            day = r[0].strip().lower()
            if day not in DAYS_NL:
                continue
            start, _, end = r[1].replace("–", "-").partition("-")
            who = r[3].strip()
            items.append(dict(d=DAYS_NL.index(day), start=start.strip(), name=r[2].strip(),
                              time=f"{start.strip()} – {end.strip()}" if end else start.strip(),
                              who=t.get(AUDIENCE.get(who.lower()), who), kind=kind_of(r[2])))
        if not items:
            return None
        days = sorted({i["d"] for i in items})
        slots = sorted({i["start"] for i in items})
        col = {d: n + 1 for n, d in enumerate(days)}
        now = today_idx()
        for i in items:
            i.update(col=col[i["d"]], row=slots.index(i["start"]) + 2, today=i["d"] == now)
        items.sort(key=lambda i: (i["d"], i["start"]))
        return dict(ncols=len(days), cards=items,
                    heads=[dict(label=t["days"][d], col=col[d], today=d == now) for d in days],
                    kinds=[(k, v) for k, v in KIND_LABELS.items() if any(i["kind"] == k for i in items)])
    return None


def footer_links():
    """Social-media-links uit de sectie(s) van pagina 'footer' (regels: 'Naam | https://...')."""
    db = SessionLocal()
    try:
        out = []
        for s in db.query(Section).filter(Section.page == "footer").order_by(Section.position, Section.id):
            for b in blocks(s.content):
                for r in b.get("rows", []):
                    if len(r) >= 2 and r[1].lower().startswith(("https://", "http://")):   # geen javascript:-links
                        out.append((r[0], r[1]))
        return out[:8]
    finally:
        db.close()


def normalize_link(u):
    """Maakt van wat de beheerder typt een veilig adres: /blog, blog, www.x.be, https://..., mailto:, tel:."""
    u = (u or "").strip()
    if not u or any(c in u for c in ' <>"\''):
        return None
    if u.startswith("/"):
        return None if u.startswith("//") else u
    if u.lower().startswith(("https://", "http://", "mailto:", "tel:")):
        return u
    if ":" in u:                                   # javascript:, data:, ...
        return None
    if "@" in u and "/" not in u:
        return "mailto:" + u
    if "." in u.split("/")[0]:
        return "https://" + u
    return "/" + u


def link_items(page, lang):
    """Extra menu-/footerlinks uit de database: titel = tekst, inhoud = adres (/pad of https://...)."""
    db = SessionLocal()
    try:
        out = []
        for s in crud.sections_for(db, page, lang):
            u = normalize_link(s.content)
            if u:
                out.append((s.title, u))
        return out[:10]
    finally:
        db.close()


def faq_items(lang):
    db = SessionLocal()
    try:
        return crud.sections_for(db, "faq", lang)
    finally:
        db.close()


def promo(lang):
    """Opvallende banner onderaan de home: titel, tekst en max. 2 knoppen (regels 'Knoptekst | https://...')."""
    db = SessionLocal()
    try:
        secs = crud.sections_for(db, "promo", lang)
        if not secs:
            return None
        text, buttons = [], []
        for b in blocks(secs[0].content):
            if "rows" in b:
                buttons += [(r[0], u) for r in b["rows"] if len(r) > 1 and (u := normalize_link(r[1]))]
            elif "text" in b:
                text.append(b["text"])
        return dict(title=secs[0].title, text=text, buttons=buttons[:2])
    finally:
        db.close()


def books():
    db = SessionLocal()
    try:
        return db.query(Book).order_by(Book.position, Book.id).all()
    finally:
        db.close()


def excerpt(text, n=180):
    for b in blocks(text):
        if "text" in b:
            t = b["text"].replace("\n", " ")
            return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"
    return ""


templates.env.globals.update(blocks=blocks, schedule=schedule, footer_links=footer_links, page_labels=PAGE_LABELS,
                             lang_names=NAMES, year=lambda: datetime.now().year, link_items=link_items, excerpt=excerpt,
                             faq_items=faq_items, promo=promo, books=books)
templates.env.filters["linkify"] = linkify


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "frame-src https://www.youtube-nocookie.com; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    if request.url.path.startswith("/admin"):
        resp.headers["Cache-Control"] = "no-store"
    if HTTPS:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
    return resp


@app.exception_handler(auth.LoginRequired)
async def need_login(request: Request, exc):
    if request.headers.get("hx-request"):
        return Response(headers={"HX-Redirect": "/admin/login"})
    return RedirectResponse("/admin/login", 303)


@app.exception_handler(auth.SetupRequired)
async def need_setup(request: Request, exc):
    if request.headers.get("hx-request"):
        return Response(headers={"HX-Redirect": "/admin/password?setup=1"})
    return RedirectResponse("/admin/password?setup=1", 303)


def get_lang(request: Request) -> str:
    q = request.query_params.get("lang")
    if q in LANGS:
        return q
    c = request.cookies.get("lang")
    return c if c in LANGS else "nl"


def render(request: Request, template: str, **ctx):
    lang = get_lang(request)
    theme = request.cookies.get("theme", "")
    ctx.update(lang=lang, t=TEXT[lang], langs=LANGS, theme=theme if theme in THEMES else "",
               csrf=auth.csrf_token(request) if request.url.path.startswith("/admin") else "")
    resp = templates.TemplateResponse(request, template, ctx)
    if request.query_params.get("lang") in LANGS:       # ?lang=fr onthouden in een cookie
        resp.set_cookie("lang", lang, max_age=31536000, samesite="lax", secure=HTTPS, httponly=True)
    return resp


# ---------------------------------------------------------------- publieke site
@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    sl = crud.slides(db)
    return render(request, "index.html", sections=crud.sections_for(db, "home", get_lang(request)),
                  slide=sl[0] if sl else None, idx=0, total=len(sl))


def make_view(slug: str, template: str):
    def view(request: Request, db: Session = Depends(get_db)):
        lang = get_lang(request)
        return render(request, template, title=TEXT[lang][slug],
                      sections=crud.sections_for(db, slug, lang))
    return view


for _slug, _tpl in PAGES.items():
    app.get(f"/{_slug}", response_class=HTMLResponse)(make_view(_slug, _tpl))


@app.get("/hero/slide/{n}", response_class=HTMLResponse)
def hero(request: Request, n: int, db: Session = Depends(get_db)):
    sl = crud.slides(db)
    if not sl:
        return HTMLResponse("")
    n %= len(sl)
    return render(request, "partials/hero.html", slide=sl[n], idx=n, total=len(sl))


@app.post("/theme")
def theme(set: str = ""):
    resp = Response(headers={"HX-Refresh": "true"})
    resp.set_cookie("theme", set if set in ("light", "dark") else "", max_age=31536000,
                    samesite="lax", secure=HTTPS, httponly=True)
    return resp


# ---------------------------------------------------------------- lessenrooster: vakanties en kalender
def lessons_for(db, lang):
    for sec in crud.sections_for(db, "schedule", lang):
        g = schedule(sec.content, lang)
        if g:
            return g["cards"]
    return []


def day_info(lessons, periods, day):
    """Wat gebeurt er op deze dag? status: open | none (geen lessen op die weekdag) | closed | reduced."""
    ps = [p for p in periods if p.date_from <= day <= p.date_to]
    todays = sorted((l for l in lessons if l["d"] == day.weekday()), key=lambda l: l["start"])
    closed = next((p for p in ps if p.kind == "closed"), None)       # 'geen training' wint van 'minder lessen'
    if closed:
        return dict(status="closed", period=closed, lessons=[], cancelled=todays)
    reduced = next((p for p in ps if p.kind == "reduced"), None)
    if reduced:
        keys = [k.strip().lower() for k in reduced.classes.split(",") if k.strip()]
        keep = [l for l in todays if not keys or any(k in l["name"].lower() for k in keys)]
        return dict(status="reduced", period=reduced, lessons=keep, cancelled=[l for l in todays if l not in keep])
    return dict(status="open" if todays else "none", period=None, lessons=todays, cancelled=[])


def fmt_range(a, b):
    f = lambda d: f"{d.day}/{d.month}"
    return f(a) if a == b else f"{f(a)} – {f(b)}"


@app.get("/schedule/calendar", response_class=HTMLResponse)
def calendar_view(request: Request, y: int = 0, m: int = 0, db: Session = Depends(get_db)):
    today = today_date()
    if not (2000 <= y <= 2100 and 1 <= m <= 12):
        y, m = today.year, today.month
    weeks = calendar.Calendar(0).monthdatescalendar(y, m)
    periods = db.query(Closure).filter(Closure.date_to >= weeks[0][0], Closure.date_from <= weeks[-1][-1]).all()
    lessons = lessons_for(db, get_lang(request))
    rows = [[dict(day=d, other=d.month != m, today=d == today, info=day_info(lessons, periods, d)) for d in w] for w in weeks]
    return render(request, "partials/calendar.html", rows=rows, y=y, m=m,
                  prev=(y - 1, 12) if m == 1 else (y, m - 1), nxt=(y + 1, 1) if m == 12 else (y, m + 1))


@app.get("/schedule/day", response_class=HTMLResponse)
def day_view(request: Request, d: str, db: Session = Depends(get_db)):
    try:
        day = date.fromisoformat(d)
    except ValueError:
        raise HTTPException(404)
    lang = get_lang(request)
    t = TEXT[lang]
    periods = db.query(Closure).filter(Closure.date_from <= day, Closure.date_to >= day).all()
    return render(request, "partials/day.html", info=day_info(lessons_for(db, lang), periods, day),
                  title=f"{t['days'][day.weekday()]} {day.day} {t['months'][day.month - 1]} {day.year}")


@app.get("/schedule/today", response_class=HTMLResponse)
def today_view(request: Request, db: Session = Depends(get_db)):
    today = today_date()
    periods = db.query(Closure).filter(Closure.date_to >= today).order_by(Closure.date_from).limit(6).all()
    info = day_info(lessons_for(db, get_lang(request)), periods, today)
    upcoming = [dict(p=p, label=fmt_range(p.date_from, p.date_to), now=p.date_from <= today) for p in periods[:4]]
    return render(request, "partials/today.html", info=info, upcoming=upcoming)


@app.get("/embed/youtube/{vid}", response_class=HTMLResponse)
def youtube_embed(vid: str):
    """Wordt pas bij een klik geladen (HTMX): zo maakt de pagina geen verbinding met YouTube voor iemand erop klikt."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
        raise HTTPException(404)
    return HTMLResponse('<div class="yt-frame"><iframe src="https://www.youtube-nocookie.com/embed/' + vid +
                        '?autoplay=1&amp;rel=0" title="YouTube" loading="lazy" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" '
                        'allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe></div>')


@app.get("/books", response_class=HTMLResponse)
def books_page(request: Request):
    return render(request, "books.html", title=TEXT[get_lang(request)]["books"])


@app.get("/blog", response_class=HTMLResponse)
def blog(request: Request, db: Session = Depends(get_db)):
    lang = get_lang(request)
    return render(request, "blog.html", title=TEXT[lang]["blog"], posts=crud.posts_for(db, lang))


@app.get("/blog/{pid}", response_class=HTMLResponse)
def blog_post(request: Request, pid: int, db: Session = Depends(get_db)):
    post = db.get(Post, pid)
    if not post:
        raise HTTPException(404)
    return render(request, "blog_post.html", title=post.title, post=post)


# ---------------------------------------------------------------- beheer: login
def _login_page(request, error=None):
    return render(request, "admin/login.html", mode=AUTH_MODE, providers=PROVIDERS, error=error)


def _has_totp(db, user):
    t = db.get(Totp, user.id)
    return bool(t and t.enabled)


def _start_session(request, db, user, ip, via):
    request.session.clear()                             # nieuwe sessie na login
    request.session["uid"] = user.id
    request.session["ph"] = auth.session_mark(user)
    if REQUIRE_2FA and not _has_totp(db, user):
        request.session["must_2fa"] = 1                 # eerst tweestapsverificatie instellen
    auth.clear_failures(db, ip)
    crud.log_event("login_ok", user=user.username, ip=ip, via=via)


def _finish(request, db, user, ip, via, htmx):
    """Laatste stap na wachtwoord/SSO: nog een 2FA-code vragen, of de sessie starten."""
    def go(url):
        return Response(headers={"HX-Redirect": url}) if htmx else RedirectResponse(url, 303)
    if USE_TOTP and _has_totp(db, user):
        request.session.clear()
        request.session.update(totp_uid=user.id, totp_ts=int(time.time()), totp_via=via)
        return go("/admin/2fa")
    _start_session(request, db, user, ip, via)
    return go("/admin/password?setup=1" if request.session.get("must_2fa") else "/admin")


def _pending(request, db):
    """Gebruiker die het wachtwoord al goed had en nu nog Google/Microsoft moet bevestigen (max. 10 minuten)."""
    uid = request.session.get("pending_uid")
    fresh = time.time() - request.session.get("pending_ts", 0) < 600
    return db.get(User, uid) if uid and fresh else None


@app.get("/admin/login", response_class=HTMLResponse)
def login_page(request: Request):
    return _login_page(request)


@app.post("/admin/login", dependencies=[Depends(auth.csrf_guard)])
def login(request: Request, username: str = Form(""), password: str = Form(""),
          db: Session = Depends(get_db)):
    if AUTH_MODE == "sso":
        return HTMLResponse('<span class="err">Inloggen kan alleen met Google of Microsoft.</span>')
    ip = auth.client_ip(request)
    user, status, extra = auth.try_login(db, ip, username[:50], password[:200], clear=False)
    if status == "ok":
        if AUTH_MODE == "password_sso":                 # stap 2: bevestigen met Google/Microsoft
            request.session.clear()
            request.session["pending_uid"], request.session["pending_ts"] = user.id, int(time.time())
            crud.log_event("login_password_ok", user=user.username, ip=ip)
            return Response(headers={"HX-Redirect": "/admin/sso"})
        return _finish(request, db, user, ip, "password", htmx=True)
    crud.log_event("login_fail" if status == "fail" else "login_blocked", ip=ip, user=username[:30])
    if status == "banned":
        msg = f"Te veel mislukte pogingen. Dit toestel is geblokkeerd tot {extra:%d-%m-%Y %H:%M} (UTC)."
    else:
        msg = f"Onjuiste gebruikersnaam of wachtwoord. Nog {extra} poging(en) voor een blokkade."
    return HTMLResponse(f'<span class="err">{msg}</span>')   # status 200 zodat HTMX het toont


@app.get("/admin/sso", response_class=HTMLResponse)
def sso_step(request: Request, db: Session = Depends(get_db)):
    if AUTH_MODE != "password_sso" or not _pending(request, db):
        return RedirectResponse("/admin/login", 303)
    return render(request, "admin/sso.html", providers=PROVIDERS)


@app.get("/admin/sso/{provider}")
async def sso_start(request: Request, provider: str, db: Session = Depends(get_db)):
    if AUTH_MODE == "password" or provider not in PROVIDERS:
        raise HTTPException(404)
    if AUTH_MODE == "password_sso" and not _pending(request, db):
        return RedirectResponse("/admin/login", 303)
    if auth.banned_until(db, auth.client_ip(request)):
        return _login_page(request, "Dit toestel is geblokkeerd. Probeer het later opnieuw.")
    return await oauth.create_client(provider).authorize_redirect(request, f"{PUBLIC_URL}/admin/sso/{provider}/callback")


@app.get("/admin/sso/{provider}/callback")
async def sso_callback(request: Request, provider: str, db: Session = Depends(get_db)):
    if AUTH_MODE == "password" or provider not in PROVIDERS:
        raise HTTPException(404)
    ip = auth.client_ip(request)
    if auth.banned_until(db, ip):
        return _login_page(request, "Dit toestel is geblokkeerd. Probeer het later opnieuw.")
    pending = _pending(request, db) if AUTH_MODE == "password_sso" else None
    if AUTH_MODE == "password_sso" and not pending:
        return RedirectResponse("/admin/login", 303)
    try:                                                # controleert state, PKCE, handtekening en nonce van het id-token
        token = await oauth.create_client(provider).authorize_access_token(request)
    except Exception as exc:
        crud.log_event("sso_error", provider=provider, ip=ip, error=type(exc).__name__)
        return _login_page(request, "Inloggen met Google of Microsoft is mislukt. Probeer het opnieuw.")
    claims = token.get("userinfo") or {}
    email = str(claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    if provider == "google" and claims.get("email_verified") is not True:
        email = ""
    name = admin_emails().get(email)
    user = db.query(User).filter_by(username=name).first() if name else None
    if not user or (pending and pending.id != user.id):
        auth.record_failure(db, ip)                     # telt mee voor dezelfde IP-ban als verkeerde wachtwoorden
        crud.log_event("sso_denied", provider=provider, ip=ip, email=email[:60])
        return _login_page(request, "Dit account heeft geen toegang tot het beheer.")
    return _finish(request, db, user, ip, provider, htmx=False)


@app.post("/admin/logout", dependencies=[Depends(auth.csrf_guard)])
def logout(request: Request):
    request.session.clear()
    return Response(headers={"HX-Redirect": "/admin/login"})


# ---------------------------------------------------------------- beheer: wachtwoord wijzigen
def _totp_pending(request, db):
    uid = request.session.get("totp_uid")
    fresh = time.time() - request.session.get("totp_ts", 0) < 600
    return db.get(User, uid) if uid and fresh else None


@app.get("/admin/2fa", response_class=HTMLResponse)
def twofa_page(request: Request, db: Session = Depends(get_db)):
    if not _totp_pending(request, db):
        return RedirectResponse("/admin/login", 303)
    return render(request, "admin/totp.html")


@app.post("/admin/2fa", dependencies=[Depends(auth.csrf_guard)])
def twofa_verify(request: Request, code: str = Form(""), db: Session = Depends(get_db)):
    user = _totp_pending(request, db)
    if not user:
        return Response(headers={"HX-Redirect": "/admin/login"})
    ip = auth.client_ip(request)
    until = auth.banned_until(db, ip)
    if until:
        return HTMLResponse(f'<span class="err">Te veel mislukte pogingen. Geblokkeerd tot {until:%d-%m-%Y %H:%M} (UTC).</span>')
    t = db.get(Totp, user.id)
    step = auth.verify_totp(t.secret, code[:20], t.last_step)
    if step is not None:
        t.last_step = step
    elif auth.use_recovery(t, code[:20]):
        crud.log_event("2fa_recovery_used", user=user.username, ip=ip, left=len([x for x in t.recovery.split(",") if x]))
    else:
        status, extra = auth.record_failure(db, ip)
        crud.log_event("2fa_fail", user=user.username, ip=ip)
        msg = (f"Te veel mislukte pogingen. Geblokkeerd tot {extra:%d-%m-%Y %H:%M} (UTC)." if status == "banned"
               else f"Code onjuist. Nog {extra} poging(en) voor een blokkade.")
        return HTMLResponse(f'<span class="err">{msg}</span>')
    db.commit()
    _start_session(request, db, user, ip, request.session.get("totp_via", "password") + "+2fa")
    return Response(headers={"HX-Redirect": "/admin"})


@app.get("/admin/password", response_class=HTMLResponse)
def password_page(request: Request, user=Depends(auth.current_user), db: Session = Depends(get_db)):
    return render(request, "admin/password.html", user=user, done=request.query_params.get("done") == "1",
                  must_setup=bool(request.session.get("must_2fa")), enabled=_has_totp(db, user), require2fa=REQUIRE_2FA,
                  use_totp=USE_TOTP, mode=AUTH_MODE)


@app.post("/admin/password", dependencies=[Depends(auth.csrf_guard)])
def password_change(request: Request, current: str = Form(""), new: str = Form(""), confirm: str = Form(""),
                    user=Depends(auth.current_user), db: Session = Depends(get_db)):
    def err(text):
        return HTMLResponse(f'<span class="err">{text}</span>')
    if new != confirm:
        return err("De twee nieuwe wachtwoorden zijn niet gelijk.")
    if not 12 <= len(new) <= 72:
        return err("Kies een wachtwoord van 12 tot 72 tekens.")
    if new == current:
        return err("Kies een ander wachtwoord dan het huidige.")
    ip = auth.client_ip(request)
    _, status, extra = auth.try_login(db, ip, user.username, current, clear=False)   # zelfde ban-regels als bij het inloggen
    if status == "banned":
        return err(f"Te veel mislukte pogingen. Geblokkeerd tot {extra:%d-%m-%Y %H:%M} (UTC).")
    if status != "ok":
        crud.log_event("password_change_fail", user=user.username, ip=ip)
        return err(f"Het huidige wachtwoord klopt niet. Nog {extra} poging(en) voor een blokkade.")
    user.password_hash = auth.hash_password(new)
    db.commit()
    request.session["ph"] = auth.session_mark(user)     # deze sessie blijft geldig, alle andere niet
    crud.log_event("password_changed", user=user.username, ip=ip)
    return Response(headers={"HX-Redirect": "/admin/password?done=1"})


# ---------------------------------------------------------------- beheer: tweestapsverificatie (authenticator-app)
def _card(request, db, user, **extra):
    return render(request, "admin/twofa_card.html", enabled=_has_totp(db, user), require2fa=REQUIRE_2FA, **extra)


def _setup_ctx(t, user):
    uri = auth.otpauth_uri(t.secret, user.username)
    qr = segno.make(uri, error="m").svg_data_uri(scale=5, border=2, dark="#000", light="#fff")   # wit vlak: scant ook in donkere modus
    return dict(qr=qr, key=" ".join(t.secret[i:i + 4] for i in range(0, len(t.secret), 4)))


def _pw_error(request, db, user, password):
    _, status, extra = auth.try_login(db, auth.client_ip(request), user.username, password[:200], clear=False)
    if status == "ok":
        return None
    if status == "banned":
        return f"Te veel mislukte pogingen. Geblokkeerd tot {extra:%d-%m-%Y %H:%M} (UTC)."
    return f"Wachtwoord klopt niet. Nog {extra} poging(en) voor een blokkade."


@app.post("/admin/security/2fa/start", dependencies=[Depends(auth.csrf_guard)])
def twofa_start(request: Request, user=Depends(auth.current_user), db: Session = Depends(get_db)):
    t = db.get(Totp, user.id)
    if t and t.enabled:
        return _card(request, db, user)
    if not t:
        t = Totp(user_id=user.id, secret="", enabled=False)
        db.add(t)
    t.secret, t.enabled, t.last_step = auth.new_totp_secret(), False, 0
    db.commit()
    return _card(request, db, user, setup=_setup_ctx(t, user))


@app.post("/admin/security/2fa/confirm", dependencies=[Depends(auth.csrf_guard)])
def twofa_confirm(request: Request, code: str = Form(""), user=Depends(auth.current_user), db: Session = Depends(get_db)):
    t = db.get(Totp, user.id)
    if not t or t.enabled:
        return _card(request, db, user)
    step = auth.verify_totp(t.secret, code[:20], 0)
    if step is None:
        return _card(request, db, user, setup=_setup_ctx(t, user), error="Die code klopt niet. Controleer de tijd op je telefoon en probeer opnieuw.")
    codes = auth.new_recovery_codes()
    t.enabled, t.last_step, t.recovery = True, step, ",".join(auth.hash_recovery(c) for c in codes)
    db.commit()
    request.session.pop("must_2fa", None)
    crud.log_event("2fa_enabled", user=user.username, ip=auth.client_ip(request))
    return _card(request, db, user, codes=codes)


@app.post("/admin/security/2fa/recovery", dependencies=[Depends(auth.csrf_guard)])
def twofa_recovery(request: Request, password: str = Form(""), user=Depends(auth.current_user), db: Session = Depends(get_db)):
    t = db.get(Totp, user.id)
    if not t or not t.enabled:
        return _card(request, db, user)
    err = _pw_error(request, db, user, password)
    if err:
        return _card(request, db, user, error=err)
    codes = auth.new_recovery_codes()
    t.recovery = ",".join(auth.hash_recovery(c) for c in codes)
    db.commit()
    crud.log_event("2fa_recovery_renewed", user=user.username)
    return _card(request, db, user, codes=codes)


@app.post("/admin/security/2fa/disable", dependencies=[Depends(auth.csrf_guard)])
def twofa_disable(request: Request, password: str = Form(""), user=Depends(auth.current_user), db: Session = Depends(get_db)):
    if REQUIRE_2FA:
        return _card(request, db, user, error="Tweestapsverificatie is verplicht en kan niet uitgezet worden.")
    err = _pw_error(request, db, user, password)
    if err:
        return _card(request, db, user, error=err)
    t = db.get(Totp, user.id)
    if t:
        db.delete(t)
        db.commit()
    crud.log_event("2fa_disabled", user=user.username)
    return _card(request, db, user)


# ---------------------------------------------------------------- beheer: secties
def _list(request, db, msg=None):
    return render(request, "admin/sections_list.html", groups=crud.overview(db, PAGE_LABELS), msg=msg, oob=True)


def _bad(text):
    return HTMLResponse(f'<span class="err">{text}</span>',
                        headers={"HX-Retarget": "#form-msg", "HX-Reswap": "innerHTML"})


def _clean(db, sid, page, lang, title, content, position):
    """Geeft (data, foutmelding). Per pagina+taal moet het volgnummer uniek zijn: dat koppelt de vertalingen."""
    title = title.strip()[:200]
    if page not in PAGE_LABELS or lang not in (*LANGS, "all") or not title:
        return None, "Vul een titel in en kies een geldige pagina en taal."
    position = max(0, min(position, 999))
    if page in ("nav", "footerlinks"):
        content = normalize_link(content)
        if not content:
            return None, "Bij Tekst moet een adres staan, bv. /blog of https://www.voorbeeld.be"
    same = db.query(Section).filter(Section.page == page, Section.lang == lang)
    if same.filter(Section.position == position, Section.id != (sid or 0)).first():
        return None, f"Volgorde {position} is al in gebruik voor deze pagina en taal. Kies bv. {max(x.position for x in same) + 1}."
    return dict(page=page, lang=lang, title=title, content=content.strip()[:10000], position=position), None


def _form(request, v, edit_id=None):
    return render(request, "admin/section_form.html", v=v, edit_id=edit_id)


def _values(s):
    return dict(title=s.title, content=s.content, page=s.page, lang=s.lang, position=s.position)


@app.get("/admin", response_class=HTMLResponse)
def dashboard(request: Request, user=Depends(auth.current_user), db: Session = Depends(get_db)):
    return render(request, "admin/dashboard.html", user=user, groups=crud.overview(db, PAGE_LABELS), msg=None)


@app.get("/admin/sections/new", response_class=HTMLResponse)
def new_form(request: Request, user=Depends(auth.current_user)):
    return _form(request, dict(title="", content="", page="home", lang="nl", position=0))


@app.get("/admin/sections/translate", response_class=HTMLResponse)
def translate_form(request: Request, page: str, position: int, lang: str,
                   user=Depends(auth.current_user), db: Session = Depends(get_db)):
    rows = db.query(Section).filter(Section.page == page, Section.position == position).all()
    if not rows or lang not in LANGS:
        raise HTTPException(404)
    have = next((s for s in rows if s.lang == lang), None)
    if have:
        return _form(request, _values(have), have.id)
    src = next((s for s in rows if s.lang == "nl"), rows[0])
    v = dict(_values(src), lang=lang,
             note=f"Dit is de tekst uit het {src.lang.upper()} als startpunt: vertaal hem naar {lang.upper()} en klik op Opslaan.")
    return _form(request, v)


@app.get("/admin/sections/{sid}/edit", response_class=HTMLResponse)
def edit_form(request: Request, sid: int, user=Depends(auth.current_user), db: Session = Depends(get_db)):
    s = db.get(Section, sid)
    if not s:
        raise HTTPException(404)
    return _form(request, _values(s), s.id)


@app.get("/admin/cancel", response_class=HTMLResponse)
def cancel(user=Depends(auth.current_user)):
    return HTMLResponse("")


@app.post("/admin/sections", dependencies=[Depends(auth.csrf_guard)])
def create_section(request: Request, page: str = Form(...), lang: str = Form(...), title: str = Form(...),
                   content: str = Form(""), position: int = Form(0),
                   user=Depends(auth.current_user), db: Session = Depends(get_db)):
    data, err = _clean(db, None, page, lang, title, content, position)
    if err:
        return _bad(err)
    s = crud.save_section(db, None, **data)
    crud.log_event("section_created", user=user.username, id=s.id, title=s.title)
    return _list(request, db, "Opgeslagen. De wijziging staat al op de website.")


@app.post("/admin/sections/{sid}", dependencies=[Depends(auth.csrf_guard)])
def update_section(request: Request, sid: int, page: str = Form(...), lang: str = Form(...),
                   title: str = Form(...), content: str = Form(""), position: int = Form(0),
                   user=Depends(auth.current_user), db: Session = Depends(get_db)):
    s = db.get(Section, sid)
    if not s:
        raise HTTPException(404)
    data, err = _clean(db, sid, page, lang, title, content, position)
    if err:
        return _bad(err)
    crud.save_section(db, s, **data)
    crud.log_event("section_updated", user=user.username, id=sid, title=s.title)
    return _list(request, db, "Opgeslagen. De wijziging staat al op de website.")


@app.delete("/admin/sections/{sid}", dependencies=[Depends(auth.csrf_guard)])
def delete_section(request: Request, sid: int, user=Depends(auth.current_user), db: Session = Depends(get_db)):
    s = db.get(Section, sid)
    if s:
        crud.log_event("section_deleted", user=user.username, id=sid, title=s.title)
        crud.delete_section(db, s)
    return _list(request, db, "Verwijderd.")


# ---------------------------------------------------------------- beheer: media (foto's, video's, pdf)
def sniff(head: bytes):
    """Bestandstype uit de eerste bytes (niet uit de naam). SVG en andere types zijn bewust niet toegestaan."""
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg", "image"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image"
    if head.startswith(b"GIF8"):
        return "gif", "image"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp", "image"
    if head[4:8] == b"ftyp":
        return "mp4", "video"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm", "video"
    if head.startswith(b"%PDF-"):
        return "pdf", "file"
    return None


LIMITS_MB = {"image": 10, "video": 150, "file": 20}


async def _store(file):
    """Slaat een upload veilig op. Geeft (info, None) of (None, foutmelding)."""
    head = await file.read(16)
    kind = sniff(head)
    if not kind:
        return None, "Dit bestandstype mag niet. Toegestaan: jpg, png, gif, webp, mp4, webm en pdf."
    ext, typ = kind
    limit = LIMITS_MB[typ] << 20
    name = f"{secrets.token_hex(8)}.{ext}"
    path = UPLOADS / name
    size, too_big = len(head), False
    with open(path, "wb") as f:
        f.write(head)
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > limit:
                too_big = True
                break
            f.write(chunk)
    if too_big:
        path.unlink(missing_ok=True)
        return None, f"Het bestand is te groot (maximaal {LIMITS_MB[typ]} MB voor dit type)."
    return dict(name=name, kind=typ, size=size), None


def media_tag(m):
    """Het tekstje dat je in een tekst of blogbericht zet om dit bestand te tonen."""
    if m.kind == "file":
        label = re.sub(r"[\[\]|]", "", m.caption or "").strip()[:80]
        return f"[bestand: {m.name}" + (f" {label}" if label else "") + "]"
    return f"[{'video' if m.kind == 'video' else 'foto'}: {m.name}]"


def media_items():
    db = SessionLocal()
    try:
        return db.query(Media).order_by(Media.id.desc()).limit(30).all()
    finally:
        db.close()


templates.env.globals.update(media_tag=media_tag, media_items=media_items)
PICK_FIELDS = ("f-content", "p-content")


def _textarea(field, content, msg, ok=True):
    """Vervangt het tekstveld door een versie met het mediatekstje erin (puur HTMX, geen JavaScript)."""
    field = field if field in PICK_FIELDS else PICK_FIELDS[0]
    cls = "ok" if ok else "err"
    return HTMLResponse(f'<textarea id="{field}" name="content" rows="12">{escape(content)}</textarea>'
                        f'<div id="pick-msg" hx-swap-oob="true" role="status" class="{cls}">{escape(msg)}</div>')


def _append(content, tag):
    return (content.rstrip() + "\n\n" if content.strip() else "") + tag


def _media(request, db, msg=None, ok=True):
    return render(request, "admin/media_list.html", items=db.query(Media).order_by(Media.id.desc()).all(), msg=msg, ok=ok)


@app.get("/admin/media", response_class=HTMLResponse)
def media_page(request: Request, user=Depends(auth.current_user), db: Session = Depends(get_db)):
    return render(request, "admin/media.html", user=user, items=db.query(Media).order_by(Media.id.desc()).all(),
                  msg=None, ok=True)


@app.post("/admin/media", dependencies=[Depends(auth.csrf_guard)])
async def media_upload(request: Request, file: UploadFile = File(...), caption: str = Form(""), in_hero: str = Form(""),
                       user=Depends(auth.current_user), db: Session = Depends(get_db)):
    info, err = await _store(file)
    if err:
        return _media(request, db, err, False)
    db.add(Media(name=info["name"], kind=info["kind"], caption=caption.strip()[:200],
                 in_hero=bool(in_hero) and info["kind"] != "file"))
    db.commit()
    crud.log_event("media_uploaded", user=user.username, **info)
    return _media(request, db, "Geüpload.")


@app.post("/admin/media/upload-insert", dependencies=[Depends(auth.csrf_guard)])
async def media_upload_insert(request: Request, user=Depends(auth.current_user), db: Session = Depends(get_db)):
    """Upload vanuit het tekst-/blogformulier: bewaart het bestand en zet het tekstje meteen onderaan de tekst."""
    form = await request.form()
    content, field, caption = str(form.get("content", "")), str(form.get("field", "")), str(form.get("caption", ""))
    file = form.get("file")
    if not hasattr(file, "read") or not getattr(file, "filename", ""):
        return _textarea(field, content, "Kies eerst een bestand.", False)
    info, err = await _store(file)
    if err:
        return _textarea(field, content, err, False)
    m = Media(name=info["name"], kind=info["kind"], caption=caption.strip()[:200], in_hero=False)
    db.add(m)
    db.commit()
    crud.log_event("media_uploaded", user=user.username, **info)
    return _textarea(field, _append(content, media_tag(m)), "Geüpload en ingevoegd onderaan de tekst.")


@app.post("/admin/media/youtube-insert", dependencies=[Depends(auth.csrf_guard)])
def youtube_insert(url: str = Form(""), content: str = Form(""), field: str = Form(""), user=Depends(auth.current_user)):
    vid = youtube_id(url[:300])
    if not vid:
        return _textarea(field, content, "Dit is geen geldige YouTube-link. Plak het adres uit de adresbalk of via Delen.", False)
    return _textarea(field, _append(content, f"[youtube: {vid}]"), "YouTube-video ingevoegd onderaan de tekst.")


@app.post("/admin/media/{mid}/insert", dependencies=[Depends(auth.csrf_guard)])
def media_insert(mid: int, content: str = Form(""), field: str = Form(""),
                 user=Depends(auth.current_user), db: Session = Depends(get_db)):
    m = db.get(Media, mid)
    if not m:
        raise HTTPException(404)
    return _textarea(field, _append(content, media_tag(m)), "Ingevoegd onderaan de tekst.")


@app.post("/admin/media/{mid}/hero", dependencies=[Depends(auth.csrf_guard)])
def media_hero(request: Request, mid: int, user=Depends(auth.current_user), db: Session = Depends(get_db)):
    m = db.get(Media, mid)
    if m:
        m.in_hero = not m.in_hero
        db.commit()
    return _media(request, db, "Opgeslagen.")


@app.delete("/admin/media/{mid}", dependencies=[Depends(auth.csrf_guard)])
def media_delete(request: Request, mid: int, user=Depends(auth.current_user), db: Session = Depends(get_db)):
    m = db.get(Media, mid)
    if m:
        (UPLOADS / m.name).unlink(missing_ok=True)
        crud.log_event("media_deleted", user=user.username, name=m.name)
        db.delete(m)
        db.commit()
    return _media(request, db, "Verwijderd.")


# ---------------------------------------------------------------- beheer: blog
def _posts(request, db, msg=None):
    return render(request, "admin/blog_list.html", posts=db.query(Post).order_by(Post.created.desc()).all(),
                  msg=msg, oob=True)


def _post_clean(title, lang, content):
    title = title.strip()[:200]
    if not title or lang not in (*LANGS, "all"):
        return None
    return dict(title=title, lang=lang, content=content.strip()[:20000])


@app.get("/admin/blog", response_class=HTMLResponse)
def blog_admin(request: Request, user=Depends(auth.current_user), db: Session = Depends(get_db)):
    return render(request, "admin/blog.html", user=user, posts=db.query(Post).order_by(Post.created.desc()).all(),
                  msg=None, oob=False)


@app.get("/admin/blog/new", response_class=HTMLResponse)
def post_new(request: Request, user=Depends(auth.current_user)):
    return render(request, "admin/post_form.html", p=None)


@app.get("/admin/blog/{pid}/edit", response_class=HTMLResponse)
def post_edit(request: Request, pid: int, user=Depends(auth.current_user), db: Session = Depends(get_db)):
    p = db.get(Post, pid)
    if not p:
        raise HTTPException(404)
    return render(request, "admin/post_form.html", p=p)


@app.post("/admin/blog", dependencies=[Depends(auth.csrf_guard)])
def post_create(request: Request, title: str = Form(...), lang: str = Form("nl"), content: str = Form(""),
                user=Depends(auth.current_user), db: Session = Depends(get_db)):
    data = _post_clean(title, lang, content)
    if not data:
        return _bad("Vul een titel in en kies een geldige taal.")
    p = Post(**data)
    db.add(p)
    db.commit()
    crud.log_event("post_created", user=user.username, id=p.id, title=p.title)
    return _posts(request, db, "Gepubliceerd. Het bericht staat op de website.")


@app.post("/admin/blog/{pid}", dependencies=[Depends(auth.csrf_guard)])
def post_update(request: Request, pid: int, title: str = Form(...), lang: str = Form("nl"), content: str = Form(""),
                user=Depends(auth.current_user), db: Session = Depends(get_db)):
    p = db.get(Post, pid)
    data = _post_clean(title, lang, content)
    if not p:
        raise HTTPException(404)
    if not data:
        return _bad("Vul een titel in en kies een geldige taal.")
    for k, v in data.items():
        setattr(p, k, v)
    db.commit()
    crud.log_event("post_updated", user=user.username, id=pid, title=p.title)
    return _posts(request, db, "Opgeslagen.")


@app.delete("/admin/blog/{pid}", dependencies=[Depends(auth.csrf_guard)])
def post_delete(request: Request, pid: int, user=Depends(auth.current_user), db: Session = Depends(get_db)):
    p = db.get(Post, pid)
    if p:
        crud.log_event("post_deleted", user=user.username, id=pid, title=p.title)
        db.delete(p)
        db.commit()
    return _posts(request, db, "Verwijderd.")


# ---------------------------------------------------------------- beheer: vakanties en uitzonderingen
KINDS = {"closed": "Geen training", "reduced": "Minder lessen (een deel gaat door)"}


def _lesson_names(db):
    return sorted({l["name"] for l in lessons_for(db, "nl")})


def _closures(request, db, msg=None):
    return render(request, "admin/closures_list.html", items=db.query(Closure).order_by(Closure.date_from.desc()).all(),
                  msg=msg, oob=True, kinds=KINDS)


def _closure_clean(title, date_from, date_to, kind, note, classes):
    try:
        a, b = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except ValueError:
        return None, "Kies een geldige begin- en einddatum."
    title = title.strip()[:120]
    if not title:
        return None, "Vul een naam in, bv. Herfstvakantie."
    if b < a:
        return None, "De einddatum mag niet voor de begindatum liggen."
    if kind not in KINDS:
        return None, "Kies een type."
    return dict(title=title, date_from=a, date_to=b, kind=kind, note=note.strip()[:300], classes=classes.strip()[:200]), None


@app.get("/admin/closures", response_class=HTMLResponse)
def closures_page(request: Request, user=Depends(auth.current_user), db: Session = Depends(get_db)):
    return render(request, "admin/closures.html", user=user, items=db.query(Closure).order_by(Closure.date_from.desc()).all(),
                  msg=None, oob=False, kinds=KINDS)


@app.get("/admin/closures/new", response_class=HTMLResponse)
def closure_new(request: Request, user=Depends(auth.current_user), db: Session = Depends(get_db)):
    return render(request, "admin/closure_form.html", c=None, names=_lesson_names(db))


@app.get("/admin/closures/{cid}/edit", response_class=HTMLResponse)
def closure_edit(request: Request, cid: int, user=Depends(auth.current_user), db: Session = Depends(get_db)):
    c = db.get(Closure, cid)
    if not c:
        raise HTTPException(404)
    return render(request, "admin/closure_form.html", c=c, names=_lesson_names(db))


@app.post("/admin/closures", dependencies=[Depends(auth.csrf_guard)])
def closure_create(request: Request, title: str = Form(""), date_from: str = Form(""), date_to: str = Form(""),
                   kind: str = Form("closed"), note: str = Form(""), classes: list[str] = Form([]),
                   user=Depends(auth.current_user), db: Session = Depends(get_db)):
    data, err = _closure_clean(title, date_from, date_to, kind, note, ", ".join(classes))
    if err:
        return _bad(err)
    c = Closure(**data)
    db.add(c)
    db.commit()
    crud.log_event("closure_created", user=user.username, id=c.id, title=c.title)
    return _closures(request, db, "Opgeslagen. Het staat meteen in de kalender op de website.")


@app.post("/admin/closures/{cid}", dependencies=[Depends(auth.csrf_guard)])
def closure_update(request: Request, cid: int, title: str = Form(""), date_from: str = Form(""), date_to: str = Form(""),
                   kind: str = Form("closed"), note: str = Form(""), classes: list[str] = Form([]),
                   user=Depends(auth.current_user), db: Session = Depends(get_db)):
    c = db.get(Closure, cid)
    if not c:
        raise HTTPException(404)
    data, err = _closure_clean(title, date_from, date_to, kind, note, ", ".join(classes))
    if err:
        return _bad(err)
    for k, v in data.items():
        setattr(c, k, v)
    db.commit()
    crud.log_event("closure_updated", user=user.username, id=cid, title=c.title)
    return _closures(request, db, "Opgeslagen.")


@app.delete("/admin/closures/{cid}", dependencies=[Depends(auth.csrf_guard)])
def closure_delete(request: Request, cid: int, user=Depends(auth.current_user), db: Session = Depends(get_db)):
    c = db.get(Closure, cid)
    if c:
        crud.log_event("closure_deleted", user=user.username, id=cid, title=c.title)
        db.delete(c)
        db.commit()
    return _closures(request, db, "Verwijderd.")


# ---------------------------------------------------------------- beheer: boeken
def _covers(db):
    return db.query(Media).filter(Media.kind == "image").order_by(Media.id.desc()).all()


def _cover_select(db, selected="", msg="", ok=True):
    opts = '<option value="">(geen cover)</option>' + "".join(
        f'<option value="{m.name}"{" selected" if m.name == selected else ""}>{escape(m.caption or m.name)}</option>' for m in _covers(db))
    return HTMLResponse(f'<select id="b-cover" name="cover">{opts}</select>'
                        f'<div id="cover-msg" hx-swap-oob="true" role="status" class="{"ok" if ok else "err"}">{escape(msg)}</div>')


def _books_admin(request, db, msg=None):
    return render(request, "admin/books_list.html", books=db.query(Book).order_by(Book.position, Book.id).all(), msg=msg, oob=True)


def _book_clean(db, title, subtitle, author, description, price, cover, link_eu, link_world, position):
    title = title.strip()[:200]
    if not title:
        return None, "Vul de titel van het boek in."
    links = []
    for raw in (link_eu, link_world):
        u = normalize_link(raw) if raw.strip() else ""
        if raw.strip() and not (u and u.lower().startswith(("http://", "https://"))):
            return None, "Een bestel-link moet een volledig adres zijn, bv. https://www.amazon.com/dp/..."
        links.append(u[:500])
    if cover and not db.query(Media).filter(Media.name == cover, Media.kind == "image").first():
        return None, "Kies een cover uit de lijst of upload een foto."
    return dict(title=title, subtitle=subtitle.strip()[:200], author=author.strip()[:120], description=description.strip()[:3000],
                price=price.strip()[:60], cover=cover, link_eu=links[0], link_world=links[1], position=max(0, min(position, 99))), None


@app.get("/admin/books", response_class=HTMLResponse)
def books_admin(request: Request, user=Depends(auth.current_user), db: Session = Depends(get_db)):
    return render(request, "admin/books.html", user=user, books=db.query(Book).order_by(Book.position, Book.id).all(), msg=None, oob=False)


@app.get("/admin/books/new", response_class=HTMLResponse)
def book_new(request: Request, user=Depends(auth.current_user), db: Session = Depends(get_db)):
    return render(request, "admin/book_form.html", b=None, covers=_covers(db))


@app.get("/admin/books/{bid}/edit", response_class=HTMLResponse)
def book_edit(request: Request, bid: int, user=Depends(auth.current_user), db: Session = Depends(get_db)):
    b = db.get(Book, bid)
    if not b:
        raise HTTPException(404)
    return render(request, "admin/book_form.html", b=b, covers=_covers(db))


@app.post("/admin/books/upload-cover", dependencies=[Depends(auth.csrf_guard)])
async def book_cover_upload(request: Request, user=Depends(auth.current_user), db: Session = Depends(get_db)):
    form = await request.form()
    selected, file = str(form.get("cover", "")), form.get("file")
    if not hasattr(file, "read") or not getattr(file, "filename", ""):
        return _cover_select(db, selected, "Kies eerst een foto.", False)
    info, err = await _store(file)
    if err:
        return _cover_select(db, selected, err, False)
    if info["kind"] != "image":
        (UPLOADS / info["name"]).unlink(missing_ok=True)
        return _cover_select(db, selected, "Een cover moet een foto zijn (jpg, png, gif of webp).", False)
    db.add(Media(name=info["name"], kind="image", caption=str(form.get("caption", "Boekcover"))[:200] or "Boekcover", in_hero=False))
    db.commit()
    crud.log_event("media_uploaded", user=user.username, **info)
    return _cover_select(db, info["name"], "Geüpload en gekozen. Klik nog op Opslaan.")


@app.post("/admin/books", dependencies=[Depends(auth.csrf_guard)])
def book_create(request: Request, title: str = Form(""), subtitle: str = Form(""), author: str = Form(""),
                description: str = Form(""), price: str = Form(""), cover: str = Form(""), link_eu: str = Form(""),
                link_world: str = Form(""), position: int = Form(0), user=Depends(auth.current_user), db: Session = Depends(get_db)):
    data, err = _book_clean(db, title, subtitle, author, description, price, cover, link_eu, link_world, position)
    if err:
        return _bad(err)
    b = Book(**data)
    db.add(b)
    db.commit()
    crud.log_event("book_created", user=user.username, id=b.id, title=b.title)
    return _books_admin(request, db, "Opgeslagen. Het boek staat op de website.")


@app.post("/admin/books/{bid}", dependencies=[Depends(auth.csrf_guard)])
def book_update(request: Request, bid: int, title: str = Form(""), subtitle: str = Form(""), author: str = Form(""),
                description: str = Form(""), price: str = Form(""), cover: str = Form(""), link_eu: str = Form(""),
                link_world: str = Form(""), position: int = Form(0), user=Depends(auth.current_user), db: Session = Depends(get_db)):
    b = db.get(Book, bid)
    if not b:
        raise HTTPException(404)
    data, err = _book_clean(db, title, subtitle, author, description, price, cover, link_eu, link_world, position)
    if err:
        return _bad(err)
    for k, v in data.items():
        setattr(b, k, v)
    db.commit()
    crud.log_event("book_updated", user=user.username, id=bid, title=b.title)
    return _books_admin(request, db, "Opgeslagen.")


@app.delete("/admin/books/{bid}", dependencies=[Depends(auth.csrf_guard)])
def book_delete(request: Request, bid: int, user=Depends(auth.current_user), db: Session = Depends(get_db)):
    b = db.get(Book, bid)
    if b:
        crud.log_event("book_deleted", user=user.username, id=bid, title=b.title)
        db.delete(b)
        db.commit()
    return _books_admin(request, db, "Verwijderd.")

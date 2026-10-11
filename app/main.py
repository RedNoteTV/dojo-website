"""SF Dojo – FastAPI-app: publieke site + beheerpaneel. Alle routes staan in dit bestand."""
import logging
import os
import re
import time
from html import escape
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from authlib.integrations.starlette_client import OAuth
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import auth, crud
from .database import Base, SessionLocal, engine, get_db
from .i18n import LANGS, NAMES, TEXT
from .models import Media, Post, Section, User

BASE = Path(__file__).parent
HTTPS = os.getenv("HTTPS_ONLY", "0") == "1"
SECRET = os.getenv("SECRET_KEY")
if not SECRET:
    raise RuntimeError("SECRET_KEY ontbreekt: kopieer .env.example naar .env en vul die in.")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                    handlers=[logging.StreamHandler(), logging.FileHandler("sfdojo-events.log")])
Base.metadata.create_all(engine)

# ---- inloggen: AUTH_MODE = password | password_sso (wachtwoord + Google/Microsoft) | sso (alleen Google/Microsoft)
AUTH_MODE = os.getenv("AUTH_MODE", "password")
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
UPLOADS = BASE.parent / "uploads"           # geüploade foto's en video's (back-up mee nemen!)
UPLOADS.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
app.mount("/media", StaticFiles(directory=UPLOADS), name="media")
templates = Jinja2Templates(directory=BASE / "templates")

PAGES = {"about": "page.html", "disciplines": "page.html", "schedule": "schedule.html",
         "prices": "prices.html", "contact": "contact.html", "payments": "payments.html"}
# Paginanamen zoals de beheerder ze ziet (home en footer hebben geen eigen route)
PAGE_LABELS = {"home": "Home", "about": "Over ons", "disciplines": "Disciplines", "schedule": "Lessenrooster",
               "prices": "Prijzen", "contact": "Contact", "payments": "Betalingen",
               "nav": "Menu: extra items", "footerlinks": "Footer: extra links",
               "footer": "Footer: social media"}
THEMES = ["", "light", "dark"]          # "" = automatisch (volgt het toestel)


MEDIA_RE = re.compile(r"^\[(foto|video|bestand):\s*([0-9a-f]{16}\.(?:jpg|png|gif|webp|mp4|webm|pdf))(?:\s+([^\[\]|]{1,80}))?\]$")


def blocks(text):
    """Tekst -> blokken. Regels met '|' = tabel; [foto: x] / [video: x] / [bestand: x] = media (opeenvolgend = galerij)."""
    out = []
    for para in (text or "").replace("\r", "").split("\n\n"):
        lines = [l for l in para.split("\n") if l.strip()]
        if not lines:
            continue
        ms = [MEDIA_RE.match(l.strip()) for l in lines]
        if all(ms):
            items = [m.groups() for m in ms]
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


def today_idx():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Brussels")).weekday()
    except Exception:
        return datetime.now().weekday()


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


def excerpt(text, n=180):
    for b in blocks(text):
        if "text" in b:
            t = b["text"].replace("\n", " ")
            return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"
    return ""


templates.env.globals.update(blocks=blocks, schedule=schedule, footer_links=footer_links, page_labels=PAGE_LABELS,
                             lang_names=NAMES, year=lambda: datetime.now().year, link_items=link_items, excerpt=excerpt)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
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


def _start_session(request, user, ip, via):
    request.session.clear()                             # nieuwe sessie na login
    request.session["uid"] = user.id
    request.session["ph"] = auth.session_mark(user)
    crud.log_event("login_ok", user=user.username, ip=ip, via=via)


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
    user, status, extra = auth.try_login(db, ip, username[:50], password[:200], clear=AUTH_MODE == "password")
    if status == "ok":
        if AUTH_MODE == "password_sso":                 # stap 2: bevestigen met Google/Microsoft
            request.session.clear()
            request.session["pending_uid"], request.session["pending_ts"] = user.id, int(time.time())
            crud.log_event("login_password_ok", user=user.username, ip=ip)
            return Response(headers={"HX-Redirect": "/admin/sso"})
        _start_session(request, user, ip, "password")
        return Response(headers={"HX-Redirect": "/admin"})
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
    auth.clear_failures(db, ip)
    _start_session(request, user, ip, provider)
    return RedirectResponse("/admin", 303)


@app.post("/admin/logout", dependencies=[Depends(auth.csrf_guard)])
def logout(request: Request):
    request.session.clear()
    return Response(headers={"HX-Redirect": "/admin/login"})


# ---------------------------------------------------------------- beheer: wachtwoord wijzigen
@app.get("/admin/password", response_class=HTMLResponse)
def password_page(request: Request, user=Depends(auth.current_user)):
    return render(request, "admin/password.html", user=user, done=request.query_params.get("done") == "1")


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
    _, status, extra = auth.try_login(db, ip, user.username, current)   # zelfde ban-regels als bij het inloggen
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

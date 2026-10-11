"""SF Dojo – FastAPI-app: publieke site + beheerpaneel. Alle routes staan in dit bestand."""
import logging
import os
import re
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import auth, crud
from .database import Base, SessionLocal, engine, get_db
from .i18n import LANGS, NAMES, TEXT
from .models import Media, Post, Section

BASE = Path(__file__).parent
HTTPS = os.getenv("HTTPS_ONLY", "0") == "1"
SECRET = os.getenv("SECRET_KEY")
if not SECRET:
    raise RuntimeError("SECRET_KEY ontbreekt: kopieer .env.example naar .env en vul die in.")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                    handlers=[logging.StreamHandler(), logging.FileHandler("sfdojo-events.log")])
Base.metadata.create_all(engine)

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


MEDIA_RE = re.compile(r"^\[(foto|video):\s*([0-9a-f]{16}\.(?:jpg|png|gif|webp|mp4|webm))\]$")


def blocks(text):
    """Tekst -> alinea's. Een alinea waarvan elke regel '|' bevat wordt een tabel."""
    out = []
    for para in (text or "").replace("\r", "").split("\n\n"):
        m = MEDIA_RE.match(para.strip())          # [foto: naam.jpg] / [video: naam.mp4]
        if m:
            out.append({"media": m.groups()})
            continue
        lines = [l for l in para.split("\n") if l.strip()]
        if not lines:
            continue
        if all("|" in l for l in lines):
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


def link_items(page, lang):
    """Extra menu-/footerlinks uit de database: titel = tekst, inhoud = adres (/pad of https://...)."""
    db = SessionLocal()
    try:
        out = []
        for s in crud.sections_for(db, page, lang):
            u = s.content.strip()
            if (u.startswith("/") and not u.startswith("//")) or u.lower().startswith(("https://", "http://")):
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
@app.get("/admin/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render(request, "admin/login.html")


@app.post("/admin/login", dependencies=[Depends(auth.csrf_guard)])
def login(request: Request, username: str = Form(""), password: str = Form(""),
          db: Session = Depends(get_db)):
    ip = auth.client_ip(request)
    user, status, extra = auth.try_login(db, ip, username[:50], password[:200])
    if status == "ok":
        request.session.clear()                         # nieuwe sessie na login
        request.session["uid"] = user.id
        request.session["ph"] = auth.session_mark(user)
        crud.log_event("login_ok", user=user.username, ip=ip)
        return Response(headers={"HX-Redirect": "/admin"})
    crud.log_event("login_fail" if status == "fail" else "login_blocked", ip=ip, user=username[:30])
    if status == "banned":
        msg = f"Te veel mislukte pogingen. Dit toestel is geblokkeerd tot {extra:%d-%m-%Y %H:%M} (UTC)."
    else:
        msg = f"Onjuiste gebruikersnaam of wachtwoord. Nog {extra} poging(en) voor een blokkade."
    return HTMLResponse(f'<span class="err">{msg}</span>')   # status 200 zodat HTMX het toont


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


# ---------------------------------------------------------------- beheer: media (foto's en video's)
def sniff(head: bytes):
    """Bestandstype uit de eerste bytes (niet uit de naam). SVG is bewust niet toegestaan."""
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
    return None


def _media(request, db, msg=None, ok=True):
    return render(request, "admin/media_list.html", items=db.query(Media).order_by(Media.id.desc()).all(), msg=msg, ok=ok)


@app.get("/admin/media", response_class=HTMLResponse)
def media_page(request: Request, user=Depends(auth.current_user), db: Session = Depends(get_db)):
    return render(request, "admin/media.html", user=user, items=db.query(Media).order_by(Media.id.desc()).all(),
                  msg=None, ok=True)


@app.post("/admin/media", dependencies=[Depends(auth.csrf_guard)])
async def media_upload(request: Request, file: UploadFile = File(...), caption: str = Form(""), in_hero: str = Form(""),
                       user=Depends(auth.current_user), db: Session = Depends(get_db)):
    head = await file.read(16)
    kind = sniff(head)
    if not kind:
        return _media(request, db, "Dit bestandstype mag niet. Kies een jpg, png, gif, webp, mp4 of webm.", False)
    ext, typ = kind
    limit = (10 if typ == "image" else 150) << 20
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
        return _media(request, db, f"Het bestand is te groot (maximaal {limit >> 20} MB).", False)
    db.add(Media(name=name, kind=typ, caption=caption.strip()[:200], in_hero=bool(in_hero)))
    db.commit()
    crud.log_event("media_uploaded", user=user.username, name=name, kind=typ, size=size)
    return _media(request, db, "Geüpload.")


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

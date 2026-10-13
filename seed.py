"""Vult de database met dummy-data en maakt de 3 beheerders aan.

  python seed.py                 -> eerste keer (bestaande data blijft staan)
  python seed.py --reset-users   -> nieuwe wachtwoorden voor de 3 beheerders
  python seed.py --reset-content -> WIST alle secties en slides en zet de dummy-inhoud terug
  python seed.py --init-env      -> maakt .env aan (of vult ontbrekende instellingen aan) met een verse SECRET_KEY
  python seed.py --reset-2fa NAAM -> zet tweestapsverificatie van één beheerder uit (telefoon kwijt en geen herstelcodes)
  python seed.py --reset-password NAAM -> nieuw tijdelijk wachtwoord voor één beheerder (moet het bij de eerste login wijzigen)
  python seed.py --unban         -> heft ALLE geblokkeerde IP's op (of: --unban 1.2.3.4 voor één IP)
Wachtwoorden worden willekeurig gemaakt en ÉÉN keer getoond: noteer ze meteen.
"""
import os
import re
import secrets
import shutil
import sys
from datetime import date
from pathlib import Path

from app.auth import hash_password
from app.database import Base, SessionLocal, engine
from app.models import Book, Closure, LoginBlock, Media, ResetToken, UserMeta, Post, Section, Slide, Totp, User

if "--init-env" in sys.argv:                   # .env klaarzetten zonder iets te overschrijven
    env, example = Path(".env"), Path(".env.example")
    cur = env.read_text() if env.exists() else ""
    have = {l.split("=", 1)[0].strip() for l in cur.splitlines() if "=" in l and not l.lstrip().startswith("#")}
    add = [l for l in example.read_text().splitlines()
           if "=" in l and not l.lstrip().startswith("#") and l.split("=", 1)[0].strip() not in have]
    key = secrets.token_hex(32)
    if re.search(r"^SECRET_KEY=(vervang-mij)?\s*$", cur, re.M):
        cur = re.sub(r"^SECRET_KEY=.*$", f"SECRET_KEY={key}", cur, flags=re.M)
    elif "SECRET_KEY" not in have:
        add = [f"SECRET_KEY={key}" if l.startswith("SECRET_KEY=") else l for l in add]
    env.write_text(cur.rstrip("\n") + ("\n" if cur else "") + ("\n# --- toegevoegd door seed.py --init-env ---\n" + "\n".join(add) + "\n" if add else ""))
    env.chmod(0o600)
    print(f".env staat in: {env.resolve()}  ({len(add)} instelling(en) toegevoegd; bestaande waarden zijn ongemoeid gelaten)")
    sys.exit()

Base.metadata.create_all(engine)
db = SessionLocal()

if "--reset-password" in sys.argv:          # herstel via de opdrachtregel (zonder e-mail)
    name = sys.argv[sys.argv.index("--reset-password") + 1].strip().lower()
    u = db.query(User).filter_by(username=name).first()
    if not u:
        print(f"Gebruiker '{name}' bestaat niet.")
        sys.exit(1)
    pw = secrets.token_urlsafe(12)
    u.password_hash = hash_password(pw)
    meta = db.get(UserMeta, u.id) or UserMeta(user_id=u.id, email="")
    meta.must_change = True
    db.add(meta)
    db.query(ResetToken).filter_by(user_id=u.id).update({"used": True})
    db.commit()
    print(f"Tijdelijk wachtwoord voor {name}: {pw}\n(alle bestaande sessies zijn ongeldig; bij de eerste login moet een eigen wachtwoord gekozen worden)")
    sys.exit()

if "--reset-2fa" in sys.argv:
    name = sys.argv[sys.argv.index("--reset-2fa") + 1].strip().lower()
    u = db.query(User).filter_by(username=name).first()
    n = db.query(Totp).filter_by(user_id=u.id).delete() if u else 0
    db.commit()
    print(f"Tweestapsverificatie van '{name}' uitgezet." if n else f"Geen tweestapsverificatie gevonden voor '{name}'.")
    sys.exit()

if "--unban" in sys.argv:                      # IP-ban opheffen (de accounts zelf worden nooit geblokkeerd)
    i = sys.argv.index("--unban")
    ip = sys.argv[i + 1] if len(sys.argv) > i + 1 and not sys.argv[i + 1].startswith("--") else None
    q = db.query(LoginBlock)
    n = (q.filter_by(ip=ip) if ip else q).delete()
    db.commit()
    print(f"{n} geblokkeerd(e) IP-adres(sen) vrijgegeven.")
    sys.exit()

# ---- beheerders: exact 3 (namen aanpasbaar via .env: ADMIN1_USER, ...)
names = [os.getenv("ADMIN1_USER", "dev"), os.getenv("ADMIN2_USER", "righthand"), os.getenv("ADMIN3_USER", "leader")]
reset = "--reset-users" in sys.argv
for name in names:
    name = name.strip().lower()
    user = db.query(User).filter_by(username=name).first()
    if user and not reset:
        continue
    pw = secrets.token_urlsafe(12)
    if user:
        user.password_hash = hash_password(pw)
    else:
        db.add(User(username=name, password_hash=hash_password(pw)))
    print(f"  gebruiker: {name:12} wachtwoord: {pw}")
db.commit()

# ---- dummy-inhoud (alleen als de tabel leeg is, of met --reset-content: wist ALLE secties en slides!)
if "--reset-content" in sys.argv:
    db.query(Section).delete()
    db.query(Slide).delete()
    db.query(Post).delete()
    db.commit()
if db.query(Section).count() == 0:
    S = lambda page, lang, pos, title, content: Section(page=page, lang=lang, position=pos, title=title, content=content)
    db.add_all([
        # --- home
        S("home", "nl", 0, "Welkom bij SF Dojo", "SF Dojo is een vechtsport- en zelfverdedigingsclub nabij Leuven. Bij ons train je Rishinjuku Karate, MMA, kickboksen en zelfverdediging, en er is ook G-Karate voor personen met een beperking.\n\nSelf Defense for Self Confidence: je leert jezelf beschermen en groeit in zelfvertrouwen."),
        S("home", "nl", 1, "Waarom SF Dojo?", "Voor iedereen: van beginner tot gevorderde, jong en oud.\n\nRespect en discipline: techniek en zelfbeheersing staan voorop.\n\nSterker en zelfverzekerder: je leert jezelf beschermen, in en buiten de dojo."),
        S("home", "nl", 2, "Eerste keer? Kom proberen", "Wil je kennismaken? Vraag een proefles aan via de contactpagina. Sportkledij en een flesje water volstaan."),
        S("home", "fr", 0, "Bienvenue au SF Dojo", "Le SF Dojo est un club d'arts martiaux et d'autodéfense près de Louvain. Karaté Rishinjuku, MMA, kickboxing, autodéfense et G-Karaté pour les personnes en situation de handicap.\n\nSelf Defense for Self Confidence."),
        S("home", "fr", 1, "Pourquoi le SF Dojo ?", "Pour tous : débutants et avancés, jeunes et moins jeunes.\n\nRespect et discipline : technique et maîtrise de soi avant tout.\n\nPlus fort et plus confiant, dans le dojo comme en dehors."),
        S("home", "en", 0, "Welcome to SF Dojo", "SF Dojo is a martial arts and self-defence club near Leuven. Train Rishinjuku Karate, MMA, kickboxing and self-defence; G-Karate is open to people with a disability.\n\nSelf Defense for Self Confidence."),
        S("home", "en", 1, "Why SF Dojo?", "For everyone: beginners to advanced, young and old.\n\nRespect and discipline: technique and self-control come first.\n\nStronger and more confident, in and outside the dojo."),
        S("home", "es", 0, "Bienvenido al SF Dojo", "SF Dojo es un club de artes marciales y defensa personal cerca de Lovaina. Karate Rishinjuku, MMA, kickboxing, defensa personal y G-Karate para personas con discapacidad.\n\nSelf Defense for Self Confidence."),
        S("home", "es", 1, "¿Por qué SF Dojo?", "Para todos: principiantes y avanzados, jóvenes y mayores.\n\nRespeto y disciplina: técnica y autocontrol ante todo.\n\nMás fuertes y más seguros, dentro y fuera del dojo."),
        S("home", "ja", 0, "SF道場へようこそ", "SF道場はルーヴェン近郊の武道・護身クラブです。理心塾空手、MMA、キックボクシング、護身術、そして障がいのある方のためのG-空手を指導しています。\n\nSelf Defense for Self Confidence"),
        S("home", "ja", 1, "SF道場の魅力", "どなたでも：初心者から経験者まで、子どもから大人まで。\n\n礼節と規律：技術と自制心を大切にします。\n\n道場の内外で、より強く、より自信を持てるように。"),
        # --- over ons
        S("about", "nl", 0, "Wie zijn we?", "SFDOJO vzw is een vechtsport- en zelfverdedigingsclub in Oud-Heverlee, vlak bij Leuven. Onder de naam Rishinjuku Karate (理心塾) bieden we karate, MMA-striking, grappling en zelfverdediging aan."),
        S("about", "nl", 1, "Onze visie", "Self Defense for Self Confidence. Zelfverdediging is meer dan technieken: het gaat om zelfvertrouwen, respect en veiligheid, in en buiten de dojo."),
        S("about", "nl", 2, "Instructeur Filip Swennen", "Filip Swennen is japanoloog en instructeur bij SF Dojo.\n\n(Vul hier zijn verhaal aan: ervaring, graden, stijl.)"),
        S("about", "nl", 3, "G-Karate: karate voor iedereen", "G-Karate is bedoeld voor personen met een fysieke beperking. De les wordt aangepast aan wat jij kan."),
        # --- disciplines
        S("disciplines", "nl", 0, "Rishinjuku Karate 理心塾", "Onze karatestijl, in groepen voor elk niveau: Karate 8+, Fundamentals (iedereen) en Volwassenen."),
        S("disciplines", "nl", 1, "MMA", "Karate technieken zijn zeer efficiënt in 'combat sports' zoals MMA (Mixed Martial Arts). Wij benaderen onze trainingen vanuit karate technieken voor het staande gedeelte. Op de grond werken we aan de submissions die statistisch het meest voorkomen."),
        S("disciplines", "nl", 2, "Grappling No Gi", "Grondwerk zonder judopak (No Gi): controle en technieken op de grond."),
        S("disciplines", "nl", 3, "G-Karate", "Karate voor personen met een fysieke beperking."),
        S("disciplines", "nl", 4, "Open Dojo", "Vrije trainingsavond, voor iedereen."),
        S("disciplines", "nl", 5, "Zelfverdediging", "Praktische technieken om jezelf te beschermen en zelfvertrouwen op te bouwen."),
        S("disciplines", "nl", 6, "Ghost Kickboksen", "Kickboksen zonder contact: techniek en conditie."),
        # --- lessenrooster (Dag | Uur | Les | Voor wie  ->  weekraster met kleuren)
        S("schedule", "nl", 0, "Wekelijkse lessen", "Dag | Uur | Les | Voor wie\nMaandag | 19:00-20:00 | Grappling No Gi* | Iedereen\nMaandag | 20:00-21:00 | Striking for MMA (kickboxing/karate) | Volwassenen\nDinsdag | 18:00-19:00 | Rishinjuku Karate 8+ |\nDinsdag | 19:00-20:00 | Rishinjuku Karate | Volwassenen\nWoensdag | 18:00-19:00 | Striking for MMA (kickboxing/karate) | Iedereen\nWoensdag | 19:00-20:00 | Grappling No Gi* | Iedereen\nDonderdag | 18:00-19:00 | G-Karate (fysieke beperking) |\nDonderdag | 19:00-20:00 | Rishinjuku Karate | Iedereen\nDonderdag | 20:00-21:00 | Open Dojo | Iedereen\nVrijdag | 18:00-19:00 | Rishinjuku Karate Fundamentals | Iedereen"),
        S("schedule", "nl", 1, "Goed om te weten", "* No Gi: grappling zonder judopak.\n\nWil je een les meevolgen? Neem contact op en we plannen een proefles."),
        # --- prijzen (dummy: vervang!)
        S("prices", "nl", 0, "Lidgeld", "Formule | Prijs | Opmerking\nProefles | Op aanvraag | Neem contact op\nVolwassenen | € 240 per jaar | Alle lessen\nJeugd | € 180 per jaar | Tot 16 jaar\nG-Karate | € 120 per jaar | Aangepaste lessen"),
        S("prices", "nl", 1, "Betalen", "Betalen kan met de app of kaartlezer van je eigen bank (KBC, ING, Belfius, BNP Paribas Fortis, ...). Alle info vind je op de pagina Betalingen."),
        # --- contact
        S("contact", "nl", 0, "Vragen of proefles?", "Stuur ons een mail of bel gerust. We antwoorden zo snel mogelijk."),
        # --- betalingen
        S("payments", "nl", 0, "Betalen", "Betaal via overschrijving naar het IBAN hieronder en vermeld de naam van het lid als mededeling."),
        # --- footer: social media (vervang door de echte links!)
        S("footer", "all", 0, "Social media", "Facebook | https://www.facebook.com/\nInstagram | https://www.instagram.com/\nYouTube | https://www.youtube.com/\nTikTok | https://www.tiktok.com/"),
    ])
    db.add_all([Slide(filename="slide1.svg", caption="Rishinjuku Karate 理心塾"),
                Slide(filename="slide2.svg", caption="Self Defense for Self Confidence"),
                Slide(filename="slide3.svg", caption="G-Karate")])
    db.commit()
    print("Dummy-inhoud aangemaakt.")
if db.query(Post).count() == 0:
    db.add(Post(lang="nl", title="Welkom op onze nieuwe website", content="Onze nieuwe website is online! Hier vind je het lessenrooster, informatie over onze disciplines en alle contactgegevens.\n\nIn de blog delen we nieuws over lessen, stages en activiteiten."))
    db.commit()
if db.query(Closure).count() == 0:             # voorbeelden volgens de Vlaamse schoolvakanties: controleer en pas aan in het beheer
    db.add_all([
        Closure(title="Herfstvakantie", date_from=date(2026, 11, 2), date_to=date(2026, 11, 8), kind="closed",
                note="Schoolvakantie: geen training. Vanaf maandag 9 november zijn we er weer."),
        Closure(title="Kerstvakantie", date_from=date(2026, 12, 21), date_to=date(2027, 1, 3), kind="reduced",
                classes="Open Dojo, Grappling No Gi*", note="Aangepast rooster tijdens de kerstvakantie: enkel deze lessen gaan door."),
        Closure(title="Krokusvakantie", date_from=date(2027, 2, 8), date_to=date(2027, 2, 14), kind="closed", note="Schoolvakantie: geen training."),
        Closure(title="Paasvakantie", date_from=date(2027, 3, 29), date_to=date(2027, 4, 11), kind="closed", note="Schoolvakantie: geen training."),
    ])
    db.commit()
# ---- extra's die ook bij een bestaande database worden aangevuld als ze nog ontbreken (FAQ, Skool-banner, boek)
if db.query(Section).filter_by(page="faq").count() == 0:
    for i, (q, a) in enumerate([
        ("Waar vinden de lessen plaats?", "Banhagestraat 75, 3052 Oud-Heverlee, vlak bij Leuven."),
        ("Kan ik eerst een proefles volgen?", "Ja, neem contact met ons op via de pagina Contact en we spreken een moment af."),
        ("Is er training tijdens de schoolvakanties?", "Dat verschilt per vakantie. In de kalender op de pagina Lessenrooster zie je precies wanneer er training is, wanneer niet en wanneer er minder lessen zijn."),
        ("Is er karate voor personen met een beperking?", "Ja, G-Karate is er voor personen met een fysieke beperking. Je vindt de les in het lessenrooster."),
        ("Hoe kan ik betalen?", "Via overschrijving (IBAN) of Bancontact in de KBC-app. Alle details staan op de pagina Betalingen."),
    ]):
        db.add(Section(page="faq", lang="nl", position=i, title=q, content=a))
    db.commit()
if db.query(Section).filter_by(page="promo").count() == 0:
    db.add(Section(page="promo", lang="nl", position=0, title="Verdiep je kennis: de online Rishinjuku Karate Course",
                   content="De volledige online Rishinjuku Karate Course staat op Skool.\n\nBekijk de course op Skool | https://www.skool.com/"))
    db.commit()
if db.query(Book).count() == 0:
    upload = Path(os.getenv("UPLOAD_DIR") or Path(__file__).parent / "uploads")
    upload.mkdir(parents=True, exist_ok=True)
    cover = f"{secrets.token_hex(8)}.png"
    shutil.copy(Path(__file__).parent / "app/static/images/book-cover.png", upload / cover)
    db.add(Media(name=cover, kind="image", caption="Weaponize your web (cover)", in_hero=False))
    db.add(Book(title="Weaponize your web", subtitle="Muchimi, karate's forgotten power source", author="Filip Swennen",
                price="€ 29,50 paperback", cover=cover,
                description="\"Weaponize your Web\" combines karate tradition with modern anatomy to explain the ability of the diagonal muscle slings to stretch, recoil and contract to develop power. This knowledge improves all kata, bunkai, self-defense and sports combat techniques.",
                link_eu="https://print.24bookprint.com/site/cart/addBook/sku/0005000485062", link_world="https://www.amazon.com/dp/B0BF35JFLM"))
    db.commit()
# ---- betaaltekst: alleen vervangen als je de oude voorbeeldtekst nooit aangepast hebt
for page, old, new in [
    ("prices", "Betalen kan via overschrijving of Bancontact in de KBC-app. Alle info vind je op de pagina Betalingen.",
     "Betalen kan met de app of kaartlezer van je eigen bank (KBC, ING, Belfius, BNP Paribas Fortis, ...). Alle info vind je op de pagina Betalingen."),
    ("payments", "Betaling via overschrijving (IBAN) of Bancontact in de KBC-app.\n\nOpen de KBC-app, kies Overschrijven, vul de IBAN hieronder en de naam van het lid als mededeling in, en bevestig.",
     "Betaal via overschrijving naar het IBAN hieronder en vermeld de naam van het lid als mededeling."),
]:
    sec = db.query(Section).filter_by(page=page, lang="nl", title="Betalen", content=old).first()
    if sec:
        sec.content = new
db.commit()
print("Klaar.")

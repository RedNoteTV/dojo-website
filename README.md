# SF Dojo – website (Rishinjuku Karate 理心塾)

FastAPI + Jinja2 + HTMX. Geen eigen JavaScript, geen CSS-framework. SQLite (`sfdojo.db`).
Alle teksten in dit project (tarieven, lesuren, beschrijvingen) zijn **dummy**: pas ze aan via het beheerpaneel.

## Installeren en starten (voor de developer)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # vul SECRET_KEY in
python seed.py                  # dummy-data + 3 beheerders (wachtwoorden worden één keer getoond!)
uvicorn app.main:app --reload   # http://127.0.0.1:8000  — beheer: /admin
```
Nieuwe wachtwoorden nodig? `python seed.py --reset-users`. Dummy-inhoud opnieuw laden (wist alle secties!): `python seed.py --reset-content`.

## Beheer voor niet-programmeurs
1. Ga naar `jouwsite.be/admin` en log in (telefoon of pc).
2. **Aanpassen:** druk bij een sectie op *Bewerken*, wijzig de tekst, druk op *Opslaan*. Het staat meteen op de website.
3. **Nieuw:** druk op *+ Nieuwe sectie*. Kies de pagina (home, disciplines, schedule, prices, contact, payments), de taal en de volgorde (laagste nummer staat bovenaan).
4. **Verwijderen:** druk op *Verwijderen* en bevestig.
5. **Lessenrooster:** sectie met de kopregel `Dag | Uur | Les | Voor wie`, daarna één les per regel (`Dinsdag | 18:00-19:00 | Rishinjuku Karate 8+ | Iedereen`). De site maakt hier zelf het gekleurde weekraster van (kleur volgt uit de naam van de les). **Prijzen** werken ook met `|` tussen de kolommen.
5b. **Social media in de footer:** pagina "Footer: social media", regels als `Instagram | https://...`.
5c. **Wachtwoord wijzigen:** knop bovenaan in het beheer. Na een wijziging moeten andere toestellen opnieuw inloggen.
6. Taal *all* = zichtbaar in elke taal. Bestaat er geen tekst in de gekozen taal, dan toont de site het Nederlands.
7. Klaar? Druk rechtsboven op *Uitloggen*.

## Beveiliging
- Er zijn exact 3 accounts, geen registratiepagina. Wachtwoorden zijn gehasht (bcrypt).
- **Login-ban per IP:** 3 fouten = 24 uur geblokkeerd. Daarna opnieuw 3 fouten = 48 uur, daarna 96 uur, 192 uur... (verdubbelt, max. 1 jaar). Een geband IP kan niet meer proberen, ook niet met het juiste wachtwoord. Een geslaagde login wist de teller van dat IP. Opgeheven ban handmatig: `sqlite3 sfdojo.db "DELETE FROM login_blocks WHERE ip='1.2.3.4'"`.
- Achter nginx/Caddy: zet `TRUST_PROXY=1`, anders zien alle bezoekers hetzelfde IP.
- Sessiecookie: httponly, samesite=lax, `secure` met `HTTPS_ONLY=1`. CSRF-token op alle beheeracties. Strikte CSP-headers.
- Tekst uit de database wordt altijd ge-escaped (geen HTML mogelijk).
- Wazuh en Kafka: zie `notes/wazuh_kafka.md`.

## Logo, foto's, taal en thema
- Logo: vervang `app/static/logo.png`. Hero-foto's: zet bestanden in `app/static/images/` en pas de tabel `slides` aan (bv. in `seed.py`, of via `sqlite3`).
- Talen: `?lang=nl|fr|en|es|ja` (wordt onthouden). Menu- en knopteksten staan in `app/i18n.py`.
- Thema: knop ◐ in de kop wisselt Auto → Licht → Donker.

## Online zetten
- Altijd HTTPS (nginx/Caddy + Let's Encrypt), `HTTPS_ONLY=1`, `TRUST_PROXY=1`, sterke `SECRET_KEY`.
- Draai met `uvicorn app.main:app --host 127.0.0.1 --port 8000` achter de reverse proxy (bv. via systemd).
- Back-up = bestand `sfdojo.db` kopiëren.

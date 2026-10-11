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

## Nieuw in deze versie
- **Vertalen via het beheer:** bij "Teksten" zie je per tekst de knoppen NL FR EN ES JA. Een knop met `+` = nog geen vertaling: klik, vertaal, sla op. Zonder vertaling toont de site per tekst het Nederlands.
- **Menu en footer uitbreiden:** pagina's "Menu: extra items" en "Footer: extra links" (Titel = tekst, Tekst = adres, bv. `/blog` of `https://...`). Social media staat onder "Footer: social media" (`TikTok | https://...`).
- **Blog:** menu "Blog" in het beheer. Berichten staan op `/blog`.
- **Foto's en video's:** menu "Foto's & video's". Uploads komen in de map `uploads/` (meenemen in de back-up!). Zet een bestand in een tekst of blogbericht met `[foto: naam.jpg]` of `[video: naam.mp4]`; het exacte tekstje staat bij elk bestand.
- **Geblokkeerd bij het inloggen?** De ban geldt per IP-adres, niet per account. Opheffen: `python seed.py --unban`.
- **Thema-knop:** zon = lichte modus, maan = donkere modus.

## Nieuw in v4
- **Foto's, video's en pdf's in teksten en blogberichten:** onderaan het formulier staat een vak "Foto, video of pdf toevoegen": kies een bestand dat al geüpload is en klik op *Invoegen*, of upload een nieuw bestand en voeg het meteen in. Meerdere foto's na elkaar worden een galerij. Een pdf wordt een downloadknop (`[bestand: naam.pdf Mijn naam]`).
- **Licht en donker:** in lichte modus zijn de kop en de hero licht met het zwarte logo en zwarte letters; in donkere modus donker met het lichte logo.
- **Menu: extra items / Footer: extra links:** bij Tekst volstaat `/blog`, `blog`, `www.voorbeeld.be`, `https://...`, `mailto:...` of `tel:...`. Een ongeldig adres wordt nu meteen geweigerd, en in het overzicht zie je de link.

## Inloggen met Google of Microsoft (extra beveiliging)
1. Zet in `.env`: `AUTH_MODE=password_sso` (wachtwoord én Google/Microsoft) of `sso` (alleen Google/Microsoft), `PUBLIC_URL`, de e-mailadressen `ADMIN1_EMAIL` enz. en de gegevens van minstens één aanbieder. Zonder die gegevens start de site bewust niet, zodat niemand zich buiten kan sluiten. Herstart daarna.
2. **Google:** Google Cloud Console -> nieuw project -> "Google Auth platform" / OAuth-toestemmingsscherm (extern, status Testen, voeg de 3 e-mailadressen toe als testgebruikers) -> Inloggegevens -> OAuth-client-ID, type Webtoepassing. Terugkeer-URI: `PUBLIC_URL/admin/sso/google/callback`.
3. **Microsoft:** Microsoft Entra -> App-registraties -> Nieuwe registratie -> Web, terugkeer-URI `PUBLIC_URL/admin/sso/microsoft/callback` -> Certificaten en geheimen -> Nieuw clientgeheim (de *waarde* kopiëren). `MICROSOFT_TENANT=consumers` voor Outlook/Hotmail-accounts, of de tenant-id voor een werk-/schoolaccount.
4. Alleen e-mailadressen uit `.env` komen binnen, en bij `password_sso` moet het adres bij de gebruiker passen die net het wachtwoord intikte. Een geweigerde poging telt mee voor de IP-ban.
5. Lokaal testen: `PUBLIC_URL=http://127.0.0.1:8000` en dat adres als terugkeer-URI bij Google/Microsoft.

## Op een Ubuntu-server (VPS) zetten
1. Maak bij je domeinnaam-provider een **A-record** (en eventueel `www`) naar het IP van de server.
2. Zet de map op de server (bv. `scp sfdojo-v4.zip jij@server:` en `unzip`), ga erin en draai:
   `sudo bash deploy/install.sh jouw@email.be jouwdomein.be www.jouwdomein.be`
   Dat installeert Python, nginx en certbot, zet de site als service (start vanzelf na een herstart), vraagt een gratis HTTPS-certificaat aan en maakt de beheerders aan (**noteer de wachtwoorden**).
3. **Extra domein later:** DNS instellen, daarna `sudo bash /opt/sfdojo/deploy/domain.sh nieuwdomein.be www.nieuwdomein.be`.
4. **Bijwerken:** nieuwe versie uitpakken en `install.sh` opnieuw draaien. `.env`, database en `uploads/` blijven staan.
5. **Back-up:** elke nacht automatisch naar `/var/backups/sfdojo` (14 dagen). Handmatig: `sudo bash /opt/sfdojo/deploy/backup.sh`.
6. Logs: `journalctl -u sfdojo -f` en `/opt/sfdojo/sfdojo-events.log` (voor Wazuh). Firewall: `SETUP_UFW=1 sudo -E bash deploy/install.sh ...` (opent alleen SSH en web).
7. Op een VPS met ook een andere SSH-poort: eerst die poort toestaan voor je ufw aanzet.

## Nieuw in v5
**Waar staat mijn `.env`?** In de hoofdmap van het project, naast `seed.py`. Het is een verborgen bestand (begint met een punt): `ls -A` toont het, in Zed/je bestandsbeheer zet je "verborgen bestanden tonen" aan. Openen: `nano .env` of `zeditor .env`. Klaarzetten of aanvullen met nieuwe instellingen: `python seed.py --init-env` (overschrijft nooit iets).

**Tweestapsverificatie met QR-code:** beheer -> Beveiliging -> *Instellen met QR-code*. Scan met Google Authenticator, Microsoft Authenticator, Authy of 2FAS. Daarna vraagt de site na het wachtwoord elke keer een code uit de app (elke code werkt één keer). Je krijgt 8 herstelcodes: bewaar ze. `REQUIRE_2FA=1` (standaard) dwingt dit af voor elke beheerder bij de volgende login. Telefoon en herstelcodes kwijt? `python seed.py --reset-2fa NAAM`.

**Google/Microsoft-login** staat uit tot je client-id, secret, `PUBLIC_URL` en de `ADMINx_EMAIL`'s invult (blok 4 in `.env`): dan verschijnt na het wachtwoord de stap "Bevestigen met Google/Microsoft". `AUTH_MODE=password` zet het altijd uit (noodknop als je jezelf buitensluit). Bij die modus vervangt het Google/Microsoft-account de authenticator-stap.

**Lessenrooster met vakanties:** beheer -> Vakanties. Geen training of minder lessen (vink aan welke lessen doorgaan). Bezoekers zien bovenaan of er vandaag training is, welke vakanties eraan komen en een maandkalender (klik op een dag voor de lessen). De voorbeelden volgen de Vlaamse schoolvakanties: controleer ze en pas aan.

**Container (Podman):** `sudo pacman -S podman podman-compose` (Ubuntu: `sudo apt install podman podman-compose`), dan in de projectmap `podman compose up -d --build`. De eerste start maakt zelf de sleutel, database en beheerders; de wachtwoorden staan in `podman logs sfdojo`. Open `http://127.0.0.1:8000` of `https://localhost:8443`. Alles wat bewaard moet worden staat in het volume `sfdojo-data` (back-up: `podman volume export sfdojo-data -o back-up.tar`).
**Op een server met domein:** zet in `.env`: `DOMAIN=jouwdomein.be`, `HTTPS_ONLY=1`, `PUBLIC_URL=https://jouwdomein.be`, `HTTP_PORT=80`, `HTTPS_PORT=443`, en geef rootless Podman toegang tot poort 80: `echo 'net.ipv4.ip_unprivileged_port_start=80' | sudo tee /etc/sysctl.d/80-podman.conf && sudo sysctl --system`. DNS: een A-record naar de server. Dan `podman compose up -d`. Caddy regelt het https-certificaat. Laat de containers na een herstart terugkomen: `loginctl enable-linger $USER` en `systemctl --user enable --now podman-restart.service`.

## Podman: aan- en uitzetten
Altijd in de projectmap (daar staat `compose.yaml`):
```fish
podman compose up -d --build   # aanzetten (en opnieuw bouwen na een nieuwe versie)
podman compose stop            # uitzetten (alles blijft bewaard)
podman compose start           # weer aanzetten, zonder opnieuw te bouwen
podman compose down            # containers weghalen; je gegevens blijven in het volume staan
podman compose logs -f web     # meekijken wat er gebeurt (Ctrl+C om te stoppen)
podman logs sfdojo | head -n 15   # de wachtwoorden van de eerste start
podman ps                      # draait het?
```
Gebruik nooit `podman compose down -v`: dat wist ook je database en uploads.

**Automatisch aan na het opstarten van je computer/server:** de containers hebben `restart: always`. Zet daarnaast één keer aan:
```fish
systemctl --user enable --now podman-restart.service
loginctl enable-linger $USER
```
Uitzetten van dat automatisch starten: `systemctl --user disable --now podman-restart.service`.

De container heeft zijn eigen database: de wachtwoorden en instellingen van je lokale `uvicorn`-versie gelden daar niet. Draai niet tegelijk uvicorn en de container: ze gebruiken allebei poort 8000.

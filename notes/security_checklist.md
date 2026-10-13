# Beveiligingschecklist: wat is gedaan en hoe het gecontroleerd is

Status: **getest** = door mijn testscripts met de echte code doorlopen; **klaar, niet gedraaid** = configuratie staat klaar maar is niet op een draaiende Wazuh/Kafka geprobeerd (dat kan alleen op jouw machine).

| Punt | Status | Hoe |
|---|---|---|
| IP-bans opgeslagen in de database (blijft na herstart) | getest | tabel `login_blocks` in SQLite; ban gelezen vanuit een apart proces |
| Ban-check vóór wachtwoord-check | getest | tijdens een ban wordt zelfs het juiste wachtwoord niet gecontroleerd |
| Teller reset bij succesvolle login | getest | 2 fouten + goed inloggen + 2 fouten = geen ban |
| Geen gebruikers-enumeratie | getest | zelfde melding voor onbekende gebruiker en fout wachtwoord; "wachtwoord vergeten" antwoordt altijd hetzelfde en stuurt de mail op de achtergrond |
| Snelheidslimiet op alle auth-eindpunten | getest | login, 2FA, vergeten, reset, SSO, wachtwoord wijzigen (per IP, per werkproces) naast de ban |
| Herstel gekozen | beide | CLI: `python seed.py --reset-password NAAM`; e-mail: link in de mail |
| Herstellink: gehasht, 15 min, eenmalig | getest | alleen HMAC-hash in de database, verlopen en gebruikte links worden geweigerd, een nieuwe aanvraag maakt de oude ongeldig, bij 2FA is ook een code nodig |
| Events `login_success`, `login_failed`, `ip_banned` | getest | zuivere JSON-regels in het logbestand; ook naar Kafka als `KAFKA_BROKER` gezet is |
| Kafka draait | klaar, niet gedraaid | `compose.kafka.yaml` (apache/kafka 3.9.0, KRaft) |
| Wazuh draait en leest de Caddy-logs | klaar, niet gedraaid | `monitoring/setup.sh` op basis van de officiële wazuh-docker v4.14.8; Caddy logt JSON naar een gedeeld volume |
| FastAPI-events bereiken Wazuh | deels | logregel-formaat en regels (XML) gecontroleerd; aankomst in Wazuh zie je pas na `setup.sh` |
| Test: 3x fout -> ban -> alarm in Wazuh | deels | app-kant getest; draai `monitoring/test-alert.sh` en kijk naar regels 100101, 100103, 100115 |
| Alles gedocumenteerd | ja | README, `notes/monitoring.md`, dit bestand |

Bekende beperkingen: de snelheidslimiet leeft in het geheugen van elk werkproces (de container draait er 2); de ban per IP is de echte rem en is gedeeld. Wazuh brengt zijn eigen standaardwachtwoorden mee: wijzig ze en houd alle poorten op 127.0.0.1.

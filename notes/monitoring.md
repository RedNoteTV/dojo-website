# Monitoring: events, Kafka en Wazuh

## Hoe het in elkaar zit
```
bezoeker -> Caddy --(access.log JSON)--------------------------+
              |                                                 v
              +-> FastAPI --(sfdojo-events.log, 1 JSON/regel)-> Wazuh manager -> indexer -> dashboard (127.0.0.1:5601)
                      |                                           regels: monitoring/sfdojo_rules.xml
                      +--(optioneel)--> Kafka, topic sfdojo-events
```
De website schrijft elke beveiligingsgebeurtenis als **één JSON-regel** (zonder tijdstempel-voorvoegsel, dat leest Wazuh het best) naar `LOG_FILE` en, als `KAFKA_BROKER` gezet is, ook naar Kafka. Wazuh leest dat bestand en de toegangslog van Caddy rechtstreeks uit twee gedeelde volumes (`sfdojo-logs`, `caddy-logs`), alleen-lezen. De database en uploads zijn dus niet zichtbaar voor Wazuh.

## Gebeurtenissen
| event | wanneer | Wazuh-niveau |
|---|---|---|
| `login_success` | goed ingelogd | 3 |
| `login_failed` | fout wachtwoord of gebruikersnaam | 5 |
| `ip_banned` | 3e fout: IP geblokkeerd (24u, 48u, 96u, ...) | 10 |
| `login_blocked` | poging vanaf een geblokkeerd IP | 8 (12 bij 5x in 5 min) |
| `2fa_fail`, `sso_denied`, `rate_limited` | foute 2FA-code, vreemd Google/Microsoft-account, te veel verzoeken | 6-7 |
| `password_changed`, `password_reset_requested`, `password_reset_done`, `temp_password_issued`, `2fa_disabled`, `2fa_recovery_used`, `user_updated` | account-wijzigingen | 4-9 |

Elke regel heeft `ts`, `event` en bij een IP ook `srcip` (die naam herkent Wazuh voor "zelfde bron-IP"). Extra regel 100115: 3x `login_failed` binnen 2 minuten vanaf hetzelfde IP = brute force (niveau 10).

## Wazuh starten (eenmalig)
1. Eenmalig als beheerder: `echo 'vm.max_map_count=262144' | sudo tee /etc/sysctl.d/99-wazuh.conf && sudo sysctl --system`
2. Zorg voor ~4 GB vrij geheugen en draai in de projectmap: `bash monitoring/setup.sh`
   Het script haalt de officiële `wazuh-docker` (v4.14.8, single-node) binnen, voegt onze regels en de twee logbestanden toe, zet **alle poorten alleen op 127.0.0.1** en start alles.
3. Open `https://localhost:5601` (certificaatwaarschuwing is normaal). Standaard: `admin` / `SecretPassword`. **Wijzig dat meteen** (Wazuh-documentatie, "Change the password of Wazuh users").
4. Test: `bash monitoring/test-alert.sh` (3x fout inloggen) en kijk in Wazuh naar de regels 100101, 100103 en 100115. Daarna de ban opheffen: `podman exec sfdojo python seed.py --unban`.

## Knoppen
- **Beheer -> Beveiliging -> "Open Wazuh"** (alleen voor de eigenaar), en **Monitoring** met de laatste 40 beveiligingsgebeurtenissen, ook zonder Wazuh.
- Het adres staat in `WAZUH_URL` (.env). Op een server bind je Wazuh bewust niet publiek: bereik het via een SSH-tunnel (`ssh -L 5601:127.0.0.1:5601 server`) en laat `WAZUH_URL=https://localhost:5601`.
- **Terug van Wazuh naar het beheer in 1 klik** kan niet zonder het Wazuh-scherm zelf aan te passen. Wazuh opent in een nieuw tabblad, dus het beheer-tabblad blijft staan; zet daarnaast een bladwijzer op `https://jouwdomein.be/admin`.

## Kafka (optioneel)
`podman compose -f compose.yaml -f compose.kafka.yaml up -d` voegt een Kafka-broker toe (KRaft, geen poorten naar buiten) en zet `KAFKA_BROKER=kafka:9092` voor de website. Meekijken: `podman exec -it sfdojo-kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic sfdojo-events --from-beginning`. Is Kafka onbereikbaar, dan probeert de site het pas na 1 minuut opnieuw en blijft het loggen naar het bestand: inloggen wordt er nooit trager van.

## Wat dit laat zien (portfolio)
Gelaagde beveiliging (wachtwoord + 2FA, escalerende IP-ban, snelheidslimieten, hash-only herstellinks), gestructureerde logging (JSON), een event-bus (Kafka), een SIEM (Wazuh) met eigen decoders/regels, en een gescheiden containeropzet met minimale rechten.

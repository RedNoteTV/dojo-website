# Wazuh en Kafka (kort)

## Wazuh-agent op de server
1. Installeer de agent volgens de Wazuh-documentatie en koppel hem aan jullie Wazuh-manager.
2. Voeg in `/var/ossec/etc/ossec.conf` toe:
```xml
<localfile><log_format>syslog</log_format><location>/pad/naar/sfdojo/sfdojo-events.log</location></localfile>
<syscheck><directories check_all="yes" realtime="yes">/pad/naar/sfdojo/app/templates,/pad/naar/sfdojo/app/static</directories></syscheck>
```
3. Bewaken: `/var/log/auth.log` (SSH), `sfdojo-events.log`, en bestandswijzigingen in templates/static (FIM).
4. Events in het logbestand (JSON): `login_ok`, `login_fail`, `login_blocked`, `section_created`, `section_updated`, `section_deleted`.
   Een Wazuh-regel op `login_blocked` of veel `login_fail` per IP geeft direct een alarm.

## Kafka (optioneel)
Zet `KAFKA_BROKER=host:9092` (en eventueel `KAFKA_TOPIC`) in `.env` en `pip install kafka-python`.
`crud.log_event()` stuurt dezelfde events dan ook naar het topic. Is Kafka onbereikbaar, dan wordt alleen gelogd (de site blijft werken).

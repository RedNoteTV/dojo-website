#!/usr/bin/env bash
# Wazuh (alarmsysteem) naast de website zetten. Eenmalig, daarna: zie notes/monitoring.md
#   bash monitoring/setup.sh            (Podman en git nodig; ~4 GB vrij geheugen)
# Zet Wazuh 4.14.x (officiele wazuh-docker, single-node) op 127.0.0.1 en laat het de logs van de website lezen.
set -euo pipefail
cd "$(dirname "$0")"
VER="${WAZUH_VERSION:-4.14.8}"
command -v podman >/dev/null && command -v git >/dev/null || { echo "Podman en git zijn nodig."; exit 1; }
if [ "$(sysctl -n vm.max_map_count)" -lt 262144 ]; then
  echo "Wazuh heeft een hogere limiet nodig. Doe eerst (eenmalig, als beheerder):"
  echo "  echo 'vm.max_map_count=262144' | sudo tee /etc/sysctl.d/99-wazuh.conf && sudo sysctl --system"
  exit 1
fi
# de logmappen van de website bestaan nu al, ook als de site nog niet gestart is
for v in sfdojo-logs caddy-logs; do podman volume exists "$v" || podman volume create "$v" >/dev/null; done
[ -d wazuh-docker ] || git clone -q --depth 1 --branch "v$VER" https://github.com/wazuh/wazuh-docker wazuh-docker
cd wazuh-docker/single-node
cp ../../sfdojo_rules.xml config/wazuh_cluster/sfdojo_rules.xml
python3 - <<'PY'
import re
p = "config/wazuh_cluster/wazuh_manager.conf"; t = open(p).read()
if "sfdojo-events.log" not in t:
    add = """
  <!-- SF Dojo -->
  <localfile>
    <log_format>json</log_format>
    <location>/var/log/sfdojo/sfdojo-events.log</location>
  </localfile>
  <localfile>
    <log_format>json</log_format>
    <location>/var/log/caddy/access.log</location>
  </localfile>
"""
    i = t.index("</ossec_config>")
    open(p, "w").write(t[:i] + add + t[i:])
PY
# alles alleen op deze computer bereikbaar (nooit publiek!); 443 is van Caddy
sed -i -e 's|- "1514:1514"|- "127.0.0.1:1514:1514"|' -e 's|- "1515:1515"|- "127.0.0.1:1515:1515"|' -e 's|- "514:514/udp"|- "127.0.0.1:514:514/udp"|' \
       -e 's|- "55000:55000"|- "127.0.0.1:55000:55000"|' -e 's|- "9200:9200"|- "127.0.0.1:9200:9200"|' -e 's|- 443:5601|- "127.0.0.1:5601:5601"|' docker-compose.yml
cat > docker-compose.override.yml <<'YML'
services:
  wazuh.manager:
    volumes:
      - sfdojo-logs:/var/log/sfdojo:ro
      - caddy-logs:/var/log/caddy:ro
      - ./config/wazuh_cluster/sfdojo_rules.xml:/wazuh-config-mount/etc/rules/sfdojo_rules.xml
volumes:
  sfdojo-logs:
    external: true
  caddy-logs:
    external: true
YML
podman compose -f generate-indexer-certs.yml run --rm generator
podman compose up -d
echo
echo "Wazuh start op (het duurt 1-2 minuten). Open https://localhost:5601 (certificaatwaarschuwing is normaal)."
echo "Standaard inloggen: admin / SecretPassword  -> WIJZIG DIT meteen (Wazuh-documentatie: 'Change the password of Wazuh users')."
echo "Test daarna met: bash monitoring/test-alert.sh"

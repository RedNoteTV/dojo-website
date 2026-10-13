#!/usr/bin/env bash
# Test: 3x fout inloggen -> IP-ban -> alarm in Wazuh.   bash monitoring/test-alert.sh [http://127.0.0.1:8000]
# LET OP: dit blokkeert het IP van deze computer 24 uur. Opheffen: podman exec sfdojo python seed.py --unban
set -euo pipefail
URL="${1:-http://127.0.0.1:8000}"; JAR="$(mktemp)"
TOKEN="$(curl -s -c "$JAR" "$URL/admin/login" | grep -o 'X-CSRF-Token": "[^"]*' | head -1 | sed 's/.*"//')"
[ -n "$TOKEN" ] || { echo "Geen CSRF-token gevonden: draait de site op $URL ?"; exit 1; }
for i in 1 2 3; do
  echo -n "Poging $i: "
  curl -s -b "$JAR" -c "$JAR" -H "X-CSRF-Token: $TOKEN" --data-urlencode "username=testaanval" --data-urlencode "password=fout-wachtwoord-$i" "$URL/admin/login" | sed 's/<[^>]*>//g'; echo
done
echo "Klaar. In Wazuh (Threat Hunting / Events, filter rule.groups: sfdojo) zie je binnen een minuut:"
echo "  100101 (mislukte login), 100103 (ip_banned) en 100115 (brute force)."
echo "Opheffen van de ban: podman exec sfdojo python seed.py --unban"

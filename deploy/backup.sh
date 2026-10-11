#!/usr/bin/env bash
# Back-up van database, uploads en .env naar /var/backups/sfdojo (14 dagen bewaard).
set -euo pipefail
APP=/opt/sfdojo; OUT=/var/backups/sfdojo; TMP="$(mktemp -d)"
mkdir -p "$OUT"; chmod 700 "$OUT"
"$APP/.venv/bin/python" - "$APP/sfdojo.db" "$TMP/sfdojo.db" <<'PY'
import sqlite3, sys
src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
src.backup(dst); dst.close(); src.close()
PY
cp "$APP/.env" "$TMP/env"; [ -d "$APP/uploads" ] && cp -r "$APP/uploads" "$TMP/uploads"
tar czf "$OUT/sfdojo-$(date +%F).tar.gz" -C "$TMP" .
rm -rf "$TMP"; find "$OUT" -name 'sfdojo-*.tar.gz' -mtime +14 -delete

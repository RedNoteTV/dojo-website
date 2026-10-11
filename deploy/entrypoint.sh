#!/bin/sh
# Eerste start: SECRET_KEY aanmaken (en bewaren), database + beheerders aanmaken. Wachtwoorden staan dan in de logs.
set -e
if [ -z "$SECRET_KEY" ]; then
  [ -f /data/secret_key ] || python -c "import secrets; print(secrets.token_hex(32))" > /data/secret_key
  SECRET_KEY="$(cat /data/secret_key)"; export SECRET_KEY
fi
mkdir -p /data/uploads
if [ ! -f /data/sfdojo.db ]; then
  echo "=== Eerste start: beheerders aanmaken. NOTEER DE WACHTWOORDEN HIERONDER ==="
  python seed.py
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2

#!/usr/bin/env bash
# SF Dojo installeren (of bijwerken) op Ubuntu 22.04/24.04.
#   sudo bash deploy/install.sh jouw@email.be jouwdomein.be [www.jouwdomein.be ...]
# Opnieuw draaien = bijwerken: .env, de database en uploads/ blijven staan.
# Optioneel firewall aanzetten: SETUP_UFW=1 sudo -E bash deploy/install.sh ...
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Draai dit met sudo."; exit 1; }
EMAIL="${1:?Gebruik: sudo bash deploy/install.sh jouw@email.be jouwdomein.be [www.jouwdomein.be]}"; shift
[ "$#" -ge 1 ] || { echo "Geef minstens 1 domein op."; exit 1; }
SRC="$(cd "$(dirname "$0")/.." && pwd)"; APP=/opt/sfdojo
export DEBIAN_FRONTEND=noninteractive

echo "==> Pakketten installeren"
apt-get update -y
apt-get install -y python3 python3-venv python3-pip nginx certbot rsync sqlite3

echo "==> Programma naar $APP"
id sfdojo >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin sfdojo
mkdir -p "$APP"
rsync -a --exclude .venv --exclude __pycache__ --exclude .env --exclude '*.db' --exclude uploads --exclude '*.log' "$SRC/" "$APP/"
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

if [ ! -f "$APP/.env" ]; then
  echo "==> .env aanmaken"
  cp "$APP/.env.example" "$APP/.env"
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$("$APP/.venv/bin/python" -c 'import secrets; print(secrets.token_hex(32))')|; s|^HTTPS_ONLY=.*|HTTPS_ONLY=1|; s|^TRUST_PROXY=.*|TRUST_PROXY=1|; s|^PUBLIC_URL=.*|PUBLIC_URL=https://$1|" "$APP/.env"
fi
chmod 600 "$APP/.env"; chown -R sfdojo:sfdojo "$APP"
if [ ! -f "$APP/sfdojo.db" ]; then
  echo "==> Database + beheerders aanmaken (NOTEER de wachtwoorden, ze worden maar één keer getoond)"
  (cd "$APP" && sudo -u sfdojo .venv/bin/python seed.py)
fi

echo "==> Service"
cat > /etc/systemd/system/sfdojo.service <<UNIT
[Unit]
Description=SF Dojo website
After=network.target

[Service]
User=sfdojo
Group=sfdojo
WorkingDirectory=$APP
ExecStart=$APP/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 2
Restart=always
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=$APP

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now sfdojo
systemctl restart sfdojo

echo "==> Dagelijkse back-up (03:15, 14 dagen bewaard)"
echo "15 3 * * * root bash $APP/deploy/backup.sh" > /etc/cron.d/sfdojo-backup

if [ "${SETUP_UFW:-0}" = 1 ]; then
  apt-get install -y ufw && ufw allow OpenSSH && ufw allow 'Nginx Full' && ufw --force enable
fi

echo "==> Domein(en) en HTTPS"
mkdir -p /etc/sfdojo; echo "$EMAIL" > /etc/sfdojo/email
bash "$APP/deploy/domain.sh" "$@"
echo "Klaar. Open https://$1/admin"

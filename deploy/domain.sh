#!/usr/bin/env bash
# Domein(en) toevoegen en HTTPS-certificaat (Let's Encrypt) aanvragen.
#   sudo bash /opt/sfdojo/deploy/domain.sh nieuwdomein.be www.nieuwdomein.be
# Eerst bij je domeinnaam-provider een A-record naar het IP van deze server zetten (en AAAA voor IPv6).
# Het eerste domein in /etc/sfdojo/domains is het hoofdadres (PUBLIC_URL). Verwijderen: regel schrappen in dat bestand en dit script opnieuw draaien.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Draai dit met sudo."; exit 1; }
D=/etc/sfdojo; APP=/opt/sfdojo; SITE=/etc/nginx/sites-available/sfdojo
mkdir -p "$D" /var/www/letsencrypt; touch "$D/domains"
[ -s "$D/email" ] || { echo "Zet eerst je e-mail: echo jij@mail.be | sudo tee $D/email"; exit 1; }
for d in "$@"; do grep -qx "$d" "$D/domains" || echo "$d" >> "$D/domains"; done
mapfile -t DOMAINS < <(grep -v '^$' "$D/domains")
[ "${#DOMAINS[@]}" -gt 0 ] || { echo "Geen domeinen opgegeven."; exit 1; }
NAMES="${DOMAINS[*]}"; MAIN="${DOMAINS[0]}"; CERT=/etc/letsencrypt/live/sfdojo

PROXY='        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;'
ACME='    location /.well-known/acme-challenge/ { root /var/www/letsencrypt; }'

# stap 1: alleen http (nodig om het certificaat aan te vragen)
cat > "$SITE" <<NGINX
server {
    listen 80; listen [::]:80;
    server_name $NAMES;
    client_max_body_size 160m;
$ACME
    location / {
$PROXY
    }
}
NGINX
ln -sf "$SITE" /etc/nginx/sites-enabled/sfdojo; rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

ARGS=(); for d in "${DOMAINS[@]}"; do ARGS+=(-d "$d"); done
certbot certonly --webroot -w /var/www/letsencrypt --non-interactive --agree-tos -m "$(cat "$D/email")" \
  --cert-name sfdojo --expand --deploy-hook "systemctl reload nginx" "${ARGS[@]}"

# stap 2: http -> https omleiden en https aanbieden
cat > "$SITE" <<NGINX
server {
    listen 80; listen [::]:80;
    server_name $NAMES;
$ACME
    location / { return 301 https://\$host\$request_uri; }
}
server {
    listen 443 ssl; listen [::]:443 ssl;
    http2 on;
    server_name $NAMES;
    ssl_certificate $CERT/fullchain.pem;
    ssl_certificate_key $CERT/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 160m;
    location / {
$PROXY
    }
}
NGINX
nginx -t && systemctl reload nginx

if [ -f "$APP/.env" ]; then
  sed -i "s|^PUBLIC_URL=.*|PUBLIC_URL=https://$MAIN|" "$APP/.env"; systemctl restart sfdojo
fi
echo "Klaar: $NAMES. Hoofdadres: https://$MAIN"
echo "Gebruik je Google/Microsoft-login? Zet bij hen als terugkeer-adres: https://$MAIN/admin/sso/google/callback (of /microsoft/callback)"

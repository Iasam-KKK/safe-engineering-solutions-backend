#!/bin/bash
# One-time setup of the VPS for the SAFE website, beside the owner's portfolio that already runs on it.
# Safe to run again: every step checks what is already there, and nothing of the portfolio (its
# user, services, Nginx sites, firewall, Cloudflare address list) is changed.
#
# Run as root from a copy of this folder taken from your own checkout, never from a release folder
# under /srv/safe (those files belong to the safe user, which the deploy key logs in as):
#   scp -r deploy ubuntu@<server IP>:safe-deploy
#   ssh ubuntu@<server IP>
#   sudo bash safe-deploy/bootstrap.sh <domain> "$(cat safe_deploy.pub)" [<Drive folder id>]
# <domain> is the bare name, like example.com. The public key is the one GitHub Actions deploys with
# (deploy/set-github-secrets.ps1 makes it); it may only log in as safe, and safe may only restart
# the two SAFE services (/etc/sudoers.d/safe-deploy).
set -euo pipefail

die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
say() { printf '\n== %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }

[ "$(id -u)" = 0 ] || die "run it with sudo"
DOMAIN=$(printf '%s' "${1:-}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')
DEPLOY_KEY=${2:-}
DRIVE_FOLDER=${3:-}
HERE=$(cd "$(dirname "$0")" && pwd)

case $DOMAIN in
    ''|*://*|*/*|www.*|api.*) die "pass the bare domain first, like example.com (no https://, www. or api.)" ;;
esac
[[ $DOMAIN =~ ^[a-z0-9-]+(\.[a-z0-9-]+)+$ ]] || die "\"$DOMAIN\" doesn't look like a domain name"
case $DEPLOY_KEY in
    *PRIVATE*) die "that is the private key; pass the public one (safe_deploy.pub)" ;;
    ssh-ed25519\ *|ssh-rsa\ *|ecdsa-sha2-*) ;;
    *) die "pass the deploy public key second, in quotes: \"\$(cat safe_deploy.pub)\"" ;;
esac
[[ -z $DRIVE_FOLDER || $DRIVE_FOLDER =~ ^[A-Za-z0-9_-]{20,}$ ]] \
    || die "the Drive folder id is the last part of the folder's link (letters, digits, - and _)"
case $HERE in /srv/*) die "copy this folder out of /srv first (see the top of this file)" ;; esac

API=api.$DOMAIN
WWW=www.$DOMAIN

say "Packages (already there for the portfolio; this only checks)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
# --no-upgrade: never upgrade what the portfolio already runs on (no surprise restarts)
apt-get install -y -q --no-upgrade nginx postgresql postgresql-client python3-venv nodejs certbot rsync curl openssl
NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]')
[ "$NODE_MAJOR" -ge 20 ] || die "Node $NODE_MAJOR is too old for the site (Next.js needs 20 or newer)"

say "The safe user and its folders"
if ! id safe >/dev/null 2>&1; then
    adduser --system --group --home /srv/safe --shell /bin/bash safe
fi
install -d -o safe -g safe -m 755 /srv/safe /srv/safe/api /srv/safe/api/releases /srv/safe/api/venvs \
    /srv/safe/web /srv/safe/web/releases /srv/safe/web/image-cache /srv/safe/media
# clients' attachments, the Drive copy and database copies: the safe user only
install -d -o safe -g safe -m 750 /srv/safe/private_media /srv/safe/drive_mirror
install -d -o safe -g safe -m 700 /srv/safe/backups

say "The deploy key (safe user only; no terminal or forwarding)"
install -d -o safe -g safe -m 700 /srv/safe/.ssh
KEYS=/srv/safe/.ssh/authorized_keys
touch "$KEYS"
KEY_BODY=$(printf '%s' "$DEPLOY_KEY" | awk '{print $2}')
grep -qF "$KEY_BODY" "$KEYS" || echo "restrict $DEPLOY_KEY" >> "$KEYS"
chown safe:safe "$KEYS"
chmod 600 "$KEYS"

say "What the deploy may run as root"
cat > /etc/sudoers.d/safe-deploy <<'EOF'
# GitHub Actions deploys as "safe" and may only restart the two SAFE services.
safe ALL=(root) NOPASSWD: /usr/bin/systemctl restart safe-web.service, /usr/bin/systemctl restart safe-api.service
EOF
chmod 440 /etc/sudoers.d/safe-deploy
visudo -cf /etc/sudoers.d/safe-deploy

say "Root's own copy of safe-manage"
# sudo safe-manage <command> runs manage.py as safe. A root-owned copy, because the one in a release
# folder can be changed by the safe user.
install -o root -g root -m 755 "$HERE/manage.sh" /usr/local/sbin/safe-manage

say "PostgreSQL and the settings files"
install -d -m 750 -o root -g safe /etc/safe
if [ ! -f /etc/safe/api.env ]; then
    DB_PW=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
    SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(50))")
    REVAL=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='safe'" | grep -q 1; then
        sudo -u postgres psql -qc "ALTER ROLE safe LOGIN PASSWORD '$DB_PW';"
    else
        sudo -u postgres psql -qc "CREATE ROLE safe LOGIN PASSWORD '$DB_PW';"
    fi
    sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='safe'" | grep -q 1 \
        || sudo -u postgres createdb -O safe safe
    cat > /etc/safe/api.env <<EOF
# The SAFE API's settings (backend/.env.example lists them all). Made by deploy/bootstrap.sh.
# Read by safe-api, the Drive sync, the nightly backup and safe-manage. After a change:
#   sudo systemctl restart safe-api
# Put a value with spaces, \$, < or > in single quotes.
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=$SECRET
DJANGO_ALLOWED_HOSTS=$API,$DOMAIN,127.0.0.1,localhost
DJANGO_CSRF_TRUSTED_ORIGINS=https://$API
# Cloudflare and Nginx send visitors to HTTPS; the site calls the API on 127.0.0.1 over plain HTTP.
DJANGO_SECURE_SSL_REDIRECT=false
CORS_ALLOWED_ORIGINS=https://$DOMAIN
DATABASE_URL=postgres://safe:$DB_PW@127.0.0.1:5432/safe
FRONTEND_URL=https://$DOMAIN
REVALIDATE_BASE=http://127.0.0.1:3100
REVALIDATE_SECRET=$REVAL
RELATIVE_MEDIA_URLS=true
SERVE_MEDIA=false
MEDIA_ROOT=/srv/safe/media
PRIVATE_MEDIA_ROOT=/srv/safe/private_media
DRF_NUM_PROXIES=1

# Google Drive sync (deploy/README.md, "Drive sync")
GOOGLE_SERVICE_ACCOUNT_FILE=/etc/safe/drive-sync-key.json
DRIVE_FOLDER_ID=$DRIVE_FOLDER
DRIVE_MIRROR_DIR=/srv/safe/drive_mirror

# Emails for new requests and new Drive work (deploy/README.md, "Email")
# EMAIL_HOST=
# EMAIL_PORT=587
# EMAIL_HOST_USER=
# EMAIL_HOST_PASSWORD=
# DEFAULT_FROM_EMAIL='SAFE website <no-reply@$DOMAIN>'
# INQUIRY_NOTIFY_EMAILS=
EOF
fi
if [ ! -f /etc/safe/web.env ]; then
    REVAL=$(sed -n 's/^REVALIDATE_SECRET=//p' /etc/safe/api.env)
    [ -n "$REVAL" ] || die "/etc/safe/api.env has no REVALIDATE_SECRET"
    cat > /etc/safe/web.env <<EOF
# The SAFE website's settings while it runs. Made by deploy/bootstrap.sh. After a change:
#   sudo systemctl restart safe-web
API_URL=http://127.0.0.1:8100
# the same value as in /etc/safe/api.env
REVALIDATE_SECRET=$REVAL
EOF
fi
if [ -n "$DRIVE_FOLDER" ] && grep -q '^DRIVE_FOLDER_ID=$' /etc/safe/api.env; then
    sed -i "s/^DRIVE_FOLDER_ID=\$/DRIVE_FOLDER_ID=$DRIVE_FOLDER/" /etc/safe/api.env
fi
chown root:safe /etc/safe/api.env /etc/safe/web.env
chmod 640 /etc/safe/api.env /etc/safe/web.env
if [ -f /etc/safe/drive-sync-key.json ]; then
    chown root:safe /etc/safe/drive-sync-key.json
    chmod 640 /etc/safe/drive-sync-key.json
fi

say "Services"
install -m 644 "$HERE"/systemd/safe-*.service "$HERE"/systemd/safe-*.timer /etc/systemd/system/
systemctl daemon-reload
# They start with the first deploy (no release is here yet).
systemctl enable safe-api.service safe-web.service
systemctl enable --now safe-backup.timer
# The Drive sync stays off until the laptop's data and Drive copy are here (deploy/README.md).

say "Nginx"
[ -f /etc/nginx/conf.d/cloudflare-realip.conf ] \
    || warn "/etc/nginx/conf.d/cloudflare-realip.conf is missing: rate limits and the request form's limit would see Cloudflare's addresses, not visitors'"
fill() { sed "s/__DOMAIN__/$DOMAIN/g" "$HERE/nginx/$1" > "/etc/nginx/$2"; chmod 644 "/etc/nginx/$2"; }
fill safe-http.conf sites-available/safe-http.conf
fill safe-site.conf sites-available/safe-site.conf
fill safe-api.conf sites-available/safe-api.conf
fill safe-ratelimit.conf conf.d/safe-ratelimit.conf
ln -sfn /etc/nginx/sites-available/safe-http.conf /etc/nginx/sites-enabled/safe-http.conf
nginx -t
systemctl reload nginx

say "HTTPS"
# One certificate, named after the domain, for every SAFE name that already reaches this server.
# The names can move over one by one: api.<domain> first while the site's own names still point at
# the old host, those join the same certificate when this runs again after they've moved.
# Through Cloudflare's proxy: Let's Encrypt fetches a file over plain HTTP, Cloudflare passes it to
# port 80 here. Each name is tried first, so a DNS slip doesn't use up Let's Encrypt's attempts.
reaches_here() {
    local token got
    token=probe-$(python3 -c "import secrets; print(secrets.token_hex(8))")
    install -d -m 755 /var/www/html/.well-known/acme-challenge
    echo "$token" > "/var/www/html/.well-known/acme-challenge/$token"
    got=$(curl -4 -s --max-time 15 "http://$1/.well-known/acme-challenge/$token" || true)
    rm -f "/var/www/html/.well-known/acme-challenge/$token"
    [ "$got" = "$token" ]
}
LIVE_CERT=/etc/letsencrypt/live/$DOMAIN/cert.pem
cert_names() {
    [ -f "$LIVE_CERT" ] || return 0
    openssl x509 -in "$LIVE_CERT" -noout -ext subjectAltName | grep -o 'DNS:[^,]*' | sed 's/DNS://'
}
on_cert() { cert_names | grep -qx "$1"; }

WANT=()
for name in "$API" "$DOMAIN" "$WWW"; do
    if on_cert "$name" || reaches_here "$name"; then
        WANT+=("$name")
    else
        warn "http://$name/ doesn't reach this server yet. In Cloudflare: an A record for it to this server's IPv4, Proxied (orange cloud); SSL/TLS mode Full (strict); \"Always Use HTTPS\" off until this works. Then run this again."
    fi
done
MISSING=0
for name in "${WANT[@]}"; do on_cert "$name" || MISSING=1; done
if [ "$MISSING" = 1 ]; then
    ARGS=()
    for name in "${WANT[@]}"; do ARGS+=(-d "$name"); done
    certbot certonly --webroot -w /var/www/html --non-interactive --agree-tos \
        --register-unsafely-without-email --deploy-hook "systemctl reload nginx" \
        --cert-name "$DOMAIN" --expand "${ARGS[@]}" || warn "certbot couldn't get the certificate (its message is above)"
fi
if on_cert "$API"; then
    ln -sfn /etc/nginx/sites-available/safe-api.conf /etc/nginx/sites-enabled/safe-api.conf
fi
if on_cert "$DOMAIN" && on_cert "$WWW"; then
    ln -sfn /etc/nginx/sites-available/safe-site.conf /etc/nginx/sites-enabled/safe-site.conf
fi
nginx -t
systemctl reload nginx

say "Checks (nothing changed here)"
# (read into a variable: with pipefail, grep -q closing the pipe early would hide a "yes")
SSH_PASSWORDS=$(sshd -T 2>/dev/null | grep -i '^passwordauthentication' || true)
if [[ ${SSH_PASSWORDS,,} == *yes* ]]; then
    warn "SSH accepts passwords. Once your own key logs in, set PasswordAuthentication no."
fi
systemctl is-active --quiet fail2ban 2>/dev/null || warn "fail2ban isn't running (sudo apt install fail2ban)"
df -h /srv | tail -1 | awk '{print "Free space for /srv: " $4 " of " $2}'

say "Done"
echo "Enabled sites: $(ls /etc/nginx/sites-enabled | tr '\n' ' ')"
echo "HTTPS here for: $(cert_names | tr '\n' ' ')"
for name in "$API" "$DOMAIN" "$WWW"; do
    on_cert "$name" || echo "Not yet: $name (see the warning above; run this again once it points here)"
done
echo "Next: deploy/README.md, \"First deploy\"."

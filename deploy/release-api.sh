#!/bin/sh
# Makes an uploaded copy of the API live. The GitHub Actions workflow runs it on the server as the
# safe user, after copying the code to /srv/safe/api/releases/<name>:
#   sh /srv/safe/api/releases/<name>/deploy/release-api.sh <name>
# It gives the copy its packages, checks the settings, saves the database if migrations are due,
# migrates, collects the admin's static files, switches /srv/safe/api/current to the new copy,
# restarts the API and checks /api/health/. If the check fails it switches back.
set -eu

NAME=${1:?pass the release name}
ROOT=/srv/safe/api
NEW=$ROOT/releases/$NAME
cd "$NEW"

# One virtualenv per requirements.txt (and Python version), shared by the releases that list the
# same packages. Each release links to its own, so switching back also brings its packages back.
HASH=$( { python3 --version; cat requirements.txt; } | sha256sum | cut -c1-16)
VENV=$ROOT/venvs/$HASH
if [ ! -f "$VENV/.complete" ]; then
    echo "Installing packages into venvs/$HASH"
    rm -rf "$VENV"
    python3 -m venv "$VENV"
    "$VENV/bin/python" -m pip install --quiet --upgrade pip
    "$VENV/bin/python" -m pip install --quiet -r requirements.txt
    touch "$VENV/.complete"
fi
ln -sfn "$VENV" "$NEW/.venv"
PY=$NEW/.venv/bin/python

# The API's settings, as the service sees them.
set -a
. /etc/safe/api.env
set +a
export DJANGO_DEBUG=false

"$PY" manage.py check --deploy --fail-level ERROR
# Migrations can't be undone by switching back, so the database is saved first.
if ! "$PY" manage.py migrate --check >/dev/null 2>&1; then
    sh "$NEW/deploy/backup-db.sh" "before-$NAME"
fi
"$PY" manage.py migrate --noinput
"$PY" manage.py collectstatic --noinput --verbosity 0

OLD=$(readlink "$ROOT/current" 2>/dev/null || true)
ln -sfn "releases/$NAME" "$ROOT/current.next"
mv -Tf "$ROOT/current.next" "$ROOT/current"
sudo /usr/bin/systemctl restart safe-api.service

# 200 exactly: a redirect here means DJANGO_SECURE_SSL_REDIRECT is on, which breaks the site's
# own calls to the API on 127.0.0.1.
ok=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8100/api/health/ || true)
    if [ "$code" = 200 ]; then ok=1; break; fi
    sleep 1
done
if [ "$ok" != 1 ]; then
    echo "The new API did not answer /api/health/ with 200 (last answer: $code). Its log: sudo journalctl -u safe-api -n 50" >&2
    if [ -n "$OLD" ]; then
        echo "Switching back to $OLD." >&2
        ln -sfn "$OLD" "$ROOT/current.next"
        mv -Tf "$ROOT/current.next" "$ROOT/current"
        sudo /usr/bin/systemctl restart safe-api.service
        echo "If this release migrated the database, its copy from just before is in /srv/safe/backups." >&2
    fi
    exit 1
fi

# Keep the three newest copies (names start with the time) and whatever is live.
LIVE=$(readlink "$ROOT/current")
cd "$ROOT/releases"
ls -1 | sort -r | tail -n +4 | while read -r old; do
    [ "releases/$old" = "$LIVE" ] || rm -rf "$old"
done
# ...and only the virtualenvs those copies use.
for v in "$ROOT"/venvs/*; do
    [ -d "$v" ] || continue
    used=0
    for r in "$ROOT"/releases/*; do
        [ "$(readlink "$r/.venv" 2>/dev/null)" = "$v" ] && { used=1; break; }
    done
    [ "$used" = 1 ] || rm -rf "$v"
done
echo "API live: $NAME"

#!/bin/sh
# Saves the SAFE database to /srv/safe/backups as a compressed pg_dump, keeping the newest 14.
# Run as the safe user: nightly by safe-backup.timer, and by release-api.sh before migrations.
#   sh backup-db.sh [label]
# To put one back (stops the API meanwhile):
#   sudo systemctl stop safe-api
#   sudo -u safe sh -c 'set -a; . /etc/safe/api.env; pg_restore --clean --if-exists --no-owner -d "$DATABASE_URL" /srv/safe/backups/<file>.dump'
#   sudo systemctl start safe-api
set -eu

LABEL=${1:-manual}
DIR=/srv/safe/backups
umask 077
mkdir -p "$DIR"
if [ -z "${DATABASE_URL:-}" ]; then
    set -a
    . /etc/safe/api.env
    set +a
fi

OUT=$DIR/safe-$(date -u +%Y%m%d-%H%M%S)-$LABEL.dump
pg_dump --format=custom --no-owner --dbname="$DATABASE_URL" --file="$OUT.part"
mv "$OUT.part" "$OUT"

ls -1 "$DIR"/safe-*.dump 2>/dev/null | sort -r | tail -n +15 | while read -r old; do rm -f "$old"; done
echo "Database saved: $OUT"

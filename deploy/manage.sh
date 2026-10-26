#!/bin/sh
# Runs manage.py on the server, as the safe user with the API's settings, against the live copy.
# bootstrap.sh installs a root-owned copy as /usr/local/sbin/safe-manage; use that one:
#   sudo safe-manage createsuperuser
#   sudo safe-manage sync_drive
set -eu
exec sudo -u safe env DJANGO_DEBUG=false sh -c '
    set -a; . /etc/safe/api.env; set +a
    cd /srv/safe/api/current && exec ./.venv/bin/python manage.py "$@"
' manage.sh "$@"

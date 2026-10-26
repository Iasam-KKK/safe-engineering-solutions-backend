"""Windowless launcher for `manage.py sync_drive`, for Windows Task Scheduler.

Run it with the *base* interpreter's pythonw.exe (see scripts/schedule-drive-sync.ps1), not the
one in .venv\\Scripts: uv's venv pythonw.exe is a small launcher that starts the console python.exe,
which pops up a terminal window on every run. This script adds the venv's packages itself.
Output goes to drive_mirror/sync.log.
"""

import os
import pathlib
import site
import sys
import traceback

BASE = pathlib.Path(__file__).resolve().parents[1]
VENV_SITE = BASE / ".venv" / "Lib" / "site-packages"
if VENV_SITE.is_dir():
    site.addsitedir(str(VENV_SITE))  # also runs the venv's .pth files

log_dir = BASE / "drive_mirror"
log_dir.mkdir(exist_ok=True)
log = open(log_dir / "sync.log", "a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = log

os.chdir(BASE)
sys.path.insert(0, str(BASE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
try:
    import django

    django.setup()
    from django.core.management import CommandError, call_command

    try:
        call_command("sync_drive", stdout=log, stderr=log)
    except CommandError as e:  # setup problems (no key, folder not shared): one readable line
        from datetime import datetime

        print(f"{datetime.now():%Y-%m-%d %H:%M} sync_drive: {e}", file=log)
except SystemExit:
    pass
except Exception:
    traceback.print_exc(file=log)

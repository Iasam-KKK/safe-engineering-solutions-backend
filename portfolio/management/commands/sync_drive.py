"""Pick up anything new in the team's Google Drive folder and load it into the website.

    uv run python manage.py sync_drive                  # one check (run it from cron / Task Scheduler)
    uv run python manage.py sync_drive --watch 15       # keep checking every 15 minutes
    uv run python manage.py sync_drive --path "G:/My Drive/SES Website Data"   # a synced folder instead of the API

With GOOGLE_SERVICE_ACCOUNT_FILE and DRIVE_FOLDER_ID set, the folder is mirrored through the
Drive API (sheets and docs included). Nothing is imported when nothing changed. New projects
arrive as drafts; team, partners, reviews and contact details are updated in place. A summary of
what arrived (or why a sync failed) is kept in the admin under "Drive imports", and is also emailed
to IMPORT_NOTIFY_EMAILS when that and the email settings are set.
"""

import os
import time
from pathlib import Path

import requests
from django.conf import settings
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from content.sheets_import import sync_content
from portfolio import drive_sync
from portfolio.importer import run_import
from portfolio.models import DriveImport

LOCK_MAX_AGE = 3 * 3600  # a crashed run's lock is ignored after 3 hours


class Command(BaseCommand):
    help = "Mirror the team's Google Drive folder and import what changed (projects, team, partners, reviews, contact)."

    def add_arguments(self, parser):
        parser.add_argument("--path", help="Use this local folder (e.g. Google Drive for desktop) instead of the Drive API")
        parser.add_argument("--watch", type=float, metavar="MINUTES", help="Keep running, checking every MINUTES")
        parser.add_argument("--force", action="store_true", help="Import even if nothing changed")
        parser.add_argument("--publish", action="store_true", help="Make new projects live at once (default: drafts)")
        parser.add_argument("--dry-run", action="store_true", help="Mirror and report, but don't change the database")

    def handle(self, *args, **o):
        if not o["path"] and not (settings.GOOGLE_SERVICE_ACCOUNT_FILE and settings.DRIVE_FOLDER_ID):
            raise CommandError(
                "Set GOOGLE_SERVICE_ACCOUNT_FILE and DRIVE_FOLDER_ID in backend/.env (see README: 'Drive sync'), "
                "or pass --path to a synced folder."
            )
        while True:
            try:
                self.cycle(o)
            except drive_sync.DriveError as exc:  # setup problem: say what to fix, no traceback
                self.log(f"sync failed: {exc}", error=True)
                self.record_failure(str(exc))
                if not o["watch"]:
                    raise CommandError(str(exc)) from None
            except Exception as exc:  # keep watching after a network hiccup
                self.log(f"sync failed: {exc}", error=True)
                if not isinstance(exc, requests.RequestException):  # a hiccup isn't worth a note
                    self.record_failure(f"{type(exc).__name__}: {exc}")
                if not o["watch"]:
                    raise
            if not o["watch"]:
                return
            time.sleep(o["watch"] * 60)

    # ------------------------------------------------------------------
    def log(self, msg, error=False):
        stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M")
        line = f"[{stamp}] {msg}"
        self.stdout.write(self.style.ERROR(line) if error else line)

    def cycle(self, o):
        mirror_dir = Path(settings.DRIVE_MIRROR_DIR)
        mirror_dir.mkdir(parents=True, exist_ok=True)
        lock = mirror_dir / ".sync.lock"
        if lock.exists() and time.time() - lock.stat().st_mtime < LOCK_MAX_AGE:
            self.log("another sync is still running; skipped")
            return
        lock.write_text(str(os.getpid()))
        try:
            root, changed = self.fetch(o, mirror_dir)
            if not changed and not o["force"]:
                self.log("no changes")
                return
            self.log(f"changes found, importing from {root}")
            notes: list[str] = []
            say = lambda m: (self.log(m), notes.append(m))  # noqa: E731
            report = run_import(root, dry_run=o["dry_run"], publish=o["publish"], out=say)
            for line in report.lines:
                say(line)
            for w in report.warnings:
                say("CHECK: " + w)
            if not o["dry_run"]:
                sync_content(root, say=say, force=o["force"])
                if notes:  # the admin's "Drive imports" list: works with or without email
                    DriveImport.objects.create(summary="\n".join(notes))
            self.notify(notes)
        finally:
            lock.unlink(missing_ok=True)

    def fetch(self, o, mirror_dir: Path) -> tuple[Path, bool]:
        """The folder to import from, and whether anything changed since last time."""
        if o["path"]:
            root = Path(o["path"])
            if not root.is_dir():
                raise CommandError(f"Folder not found: {root}")
            state = mirror_dir / ".local-fingerprint"
            fp = drive_sync.fingerprint(root)
            changed = not state.exists() or state.read_text() != fp
            state.write_text(fp)
            return root, changed
        rep = drive_sync.mirror(drive_sync.session(), settings.DRIVE_FOLDER_ID, mirror_dir)
        for path in rep.downloaded:
            self.log(f"  new or changed: {path}")
        for path in rep.removed:
            self.log(f"  removed on Drive: {path}")
        return mirror_dir, rep.changed

    def record_failure(self, summary: str):
        """A failed sync shows in the admin's Drive imports, once until someone ticks it checked."""
        try:
            last = DriveImport.objects.first()
            if last and last.failed and not last.checked and last.summary == summary:
                return
            DriveImport.objects.create(summary=summary, failed=True)
        except Exception as exc:  # e.g. the database itself is the problem
            self.log(f"couldn't note the failure in the admin: {exc}", error=True)

    def notify(self, notes: list[str]):
        if not settings.IMPORT_NOTIFY_EMAILS or not notes:
            return
        body = "New files from the team's Drive folder were loaded into the website:\n\n" + "\n".join(notes)
        body += "\n\nNew projects are drafts: check each sheet in the admin, then tick Published."
        try:
            EmailMessage("[SAFE website] New work from Drive", body, to=settings.IMPORT_NOTIFY_EMAILS).send()
        except Exception as exc:
            self.log(f"summary email failed: {exc}", error=True)

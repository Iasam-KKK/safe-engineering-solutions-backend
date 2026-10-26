"""Tell the Next.js site to rebuild cached pages after content changes in the admin.

Pages on the public site are pre-rendered for speed and search engines. When a
project, partner, review or setting is saved, Django POSTs the affected cache
tags to {FRONTEND_URL}/api/revalidate so the change shows within seconds.

Tags used by the frontend:
  site                 header/footer details, partners, reviews, team
  projects             every list of projects (homepage pile, /projects, sitemap)
  project:<slug>       one project page
"""

import json
import logging
import threading
import urllib.request
from contextlib import contextmanager

from django.conf import settings
from django.db import transaction

log = logging.getLogger("safe.revalidate")


def _post(tags):
    url = settings.REVALIDATE_BASE + "/api/revalidate"
    body = json.dumps({"tags": sorted(tags)}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "x-revalidate-secret": settings.REVALIDATE_SECRET},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as res:
            log.info("revalidated %s -> %s", sorted(tags), res.status)
    except Exception as exc:  # the site keeps its older copy; log and move on
        log.warning("revalidate %s failed: %s", sorted(tags), exc)


_batch = threading.local()


@contextmanager
def batched():
    """Collect revalidations (e.g. during a big import) and send them once at the end.

    Sent in the calling thread, not a background one: imports and the Drive sync run as
    short commands, and a background thread would die with the process before it posts.
    """
    _batch.tags = set()
    try:
        yield
    finally:
        tags, _batch.tags = _batch.tags, None
        if tags and settings.REVALIDATE_SECRET:
            transaction.on_commit(lambda: _post(tags))


def revalidate(*tags):
    """Queue a revalidation for after the current database transaction commits."""
    if getattr(_batch, "tags", None) is not None:
        _batch.tags.update(tags)
        return
    if not settings.REVALIDATE_SECRET or not tags:
        return
    tags = set(tags)
    # not a daemon thread: a short command (manage.py shell, loaddata) waits for the post before
    # it exits, instead of dropping it; the admin's requests still don't wait for it
    transaction.on_commit(lambda: threading.Thread(target=_post, args=(tags,), daemon=False).start())

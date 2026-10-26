"""Private storage for files clients attach to inquiries.

These files never get a public URL; staff download them through the admin
(see leads.admin), which checks the login first.
"""

import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateStorage(FileSystemStorage):
    """Files under PRIVATE_MEDIA_ROOT; .url() raises because there is no public URL."""

    @property
    def base_location(self):
        return settings.PRIVATE_MEDIA_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    @property
    def base_url(self):
        return None


def private_storage():
    """Callable storage (Django calls it), so migrations don't hard-code the folder."""
    return PrivateStorage()

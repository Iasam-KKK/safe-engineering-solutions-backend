"""Refresh the public site's cached pages when site-wide content changes."""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from config.revalidate import revalidate

from .models import Partner, Review, SiteSettings, TeamMember


@receiver([post_save, post_delete], sender=SiteSettings)
@receiver([post_save, post_delete], sender=Partner)
@receiver([post_save, post_delete], sender=Review)
@receiver([post_save, post_delete], sender=TeamMember)
def site_changed(sender, instance, **kwargs):
    revalidate("site")

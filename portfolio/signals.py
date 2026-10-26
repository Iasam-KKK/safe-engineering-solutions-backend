"""Refresh the public site's cached pages when portfolio content changes."""

from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from config.revalidate import revalidate

from .models import Category, Project, ProjectFact, ProjectMedia


@receiver([post_save, post_delete], sender=Project)
def project_changed(sender, instance, **kwargs):
    revalidate("projects", "site", f"project:{instance.slug}")


@receiver([post_save, post_delete], sender=ProjectMedia)
@receiver([post_save, post_delete], sender=ProjectFact)
def project_part_changed(sender, instance, **kwargs):
    try:
        slug = instance.project.slug
    except Project.DoesNotExist:  # project deleted along with its parts
        return
    revalidate("projects", f"project:{slug}")


@receiver(m2m_changed, sender=Project.categories.through)
def project_categories_changed(sender, instance, action, **kwargs):
    if action.startswith("post_"):
        revalidate("projects", "site")


@receiver([post_save, post_delete], sender=Category)
def category_changed(sender, instance, **kwargs):
    revalidate("projects", "site")

from django.contrib.admin.apps import AdminConfig


class SafeAdminConfig(AdminConfig):
    """Django's admin, served by config.admin_site.SafeAdminSite (see INSTALLED_APPS)."""

    default_site = "config.admin_site.SafeAdminSite"

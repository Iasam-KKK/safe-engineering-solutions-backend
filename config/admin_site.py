"""The admin, with what needs a look counted on its home page and in the side menu:
"Inquiries (2 new)", "Drive imports (1 new)". This replaces email alerts until email is set up."""

from django.contrib import admin


class SafeAdminSite(admin.AdminSite):
    site_header = "SAFE Engineering Solutions"
    site_title = "SAFE admin"
    index_title = "Website content"

    def get_app_list(self, request, app_label=None):
        from leads.models import Inquiry
        from portfolio.models import DriveImport

        waiting = {
            ("leads", "Inquiry"): lambda: Inquiry.objects.filter(status=Inquiry.Status.NEW).count(),
            ("portfolio", "DriveImport"): lambda: DriveImport.objects.filter(checked=False).count(),
        }
        apps = super().get_app_list(request, app_label)
        for app in apps:
            for model in app["models"]:
                count = waiting.get((app["app_label"], model["object_name"]))
                n = count() if count else 0
                if n:
                    model["name"] = f"{model['name']} ({n} new)"
        return apps

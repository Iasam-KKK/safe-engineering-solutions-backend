from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse

from portfolio.admin import thumb

from .models import Partner, Review, SiteSettings, TeamMember


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    """Single row: the list page jumps straight to the edit form."""

    fieldsets = [
        ("Company", {"fields": ["company_name", "email", "whatsapp_number", "linkedin_url", "fiverr_url"]}),
        ("Video consultation", {"fields": ["booking_url", "consultation_length"]}),
        ("California stamping", {"fields": ["stamping_engineer", "stamping_license"]}),
        ("Review rating", {"fields": [("rating_value", "rating_count"), ("rating_source", "rating_as_of")]}),
    ]

    def changelist_view(self, request, extra_context=None):
        obj = SiteSettings.load()
        return redirect(reverse("admin:content_sitesettings_change", args=[obj.pk]))

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Partner)
class PartnerAdmin(admin.ModelAdmin):
    list_display = ["logo_thumb", "name", "website", "order", "is_active"]
    list_display_links = ["logo_thumb", "name"]
    list_editable = ["order", "is_active"]
    search_fields = ["name"]

    @admin.display(description="Logo")
    def logo_thumb(self, obj):
        return thumb(obj.logo, 48)


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ["author", "country", "rating", "source", "date", "project", "is_featured", "order"]
    list_editable = ["is_featured", "order"]
    list_filter = ["source", "rating", "is_featured"]
    search_fields = ["author", "text"]
    autocomplete_fields = ["project"]


@admin.register(TeamMember)
class TeamMemberAdmin(admin.ModelAdmin):
    list_display = ["photo_thumb", "name", "role", "credentials", "order", "is_active"]
    list_display_links = ["photo_thumb", "name"]
    list_editable = ["order", "is_active"]

    @admin.display(description="Photo")
    def photo_thumb(self, obj):
        return thumb(obj.photo, 48, contain=False)

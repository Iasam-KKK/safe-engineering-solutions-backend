from zoneinfo import available_timezones

from django import forms
from django.contrib import admin
from django.db import models
from django.shortcuts import redirect
from django.urls import reverse

from portfolio.admin import thumb

from .models import Partner, Review, SiteSettings, TeamMember, VideoCallDayOff, VideoCallHours


class SiteSettingsForm(forms.ModelForm):
    booking_timezone = forms.ChoiceField(
        label="Your time zone",
        help_text=SiteSettings._meta.get_field("booking_timezone").help_text,
        choices=[(z, z.replace("_", " ")) for z in sorted(available_timezones()) if "/" in z and not z.startswith("Etc/")],
    )

    class Meta:
        model = SiteSettings
        fields = "__all__"


# Plain browser pickers. The admin's own add a note beside the field saying to enter times in UTC, which is wrong here:
# these are the hours of the time zone chosen above them, and plain clock times, not moments.
class VideoCallHoursInline(admin.TabularInline):
    model = VideoCallHours
    extra = 1
    formfield_overrides = {models.TimeField: {"widget": forms.TimeInput(attrs={"type": "time"}, format="%H:%M")}}


class VideoCallDayOffInline(admin.TabularInline):
    model = VideoCallDayOff
    extra = 1
    formfield_overrides = {models.DateField: {"widget": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}}


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    """Single row: the list page jumps straight to the edit form."""

    form = SiteSettingsForm
    # the video-call tables follow the last section, so that one is about video calls too
    fieldsets = [
        ("Company", {"fields": ["company_name", "email", "whatsapp_number", "linkedin_url", "instagram_url", "fiverr_url"]}),
        ("California stamping", {"fields": ["stamping_engineer", "stamping_license"]}),
        ("Review rating", {"fields": [("rating_value", "rating_count"), ("rating_source", "rating_as_of")]}),
        (
            "Video calls",
            {
                "description": "Clients pick a time on the contact page, from the hours in the table at the bottom of this page. A booked time disappears from the calendar. To free one, set that request's status to Closed.",
                "fields": [
                    "booking_timezone",
                    ("booking_slot_minutes", "booking_notice_hours", "booking_days_ahead"),
                    "meeting_link",
                    "booking_url",
                    "consultation_length",
                ],
            },
        ),
    ]
    inlines = [VideoCallHoursInline, VideoCallDayOffInline]

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

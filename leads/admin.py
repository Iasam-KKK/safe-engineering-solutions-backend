from django.contrib import admin
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from content.models import SiteSettings

from .booking import in_zone
from .models import Inquiry, InquiryFile


class FileInline(admin.TabularInline):
    model = InquiryFile
    extra = 0
    can_delete = False
    fields = ["download", "size_mb"]
    readonly_fields = ["download", "size_mb"]

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="File")
    def download(self, obj):
        url = reverse("admin:leads_inquiry_file", args=[obj.pk])
        return format_html('<a href="{}">{}</a>', url, obj.original_name)

    @admin.display(description="Size")
    def size_mb(self, obj):
        return f"{obj.size / 1024 / 1024:.1f} MB"


class CallFilter(admin.SimpleListFilter):
    title = "video call"
    parameter_name = "call"

    def lookups(self, request, model_admin):
        return [("upcoming", "Upcoming"), ("past", "Past"), ("none", "No time picked")]

    def queryset(self, request, queryset):
        now = timezone.now()
        if self.value() == "upcoming":
            return queryset.filter(scheduled_at__gte=now).exclude(status=Inquiry.Status.CLOSED).order_by("scheduled_at")
        if self.value() == "past":
            return queryset.filter(scheduled_at__lt=now)
        if self.value() == "none":
            return queryset.filter(scheduled_at__isnull=True)
        return queryset


@admin.register(Inquiry)
class InquiryAdmin(admin.ModelAdmin):
    list_display = ["created_at", "kind", "name", "email", "call_time", "project_type", "location", "file_count", "status"]
    list_editable = ["status"]
    list_filter = [CallFilter, "status", "kind", "project_type", "role", "timing"]
    search_fields = ["name", "email", "location", "details"]
    date_hierarchy = "created_at"
    inlines = [FileInline]
    readonly_fields = [
        "kind", "name", "email", "phone", "role", "project_type", "location", "timing", "details",
        "source_page", "ip_address", "created_at", "call_time", "call_their_time", "call_minutes",
    ]
    fieldsets = [
        ("Request", {"fields": ["kind", ("name", "email", "phone"), ("role", "project_type"), ("location", "timing"), "details"]}),
        (
            "Video call",
            {
                "description": "Setting the status to Closed frees this time for someone else.",
                "fields": [("call_time", "call_their_time", "call_minutes")],
            },
        ),
        ("Follow-up", {"fields": ["status", "internal_notes"]}),
        ("Technical", {"classes": ["collapse"], "fields": ["source_page", "ip_address", "created_at"]}),
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("files")

    def has_add_permission(self, request):
        return False

    @admin.display(description="Files")
    def file_count(self, obj):
        return len(obj.files.all())

    @admin.display(description="Video call (your time)", ordering="scheduled_at")
    def call_time(self, obj):
        return in_zone(obj.scheduled_at, SiteSettings.load().booking_timezone) if obj.scheduled_at else "-"

    @admin.display(description="Their time")
    def call_their_time(self, obj):
        if not obj.scheduled_at:
            return "-"
        return f"{in_zone(obj.scheduled_at, obj.client_timezone)} ({obj.client_timezone or 'UTC'})"

    def get_urls(self):
        return [
            path("file/<int:pk>/", self.admin_site.admin_view(self.download_file), name="leads_inquiry_file"),
        ] + super().get_urls()

    def download_file(self, request, pk):
        """Staff-only download of a client's attachment (these files have no public URL)."""
        if not self.has_view_permission(request):
            raise Http404
        f = get_object_or_404(InquiryFile, pk=pk)
        return FileResponse(f.file.open("rb"), as_attachment=True, filename=f.original_name)

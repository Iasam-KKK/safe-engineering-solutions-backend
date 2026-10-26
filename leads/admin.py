from django.contrib import admin
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils.html import format_html

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


@admin.register(Inquiry)
class InquiryAdmin(admin.ModelAdmin):
    list_display = ["created_at", "kind", "name", "email", "project_type", "location", "file_count", "status"]
    list_editable = ["status"]
    list_filter = ["status", "kind", "project_type", "role", "timing"]
    search_fields = ["name", "email", "location", "details"]
    date_hierarchy = "created_at"
    inlines = [FileInline]
    readonly_fields = [
        "kind", "name", "email", "phone", "role", "project_type", "location", "timing", "details",
        "source_page", "ip_address", "created_at",
    ]
    fieldsets = [
        ("Request", {"fields": ["kind", ("name", "email", "phone"), ("role", "project_type"), ("location", "timing"), "details"]}),
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

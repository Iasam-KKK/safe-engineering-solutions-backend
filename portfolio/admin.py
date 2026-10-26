from django import forms
from django.contrib import admin, messages
from django.utils.html import format_html

from .models import Category, DriveImport, Project, ProjectFact, ProjectMedia


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": ".pdf,.jpg,.jpeg,.png,.webp,.tif,.tiff,.mp4,.mov,.m4v,.webm"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single = super().clean
        if isinstance(data, (list, tuple)):
            return [single(d, initial) for d in data]
        return [single(data, initial)] if data else []


class ProjectForm(forms.ModelForm):
    add_files = MultipleFileField(
        required=False,
        label="Add files",
        help_text=(
            "Pick several at once. PDFs: every page becomes a sheet (sheet numbers read from the title block). "
            "Images named like \"A101 Floor plan.jpg\" become sheets, others renders. MP4 videos become videos "
            "with a poster. Check every new sheet for names, addresses and stamps before publishing."
        ),
    )
    add_as = forms.ChoiceField(
        required=False,
        label="Images are",
        choices=[("auto", "Decide from the file name"), ("sheet", "Drawing sheets"), ("render", "Renders or photos"),
                 ("before", "Before images (matched to a sheet or render by name)")],
        initial="auto",
    )

    class Meta:
        model = Project
        fields = "__all__"


def thumb(field, size=72, contain=True):
    if not field:
        return "—"
    fit = "contain" if contain else "cover"
    return format_html(
        '<img src="{}" style="width:{}px;height:{}px;object-fit:{};background:#fff;border:1px solid #ccc">',
        field.url, size, size, fit,
    )


@admin.register(DriveImport)
class DriveImportAdmin(admin.ModelAdmin):
    """Each Drive sync that loaded something (or failed) leaves its summary here."""

    list_display = ["created_at", "result", "what_arrived", "checked"]
    list_editable = ["checked"]
    list_filter = ["checked", "failed"]
    fields = ["created_at", "result", "summary", "checked"]
    readonly_fields = ["created_at", "result", "summary"]

    def has_add_permission(self, request):
        return False

    @admin.display(description="Result")
    def result(self, obj):
        return "Failed" if obj.failed else "Loaded"

    @admin.display(description="What arrived")
    def what_arrived(self, obj):
        lines = [line.strip() for line in obj.summary.splitlines() if line.strip()]
        text = "; ".join(lines[:2])
        if len(text) > 160:
            text = text[:157] + "..."
        return text + (f" (+{len(lines) - 2} more)" if len(lines) > 2 else "")


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "home_label", "order", "show_in_filter", "home_order", "show_on_home"]
    list_editable = ["order", "show_in_filter", "home_order", "show_on_home"]
    prepopulated_fields = {"slug": ["name"]}


class FactInline(admin.TabularInline):
    model = ProjectFact
    extra = 1
    fields = ["order", "label", "value"]


class MediaInline(admin.StackedInline):
    model = ProjectMedia
    extra = 0
    readonly_fields = ["preview", "before_preview", "source"]
    fieldsets = [
        (None, {"fields": [("order", "kind"), ("sheet_no", "title"), ("image", "preview"), "alt", "placeholder_glyph",
                           "video", ("in_gallery", "in_pile"), "source"]}),
        ("Before / after pair (optional)", {
            "classes": ["collapse"],
            "fields": [("before_image", "before_preview"), "before_alt", ("before_label", "after_label"), "before_missing_note"],
        }),
    ]

    @admin.display(description="Preview")
    def preview(self, obj):
        return thumb(obj.image, 120, obj.kind == ProjectMedia.Kind.SHEET)

    @admin.display(description="Preview")
    def before_preview(self, obj):
        return thumb(obj.before_image, 120, obj.kind == ProjectMedia.Kind.SHEET)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ["cover_thumb", "title", "location", "category_list", "media_total", "show_on_home", "stage", "is_published", "order"]
    list_display_links = ["cover_thumb", "title"]
    list_editable = ["show_on_home", "is_published", "order"]
    list_filter = ["is_published", "show_on_home", "stage", "categories"]
    search_fields = ["title", "subtitle", "location", "description"]
    prepopulated_fields = {"slug": ["title"]}
    filter_horizontal = ["categories"]
    form = ProjectForm
    inlines = [FactInline, MediaInline]
    save_on_top = True
    fieldsets = [
        (None, {"fields": ["title", "slug", "categories", "subtitle", "location", "description"]}),
        ("Add sheets, renders and videos", {"fields": ["add_files", "add_as"]}),
        ("Where it shows", {"fields": [("is_published", "show_on_home", "order"), "cover"]}),
        ("Running / built strip on the homepage", {
            "classes": ["collapse"],
            "fields": ["stage", ("stage_label", "stage_date"), "card_text"],
        }),
        ("Search engines", {"classes": ["collapse"], "fields": ["seo_title", "seo_description"]}),
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("categories", "media").select_related("cover")

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        files = form.cleaned_data.get("add_files") or []
        if not files:
            return
        from .importer import Report, ingest_uploads

        report = Report()
        counts = ingest_uploads(form.instance, files, form.cleaned_data.get("add_as") or "auto", report)
        self.message_user(
            request,
            f"Added {counts['sheet']} sheets, {counts['render']} renders, {counts['video']} videos, "
            f"{counts['pairs']} before/after pairs. Check each new sheet for names, addresses and stamps.",
            messages.SUCCESS,
        )
        for w in report.warnings:
            self.message_user(request, w, messages.WARNING)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # the cover can only be one of this project's own images
        if db_field.name == "cover":
            pk = request.resolver_match.kwargs.get("object_id")
            kwargs["queryset"] = ProjectMedia.objects.filter(project_id=pk) if pk else ProjectMedia.objects.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description="")
    def cover_thumb(self, obj):
        m = obj.cover_media()
        return thumb(m.image if m else None, 56, m.kind == ProjectMedia.Kind.SHEET if m else True)

    @admin.display(description="Categories")
    def category_list(self, obj):
        return ", ".join(c.name for c in obj.categories.all())

    @admin.display(description="Images")
    def media_total(self, obj):
        return len(obj.media.all())

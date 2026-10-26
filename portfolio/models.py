"""Projects shown on the website: Category -> Project -> media (sheets, renders, before/after pairs, videos)."""

from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.urls import reverse
from django.utils.text import slugify


class Category(models.Model):
    """A kind of project, e.g. "Home modification" or "Structures".

    The same category drives the filter chips on /projects and the tabs of the
    "Best work" drawing pile on the homepage.
    """

    slug = models.SlugField(unique=True, help_text="Used in links, e.g. /projects?category=structures")
    name = models.CharField(max_length=80, help_text='Filter label on the Projects page, e.g. "Home modification"')
    home_label = models.CharField(
        max_length=40,
        blank=True,
        help_text='Tab label in the homepage "Best work" pile, e.g. "HOUSE MODIFICATION". Empty uses the name.',
    )
    intro = models.TextField(
        blank=True,
        help_text="One or two sentences shown above the filtered list; also used as the search-engine description.",
    )
    order = models.PositiveIntegerField(default=0, help_text="Position among the Projects page filters")
    home_order = models.PositiveIntegerField(default=0, help_text="Position among the homepage tabs")
    show_in_filter = models.BooleanField(default=True, help_text="Show as a filter chip on the Projects page")
    show_on_home = models.BooleanField(default=False, help_text='Show as a tab in the homepage "Best work" pile')

    class Meta:
        ordering = ["order", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name

    @property
    def tab_label(self):
        return self.home_label or self.name.upper()


class PublishedQuerySet(models.QuerySet):
    def published(self):
        return self.filter(is_published=True)


class Project(models.Model):
    class Stage(models.TextChoices):
        NONE = "", "Not shown in the running / built strips"
        RUNNING = "running", "Running (being drawn now)"
        BUILT = "built", "Built (finished building)"

    title = models.CharField(max_length=120)
    slug = models.SlugField(unique=True, max_length=140, help_text="Web address: /projects/<slug>")
    categories = models.ManyToManyField(Category, related_name="projects")
    subtitle = models.CharField(max_length=200, help_text="One line under the title, e.g. \"New waterfront home, exteriors and interiors\"")
    location = models.CharField(max_length=120, blank=True, help_text='e.g. "Exuma, Bahamas". Leave out street addresses.')
    description = models.TextField(
        blank=True,
        help_text="A few paragraphs for the project's own page: the brief, what we drew, what came out of plan check.",
    )

    # homepage placement
    is_published = models.BooleanField(default=True, help_text="Untick to hide the project everywhere")
    show_on_home = models.BooleanField(default=False, help_text='Include in the homepage "Best work" pile under its categories')
    order = models.PositiveIntegerField(default=0, help_text="Lower numbers come first")

    # "Running" / "Built" strips on the homepage
    stage = models.CharField(max_length=10, choices=Stage, blank=True, default=Stage.NONE)
    stage_label = models.CharField(max_length=40, blank=True, help_text='Card label, e.g. "PERMIT SET" or "MEP SHEETS"')
    stage_date = models.CharField(max_length=40, blank=True, help_text='e.g. "OCT 2026"')
    card_text = models.CharField(max_length=200, blank=True, help_text="One sentence for the running / built card")
    cover = models.ForeignKey(
        "ProjectMedia",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Image used on cards. Empty uses the first image.",
    )

    # search engines
    seo_title = models.CharField(max_length=70, blank=True, help_text="Browser tab / Google title. Empty uses the project title.")
    seo_description = models.CharField(max_length=160, blank=True, help_text="Google snippet. Empty uses the subtitle.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = PublishedQuerySet.as_manager()

    class Meta:
        ordering = ["order", "-created_at"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)[:140]
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return f"{settings.FRONTEND_URL}/projects/{self.slug}"

    def cover_media(self):
        """The card picture: the one chosen in the admin, else the first render, else the first sheet."""
        if self.cover_id:
            return self.cover
        media = [m for m in self.media.all() if m.image]
        return next((m for m in media if m.kind == ProjectMedia.Kind.RENDER), None) or next(
            (m for m in media if m.kind != ProjectMedia.Kind.VIDEO), None
        ) or (media[0] if media else None)

    def reorder_media(self) -> bool:
        """Put the strongest views first: before/after pairs, renders and photos, videos, then sheets
        (placeholders last). The order within each group is kept, so admin tweaks there survive.
        Called by the importer whenever it adds files, so renders that arrive after the PDF still lead."""
        from config.revalidate import revalidate

        def group(m):
            if not m.image and not m.video:
                return 9  # placeholder sketch waiting for the real sheet
            if m.kind == ProjectMedia.Kind.RENDER:
                return 0 if m.before_image else 1
            return 2 if m.kind == ProjectMedia.Kind.VIDEO else 3

        items = sorted(self.media.all(), key=lambda m: (group(m), m.order, m.id))
        changed = {m.pk: m for i, m in enumerate(items, start=1) if m.order != i * 10}
        for i, m in enumerate(items, start=1):
            m.order = i * 10
        # the homepage pile takes the first pictures in this order (imported files only: piles
        # picked by hand in the admin stay as they are)
        def site_photo(m):  # from a "Site photos" folder: shown in the viewer, kept out of the pile
            folders = m.source.split("!/")[0].lower().split("/")[:-1]
            return any("photo" in f and "before" not in f for f in folders)

        imported = [m for m in items if m.source and m.image and m.kind != ProjectMedia.Kind.VIDEO and not site_photo(m)]
        if imported:
            for k, m in enumerate(imported):
                if m.in_pile != (k < self.PILE_SIZE):
                    m.in_pile = k < self.PILE_SIZE
                    changed[m.pk] = m
        if changed:
            ProjectMedia.objects.bulk_update(list(changed.values()), ["order", "in_pile"])
            revalidate("projects", f"project:{self.slug}")
        return bool(changed)

    PILE_SIZE = 10  # the homepage pile has poses for up to 11 sheets


class ProjectFact(models.Model):
    """A label/value row in the project's title block, e.g. LOCATION / Santa Ana, California."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="facts")
    label = models.CharField(max_length=30, help_text='e.g. "SCOPE", "CODES", "DELIVERED"')
    value = models.CharField(max_length=200)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.label}: {self.value}"


def media_upload_to(instance, filename):
    return f"projects/{instance.project.slug}/{filename}"


class ProjectMedia(models.Model):
    """One drawing sheet, render, photo or video of a project.

    Fill "Before image" to turn it into a before/after pair with a drag slider.
    Leave "Image" empty to show a drawn placeholder until the real sheet arrives.
    For a video, "Image" is its poster (made from the video when left empty).
    """

    class Kind(models.TextChoices):
        SHEET = "sheet", "Drawing sheet (white paper, shown whole)"
        RENDER = "render", "Render or photo (fills the frame)"
        VIDEO = "video", "Video (walkthrough or animation)"

    class Glyph(models.TextChoices):
        PLAN = "plan", "Floor plan"
        SECTION = "section", "Section"
        TOWER = "tower", "Telecom tower"
        PLATE = "plate", "Connection plate"
        FOOTING = "footing", "Footing"
        ARRAY = "array", "Solar array"
        FRAME = "frame", "Solar frame"
        PIER = "pier", "Pier"
        CONTAINER = "container", "Container home"
        FLOAT = "float", "Floating house"
        GLASS = "glass", "Greenhouse"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="media")
    order = models.PositiveIntegerField(default=0)
    kind = models.CharField(max_length=10, choices=Kind, default=Kind.SHEET)
    sheet_no = models.CharField(max_length=12, blank=True, help_text='Sheet number, e.g. "A101". Renders use R01, R02...')
    title = models.CharField(max_length=80, help_text='e.g. "Proposed first floor" or "Pool deck"')

    image = models.ImageField(
        upload_to=media_upload_to,
        blank=True,
        width_field="width",
        height_field="height",
        help_text="JPG or PNG, about 2000 px on the long side. Remove addresses and other firms' names first.",
    )
    width = models.PositiveIntegerField(null=True, blank=True, editable=False)
    height = models.PositiveIntegerField(null=True, blank=True, editable=False)
    alt = models.CharField(max_length=200, help_text="What the image shows, for blind visitors and Google")
    video = models.FileField(
        upload_to=media_upload_to,
        blank=True,
        validators=[FileExtensionValidator(["mp4", "webm"])],
        help_text="Videos only: MP4 (H.264), ideally under 100 MB. Big render exports: use the import command, which compresses them.",
    )
    placeholder_glyph = models.CharField(
        max_length=12,
        choices=Glyph,
        blank=True,
        help_text="Only used while Image is empty: the sketch drawn on the placeholder sheet",
    )

    # before / after pair
    before_image = models.ImageField(
        upload_to=media_upload_to,
        blank=True,
        width_field="before_width",
        height_field="before_height",
        help_text="Optional: the existing condition or client sketch, shown under a drag slider",
    )
    before_width = models.PositiveIntegerField(null=True, blank=True, editable=False)
    before_height = models.PositiveIntegerField(null=True, blank=True, editable=False)
    before_alt = models.CharField(max_length=200, blank=True)
    before_label = models.CharField(max_length=30, blank=True, help_text='e.g. "EXISTING" or "CLIENT SKETCH"')
    after_label = models.CharField(max_length=30, blank=True, help_text='e.g. "PROPOSED" or "SHEET A103"')
    before_missing_note = models.CharField(
        max_length=120,
        blank=True,
        help_text="Pair without a before image yet: note shown on the empty side",
    )

    # where it appears
    in_gallery = models.BooleanField(default=True, help_text="Show on the project page and the Projects list")
    in_pile = models.BooleanField(default=True, help_text='Show in the homepage "Best work" drawing pile')
    source = models.CharField(
        max_length=300, blank=True, editable=False,
        help_text="File this came from in an import (re-running the import updates it instead of adding a copy)",
    )

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "sheet or image"
        verbose_name_plural = "sheets and images"

    def __str__(self):
        return " ".join(x for x in [self.sheet_no, self.title] if x)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # a video without a poster gets one from its own frames
        if self.kind == self.Kind.VIDEO and self.video and not self.image:
            self.make_poster()

    def make_poster(self):
        import logging
        import shutil
        import tempfile

        from django.core.files.base import ContentFile

        from .media_tools import video_poster

        try:
            try:
                jpeg = video_poster(Path(self.video.path))
            except NotImplementedError:  # remote storage: work on a local copy
                with tempfile.NamedTemporaryFile(suffix=Path(self.video.name).suffix, delete=False) as tmp:
                    with self.video.open("rb") as f:
                        shutil.copyfileobj(f, tmp)
                try:
                    jpeg = video_poster(Path(tmp.name))
                finally:
                    Path(tmp.name).unlink(missing_ok=True)
        except Exception as exc:  # never block a save over the poster
            logging.getLogger("safe").warning("no poster for %s: %s", self.video.name, exc)
            return
        self.image.save(Path(self.video.name).stem + "-poster.jpg", ContentFile(jpeg), save=False)
        super().save(update_fields=["image", "width", "height"])

    @property
    def is_pair(self):
        """Shown as a before/after slider only once the "before" image is uploaded."""
        return bool(self.before_image)

    def clean(self):
        if self.kind == self.Kind.VIDEO:
            if not self.video:
                raise ValidationError({"video": "Upload the video file (MP4)."})
            if self.in_pile:
                raise ValidationError({"in_pile": "Videos can't go in the homepage drawing pile; untick it."})
            return
        if not self.image and not self.placeholder_glyph:
            raise ValidationError({"image": "Upload an image, or pick a placeholder sketch until the image arrives."})
        if self.before_image and not self.before_alt:
            raise ValidationError({"before_alt": "Describe the before image too."})


class DriveImport(models.Model):
    """What one Drive sync loaded, or why it failed. Kept in the admin, so nothing depends on email."""

    created_at = models.DateTimeField(auto_now_add=True)
    summary = models.TextField()
    failed = models.BooleanField(default=False)
    checked = models.BooleanField(default=False, help_text="Tick once you've looked at what arrived.")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Drive import"
        verbose_name_plural = "Drive imports"

    def __str__(self):
        when = f"{self.created_at:%Y-%m-%d %H:%M}" if self.created_at else "new"
        return f"{when} ({'failed' if self.failed else 'loaded'})"

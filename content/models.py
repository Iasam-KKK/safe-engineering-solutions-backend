"""Site-wide content edited in the admin: contact details, partners, reviews and the team."""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class SiteSettings(models.Model):
    """One row of company-wide details. Edit it under Content > Site settings."""

    company_name = models.CharField(max_length=80, default="SAFE Engineering Solutions")
    email = models.EmailField(default="saifulhaq_kkk@yahoo.com")
    whatsapp_number = models.CharField(
        max_length=20, default="923329866561", help_text="Digits only with country code, used for wa.me links"
    )
    linkedin_url = models.URLField(blank=True, default="https://www.linkedin.com/company/safe-engineering-solutions2/")
    instagram_url = models.URLField(
        blank=True, help_text="The company's Instagram page. Empty: the Instagram button is left off the About page."
    )
    fiverr_url = models.URLField(blank=True)

    booking_url = models.URLField(
        blank=True,
        help_text="Calendly (or similar) link for video consultations. Empty: the button opens an email instead.",
    )
    consultation_length = models.CharField(max_length=40, blank=True, help_text='e.g. "30 minutes"')

    # California stamping
    stamping_engineer = models.CharField(max_length=120, blank=True, help_text='e.g. "Jane Doe, PE"')
    stamping_license = models.CharField(max_length=60, blank=True, help_text="California license number")

    # review summary
    rating_value = models.DecimalField(max_digits=2, decimal_places=1, null=True, blank=True, help_text="e.g. 4.9")
    rating_count = models.PositiveIntegerField(null=True, blank=True, help_text="Number of reviews behind the rating")
    rating_source = models.CharField(max_length=40, blank=True, default="Fiverr")
    rating_as_of = models.DateField(null=True, blank=True, help_text="Date you last checked the rating")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "site settings"
        verbose_name_plural = "site settings"

    def __str__(self):
        return "Site settings"

    def save(self, *args, **kwargs):
        self.pk = 1  # always one row
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Partner(models.Model):
    """A firm that keeps sending work. Shown in the homepage partner ticker (with their permission)."""

    name = models.CharField(max_length=120)
    logo = models.ImageField(
        upload_to="partners/",
        blank=True,
        help_text="Any logo file: it is shown as a one-colour mark on the site, whatever its background.",
    )
    website = models.URLField(blank=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # a logo uploaded in the admin becomes the same one-colour mark as the ones from the Drive
        from portfolio.media_tools import MARK_NAME, MARK_VERSION, logo_mark

        if self.logo and not MARK_NAME.search(self.logo.name):
            from django.core.files.base import ContentFile
            from django.utils.text import slugify
            from PIL import Image

            try:
                with Image.open(self.logo) as img:
                    data = logo_mark(img)
                self.logo.save(f"{slugify(self.name)[:60] or 'logo'}-mark{MARK_VERSION}.png", ContentFile(data), save=False)
            except Exception:  # an odd file stays as uploaded rather than blocking the save
                pass
        super().save(*args, **kwargs)


class Review(models.Model):
    """A client review copied word for word, e.g. from Fiverr. Show initials unless the client agreed to their name."""

    author = models.CharField(max_length=80, help_text='e.g. "K. P." or the full name if the client agreed')
    country = models.CharField(max_length=60, blank=True)
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)], default=5)
    text = models.TextField(help_text="Exactly as written; trim with … only")
    source = models.CharField(max_length=40, default="Fiverr")
    source_url = models.URLField(blank=True)
    date = models.DateField(null=True, blank=True)
    project = models.ForeignKey(
        "portfolio.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="reviews"
    )
    is_featured = models.BooleanField(default=True, help_text="Show on the homepage")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "-date"]

    def __str__(self):
        return f"{self.author} ({self.rating}★)"


class TeamMember(models.Model):
    name = models.CharField(max_length=120)
    role = models.CharField(max_length=120)
    credentials = models.CharField(max_length=200, blank=True, help_text='e.g. "PE, California" or "B.Sc. Civil Engineering"')
    bio = models.TextField(blank=True)
    photo = models.ImageField(upload_to="team/", blank=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name

"""Requests sent from the website: quotes, video consultations and stamping reviews."""

import uuid

from django.db import models

from .storage import private_storage


class Inquiry(models.Model):
    class Kind(models.TextChoices):
        QUOTE = "quote", "Quote request"
        CONSULTATION = "consultation", "Video consultation"
        STAMPING = "stamping", "Stamping review (California)"

    class Role(models.TextChoices):
        HOMEOWNER = "homeowner", "Homeowner"
        BUILDER = "builder", "Builder or contractor"
        FIRM = "firm", "Architect or design firm"
        DEVELOPER = "developer", "Developer"

    class ProjectType(models.TextChoices):
        MODIFICATION = "modification", "Existing home extension or modification"
        NEW_RESIDENTIAL = "new_residential", "New residential construction"
        NEW_COMMERCIAL = "new_commercial", "New commercial construction"
        ADU_GARAGE = "adu_garage", "ADU or garage"
        LAND = "land", "Land or site planning, lot subdivision"
        STORM = "storm", "Storm or drainage design"
        STRUCTURES = "structures", "Structures (steel, telecom, solar)"
        MODULAR = "modular", "Modular home"
        MEP = "mep", "MEP sheets only"
        RENDERS = "renders", "Renders or walkthrough only"
        OTHER = "other", "Something else"

    class Timing(models.TextChoices):
        DEADLINE = "active", "Has a deadline"
        PLANNING = "planning", "Planning ahead"

    class Status(models.TextChoices):
        NEW = "new", "New"
        REPLIED = "replied", "Replied"
        QUOTED = "quoted", "Quoted"
        WON = "won", "Won"
        CLOSED = "closed", "Closed"

    kind = models.CharField(max_length=20, choices=Kind, default=Kind.QUOTE)
    name = models.CharField(max_length=120)
    email = models.EmailField()
    phone = models.CharField(max_length=40, blank=True)
    role = models.CharField(max_length=20, choices=Role, blank=True)
    project_type = models.CharField(max_length=20, choices=ProjectType, blank=True)
    location = models.CharField(max_length=120, blank=True, help_text="City and state")
    timing = models.CharField(max_length=20, choices=Timing, blank=True)
    details = models.TextField(blank=True)
    source_page = models.CharField(max_length=200, blank=True, help_text="Page the request was sent from")

    status = models.CharField(max_length=20, choices=Status, default=Status.NEW)
    internal_notes = models.TextField(blank=True, help_text="Only visible in the admin")
    ip_address = models.GenericIPAddressField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "inquiries"

    def __str__(self):
        return f"{self.get_kind_display()} from {self.name}"


def inquiry_upload_to(instance, filename):
    # random folder so uploaded file names can't be guessed from the outside
    return f"inquiries/{instance.inquiry.created_at:%Y/%m}/{uuid.uuid4().hex[:12]}/{filename}"


class InquiryFile(models.Model):
    inquiry = models.ForeignKey(Inquiry, on_delete=models.CASCADE, related_name="files")
    file = models.FileField(upload_to=inquiry_upload_to, storage=private_storage)
    original_name = models.CharField(max_length=255)
    size = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.original_name

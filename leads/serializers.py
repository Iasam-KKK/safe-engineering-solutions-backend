import os

from django.conf import settings
from rest_framework import serializers

from .models import Inquiry

ALLOWED_EXTENSIONS = {".pdf", ".dwg", ".dxf", ".jpg", ".jpeg", ".png", ".heic", ".webp", ".zip", ".rvt"}


class InquiryCreateSerializer(serializers.ModelSerializer):
    files = serializers.ListField(
        child=serializers.FileField(allow_empty_file=False), required=False, write_only=True
    )
    # Honeypot: real visitors never see or fill this field; bots that fill it are dropped quietly.
    website = serializers.CharField(required=False, allow_blank=True, write_only=True)

    class Meta:
        model = Inquiry
        fields = [
            "kind", "name", "email", "phone", "role", "project_type", "location", "timing", "details",
            "source_page", "files", "website",
        ]
        extra_kwargs = {"name": {"required": True}, "email": {"required": True}}

    def validate_files(self, files):
        if len(files) > settings.INQUIRY_MAX_FILES:
            raise serializers.ValidationError(f"Attach up to {settings.INQUIRY_MAX_FILES} files.")
        limit = settings.INQUIRY_MAX_FILE_MB * 1024 * 1024
        for f in files:
            ext = os.path.splitext(f.name)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise serializers.ValidationError(f"{f.name}: send PDF, DWG, photos or a ZIP.")
            if f.size > limit:
                raise serializers.ValidationError(f"{f.name} is over {settings.INQUIRY_MAX_FILE_MB} MB.")
        return files

    def validate(self, attrs):
        if attrs.get("kind") != Inquiry.Kind.CONSULTATION and not attrs.get("details") and not attrs.get("files"):
            raise serializers.ValidationError({"details": "Tell us a little about the project."})
        return attrs

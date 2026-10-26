import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.urls import reverse
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import Inquiry, InquiryFile
from .serializers import InquiryCreateSerializer

log = logging.getLogger("safe.leads")


def notify(inquiry, admin_url):
    """Email the new inquiry to everyone in INQUIRY_NOTIFY_EMAILS."""
    if not settings.INQUIRY_NOTIFY_EMAILS:
        return
    lines = [
        f"{inquiry.get_kind_display()} from {inquiry.name} <{inquiry.email}>",
        "",
        f"I am a:      {inquiry.get_role_display() or '-'}",
        f"Project:     {inquiry.get_project_type_display() or '-'}",
        f"Location:    {inquiry.location or '-'}",
        f"Timing:      {inquiry.get_timing_display() or '-'}",
        f"Phone:       {inquiry.phone or '-'}",
        f"Sent from:   {inquiry.source_page or '-'}",
        f"Files:       {inquiry.files.count()}",
        "",
        inquiry.details or "(no details)",
        "",
        f"Open in the admin: {admin_url}",
    ]
    msg = EmailMessage(
        subject=f"[SAFE website] {inquiry.get_kind_display()}: {inquiry.name}",
        body="\n".join(lines),
        to=settings.INQUIRY_NOTIFY_EMAILS,
        reply_to=[inquiry.email],
    )
    try:
        msg.send()
    except Exception as exc:  # the inquiry is saved either way
        log.warning("inquiry %s email failed: %s", inquiry.pk, exc)


class InquiryCreate(APIView):
    """POST /api/inquiries/ — quote, consultation or stamping request from the website (multipart for files)."""

    parser_classes = [MultiPartParser, FormParser, JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "inquiries"

    def post(self, request):
        # multipart: repeated "files" parts arrive as a list (DRF ListField reads getlist)
        ser = InquiryCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        files = ser.validated_data.pop("files", [])
        honeypot = ser.validated_data.pop("website", "")
        if honeypot:
            log.info("honeypot filled, inquiry dropped")
            return Response({"ok": True}, status=status.HTTP_201_CREATED)

        with transaction.atomic():
            inquiry = Inquiry.objects.create(**ser.validated_data, ip_address=self._ip(request))
            for f in files:
                InquiryFile.objects.create(inquiry=inquiry, file=f, original_name=f.name[:255], size=f.size)

        admin_url = request.build_absolute_uri(reverse("admin:leads_inquiry_change", args=[inquiry.pk]))
        transaction.on_commit(lambda: notify(inquiry, admin_url))
        return Response({"ok": True, "id": inquiry.pk}, status=status.HTTP_201_CREATED)

    @staticmethod
    def _ip(request):
        # REMOTE_ADDR is the visitor unless a proxy sits in front; DRF's NUM_PROXIES handles that for throttling
        return request.META.get("REMOTE_ADDR") or None

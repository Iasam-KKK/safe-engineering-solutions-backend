import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from content.models import SiteSettings

from .booking import call_invite, in_zone, open_slots
from .models import Inquiry, InquiryFile
from .serializers import InquiryCreateSerializer

log = logging.getLogger("safe.leads")


def notify(inquiry, admin_url):
    """Email the new inquiry to everyone in INQUIRY_NOTIFY_EMAILS."""
    if not settings.INQUIRY_NOTIFY_EMAILS:
        return
    site = SiteSettings.load()
    when = []
    if inquiry.scheduled_at:
        when = [
            f"Video call:  {in_zone(inquiry.scheduled_at, site.booking_timezone)} (your time)",
            f"             {in_zone(inquiry.scheduled_at, inquiry.client_timezone)} ({inquiry.client_timezone or 'UTC'}, their time)",
            f"Length:      {inquiry.call_minutes} minutes",
            "",
        ]
    lines = [
        f"{inquiry.get_kind_display()} from {inquiry.name} <{inquiry.email}>",
        "",
        *when,
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
    subject = f"[SAFE website] {inquiry.get_kind_display()}: {inquiry.name}"
    if inquiry.scheduled_at:
        subject += f", {in_zone(inquiry.scheduled_at, site.booking_timezone)}"
    msg = EmailMessage(subject=subject, body="\n".join(lines), to=settings.INQUIRY_NOTIFY_EMAILS, reply_to=[inquiry.email])
    if inquiry.scheduled_at:
        title = f"Video call: {inquiry.name}"
        about = f"{inquiry.email} {inquiry.phone}".strip() + f"\n{inquiry.details}\n{admin_url}"
        msg.attach("video-call.ics", call_invite(inquiry, title, about, site.meeting_link), "text/calendar")
    try:
        msg.send()
    except Exception as exc:  # the inquiry is saved either way
        log.warning("inquiry %s email failed: %s", inquiry.pk, exc)


def confirm_call(inquiry):
    """The client's confirmation, with a calendar invite. Holds nothing they typed: the address isn't verified yet,
    so a stranger's text must not be sendable to a third party through this form."""
    if not settings.EMAIL_CONFIGURED or not inquiry.scheduled_at:
        return
    site = SiteSettings.load()
    video = f"Join the call: {site.meeting_link}" if site.meeting_link else "We will email you the video link before the call."
    lines = [
        "Hello,",
        "",
        f"Your video call with {site.company_name} is booked:",
        "",
        f"    {in_zone(inquiry.scheduled_at, inquiry.client_timezone)} ({inquiry.client_timezone or 'UTC'})",
        f"    {inquiry.call_minutes} minutes",
        "",
        video,
        "",
        "Have your sketches, photos or city comments to hand. The attached invite adds the call to your calendar.",
        "",
        f"Need to change it? Reply to this email, or write to {site.email}.",
        "",
        site.company_name,
    ]
    msg = EmailMessage(
        subject=f"Your video call with {site.company_name}: {in_zone(inquiry.scheduled_at, inquiry.client_timezone)}",
        body="\n".join(lines),
        to=[inquiry.email],
        reply_to=[site.email],
    )
    about = video + f"\nNeed to change it? Write to {site.email}."
    msg.attach("video-call.ics", call_invite(inquiry, f"Video call with {site.company_name}", about, site.meeting_link), "text/calendar")
    try:
        msg.send()
    except Exception as exc:  # the booking stands either way
        log.warning("inquiry %s confirmation failed: %s", inquiry.pk, exc)


class CallSlots(APIView):
    """GET /api/booking/slots/ — the times a video call can be booked, as UTC instants, with the length of a call.
    The browser shows them in the visitor's own time zone; the firm's time zone isn't part of the answer."""

    def get(self, request):
        slots = [s.strftime("%Y-%m-%dT%H:%M:%SZ") for s in open_slots()]
        res = Response({"minutes": SiteSettings.load().booking_slot_minutes, "slots": slots})
        res["Cache-Control"] = "no-store"  # a time booked a moment ago must not be offered again
        return res


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

        site = SiteSettings.load()
        fields = ser.validated_data
        if fields.get("scheduled_at"):
            fields["call_minutes"] = site.booking_slot_minutes
        try:
            with transaction.atomic():
                inquiry = Inquiry.objects.create(**fields, ip_address=self._ip(request))
                for f in files:
                    InquiryFile.objects.create(inquiry=inquiry, file=f, original_name=f.name[:255], size=f.size)
        except IntegrityError:
            # someone else booked this time between the check and the save
            return Response(
                {"scheduled_at": "That time was just taken. Please pick another one."}, status=status.HTTP_409_CONFLICT
            )

        admin_url = request.build_absolute_uri(reverse("admin:leads_inquiry_change", args=[inquiry.pk]))
        transaction.on_commit(lambda: notify(inquiry, admin_url))
        transaction.on_commit(lambda: confirm_call(inquiry))
        answer = {"ok": True, "id": inquiry.pk}
        if inquiry.scheduled_at:
            answer["call"] = {"minutes": inquiry.call_minutes, "video_link": site.meeting_link}
        return Response(answer, status=status.HTTP_201_CREATED)

    @staticmethod
    def _ip(request):
        # REMOTE_ADDR is the visitor unless a proxy sits in front; DRF's NUM_PROXIES handles that for throttling
        return request.META.get("REMOTE_ADDR") or None

"""Video-call bookings: which times are open, and the calendar invite.

Hours are kept in the firm's own time zone (Content > Site settings), because that is how Saif thinks of them. Times
leave this module as UTC instants, so the browser can show them in the visitor's zone and daylight saving on either
side takes care of itself.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from django.db.models import Q
from django.utils import timezone

from content.models import SiteSettings

from .models import Inquiry


def is_real_timezone(name: str) -> bool:
    return name in available_timezones()


def in_zone(moment: datetime, zone_name: str) -> str:
    """'Tue 4 Nov 2026, 7:30 PM' in that time zone (UTC when the zone is unknown or empty)."""
    try:
        zone = ZoneInfo(zone_name) if zone_name else UTC
    except (ZoneInfoNotFoundError, ValueError):
        zone = UTC
    local = moment.astimezone(zone)
    hour = local.strftime("%I").lstrip("0")
    return f"{local:%a} {local.day} {local:%b %Y}, {hour}:{local:%M %p}"


def _booked(after: datetime, before: datetime) -> list[tuple[datetime, datetime]]:
    """Times already taken. A closed request gives its time back."""
    taken = Inquiry.objects.filter(
        ~Q(status=Inquiry.Status.CLOSED), scheduled_at__isnull=False, scheduled_at__gte=after, scheduled_at__lt=before
    ).values_list("scheduled_at", "call_minutes")
    return [(at, at + timedelta(minutes=minutes or 30)) for at, minutes in taken]


def open_slots(now: datetime | None = None) -> list[datetime]:
    """Every time a call can start, soonest first, as UTC datetimes."""
    site = SiteSettings.load()
    try:
        zone = ZoneInfo(site.booking_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return []
    now = now or timezone.now()
    step = timedelta(minutes=site.booking_slot_minutes)
    earliest = now + timedelta(hours=site.booking_notice_hours)
    today = now.astimezone(zone).date()
    last_day = today + timedelta(days=site.booking_days_ahead)

    hours: dict[int, list] = {}
    for window in site.call_hours.all():
        hours.setdefault(window.weekday, []).append(window)
    days_off = [(off.first_day, off.last_day or off.first_day) for off in site.call_days_off.all()]
    booked = _booked(now - timedelta(days=1), now + timedelta(days=site.booking_days_ahead + 2))

    found: set[datetime] = set()
    # from yesterday: its hours may run past midnight into today
    for offset in range(-1, site.booking_days_ahead + 1):
        day = today + timedelta(days=offset)
        if day > last_day or any(first <= day <= last for first, last in days_off):
            continue
        for window in hours.get(day.weekday(), []):
            start = datetime.combine(day, window.start, tzinfo=zone).astimezone(UTC)
            end_day = day if window.end > window.start else day + timedelta(days=1)
            end = datetime.combine(end_day, window.end, tzinfo=zone).astimezone(UTC)
            at = start
            while at + step <= end:
                free = all(at + step <= b_from or at >= b_to for b_from, b_to in booked)
                if at >= earliest and free:
                    found.add(at)
                at += step
    return sorted(found)


def is_open_slot(moment: datetime) -> bool:
    moment = moment.astimezone(UTC)
    return moment in open_slots()


def _ics_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Calendar files keep lines to 75 bytes; longer ones continue on the next line after a space."""
    out: list[str] = []
    while len(line.encode()) > 75:
        cut = 74
        while len(line[:cut].encode()) > 74:
            cut -= 1
        out.append(line[:cut])
        line = " " + line[cut:]
    out.append(line)
    return "\r\n".join(out)


def call_invite(inquiry: Inquiry, title: str, description: str, video_link: str = "") -> str:
    """An .ics calendar event. In UTC, so it lands right in any calendar and names no time zone of ours."""
    start = inquiry.scheduled_at.astimezone(UTC)
    end = start + timedelta(minutes=inquiry.call_minutes or 30)
    stamp = (inquiry.created_at or timezone.now()).astimezone(UTC)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//SAFE Engineering Solutions//Website//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:video-call-{inquiry.pk}@safe-engineering-solutions",
        f"DTSTAMP:{stamp:%Y%m%dT%H%M%SZ}",
        f"DTSTART:{start:%Y%m%dT%H%M%SZ}",
        f"DTEND:{end:%Y%m%dT%H%M%SZ}",
        f"SUMMARY:{_ics_text(title)}",
        f"DESCRIPTION:{_ics_text(description)}",
    ]
    if video_link:
        lines += [f"LOCATION:{_ics_text(video_link)}", f"URL:{video_link}"]
    lines += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Video call", "TRIGGER:-PT15M", "END:VALARM", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"

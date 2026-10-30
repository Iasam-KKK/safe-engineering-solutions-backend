import shutil
import tempfile
from datetime import UTC, date, datetime, time
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from content.models import SiteSettings, VideoCallDayOff, VideoCallHours
from leads.booking import call_invite, open_slots
from leads.models import Inquiry

TMP = tempfile.mkdtemp()


@override_settings(PRIVATE_MEDIA_ROOT=TMP, INQUIRY_NOTIFY_EMAILS=["saif@example.com"])
class InquiryTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        from django.core.cache import cache

        cache.clear()  # throttle counters

    def post(self, **extra):
        data = {
            "kind": "quote", "name": "Kim P", "email": "kim@example.com", "role": "homeowner",
            "project_type": "modification", "location": "Fairfax, VA", "timing": "active",
            "details": "Porch and a link to the garage", "source_page": "/contact",
        }
        data.update(extra)
        return self.client.post("/api/inquiries/", data)

    def test_quote_with_files_and_email(self):
        files = [SimpleUploadedFile("sketch.pdf", b"%PDF-1.4 test"), SimpleUploadedFile("photo.jpg", b"jpgdata")]
        with self.captureOnCommitCallbacks(execute=True):
            res = self.post(files=files)
        self.assertEqual(res.status_code, 201, res.content)
        inq = Inquiry.objects.get()
        self.assertEqual(inq.files.count(), 2)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Kim P", mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].reply_to, ["kim@example.com"])
        # stored outside MEDIA_ROOT, no public URL
        self.assertTrue(inq.files.first().file.path.startswith(TMP))

    def test_bad_file_type(self):
        res = self.post(files=[SimpleUploadedFile("run.exe", b"MZ")])
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Inquiry.objects.count(), 0)

    def test_honeypot(self):
        res = self.post(website="http://spam")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(Inquiry.objects.count(), 0)

    def test_quote_needs_details(self):
        self.assertEqual(self.post(details="").status_code, 400)
        self.assertEqual(self.post(kind="consultation", details="").status_code, 201)

    def test_file_download_is_staff_only(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.post(files=[SimpleUploadedFile("plan.pdf", b"%PDF-1.4")])
        f = Inquiry.objects.get().files.get()
        url = f"/admin/leads/inquiry/file/{f.pk}/"
        self.assertEqual(self.client.get(url).status_code, 302)  # to the login page
        User.objects.create_superuser("saif", "s@example.com", "pw-123456!")
        self.client.login(username="saif", password="pw-123456!")
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertIn("attachment", res["Content-Disposition"])

    @override_settings(REST_FRAMEWORK={"DEFAULT_THROTTLE_RATES": {"inquiries": "2/hour"}, "DEFAULT_AUTHENTICATION_CLASSES": [], "UNAUTHENTICATED_USER": None})
    def test_throttle(self):
        from rest_framework.settings import api_settings
        from rest_framework.throttling import ScopedRateThrottle

        api_settings.reload()
        ScopedRateThrottle.THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES
        try:
            codes = [self.post().status_code for _ in range(3)]
        finally:
            api_settings.reload()
            ScopedRateThrottle.THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES
        self.assertEqual(codes, [201, 201, 429])


@override_settings(INQUIRY_NOTIFY_EMAILS=["saif@example.com"], EMAIL_CONFIGURED=True)
class BookingTests(TestCase):
    """The calendar on the contact page. The hours are Karachi time (UTC+5, no daylight saving)."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        VideoCallHours.objects.all().delete()
        self.site = SiteSettings.load()
        self.site.booking_timezone = "Asia/Karachi"
        self.site.booking_slot_minutes = 30
        self.site.booking_notice_hours = 12
        self.site.booking_days_ahead = 14
        self.site.save()
        # Monday 2 Nov 2026, 12:00 UTC
        self.now = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
        self.addCleanup(patch.stopall)
        patch("leads.booking.timezone.now", return_value=self.now).start()

    def hours(self, weekday=VideoCallHours.Weekday.TUESDAY, start=time(19, 0), end=time(21, 0)):
        return VideoCallHours.objects.create(settings=self.site, weekday=weekday, start=start, end=end)

    def book(self, at, **extra):
        data = {
            "kind": "consultation", "name": "Kim P", "email": "kim@example.com", "source_page": "/contact",
            "scheduled_at": at, "client_timezone": "America/New_York",
        }
        data.update(extra)
        return self.client.post("/api/inquiries/", data)

    def test_hours_become_utc_slots(self):
        self.hours()
        # Tuesday 3 Nov, 19:00 to 21:00 in Karachi is 14:00 to 16:00 UTC
        self.assertEqual(open_slots(self.now)[:4], [
            datetime(2026, 11, 3, 14, 0, tzinfo=UTC),
            datetime(2026, 11, 3, 14, 30, tzinfo=UTC),
            datetime(2026, 11, 3, 15, 0, tzinfo=UTC),
            datetime(2026, 11, 3, 15, 30, tzinfo=UTC),
        ])

    def test_hours_can_run_past_midnight(self):
        self.hours(start=time(23, 0), end=time(1, 0))
        slots = open_slots(self.now)
        self.assertEqual(slots[0], datetime(2026, 11, 3, 18, 0, tzinfo=UTC))
        self.assertEqual(slots[3], datetime(2026, 11, 3, 19, 30, tzinfo=UTC))
        self.assertNotIn(datetime(2026, 11, 3, 20, 0, tzinfo=UTC), slots)

    def test_notice_and_days_off_and_horizon(self):
        self.hours(weekday=VideoCallHours.Weekday.MONDAY, start=time(10, 0), end=time(23, 0))
        self.hours(weekday=VideoCallHours.Weekday.TUESDAY, start=time(10, 0), end=time(11, 0))
        slots = open_slots(self.now)
        # 12 hours' notice: nothing today (Monday 17:00 in Karachi plus 12 hours is Tuesday 05:00)
        self.assertEqual(slots[0], datetime(2026, 11, 3, 5, 0, tzinfo=UTC))
        VideoCallDayOff.objects.create(settings=self.site, first_day=date(2026, 11, 3))
        self.assertNotIn(datetime(2026, 11, 3, 5, 0, tzinfo=UTC), open_slots(self.now))
        # 14 days ahead: the Monday two weeks on is the last
        self.assertLessEqual(open_slots(self.now)[-1].date(), date(2026, 11, 17))

    def test_slots_api_names_no_time_zone_of_ours(self):
        self.hours()
        res = self.client.get("/api/booking/slots/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Cache-Control"], "no-store")
        body = res.json()
        self.assertEqual(body["minutes"], 30)
        self.assertEqual(body["slots"][0], "2026-11-03T14:00:00Z")
        self.assertNotIn("Karachi", res.content.decode())

    def test_booking_takes_the_time_and_emails_both_sides(self):
        self.hours()
        with self.captureOnCommitCallbacks(execute=True):
            res = self.book("2026-11-03T14:00:00Z", details="Porch")
        self.assertEqual(res.status_code, 201, res.content)
        inq = Inquiry.objects.get()
        self.assertEqual(inq.kind, "consultation")
        self.assertEqual(inq.call_minutes, 30)
        self.assertEqual(inq.client_timezone, "America/New_York")
        self.assertEqual(res.json()["call"]["minutes"], 30)
        self.assertNotIn(datetime(2026, 11, 3, 14, 0, tzinfo=UTC), open_slots(self.now))

        to_saif, to_client = mail.outbox
        self.assertEqual(to_saif.to, ["saif@example.com"])
        self.assertIn("Tue 3 Nov 2026, 7:00 PM", to_saif.subject)  # his own time
        self.assertIn("9:00 AM", to_saif.body)  # theirs
        self.assertEqual(to_client.to, ["kim@example.com"])
        self.assertIn("Tue 3 Nov 2026, 9:00 AM", to_client.subject)
        self.assertNotIn("Karachi", to_client.body)
        self.assertNotIn("Porch", to_client.body)  # nothing they typed goes to an unverified address
        name, ics, mimetype = to_client.attachments[0]
        self.assertEqual((name, mimetype), ("video-call.ics", "text/calendar"))
        self.assertIn("DTSTART:20261103T140000Z", ics)
        self.assertIn("DTEND:20261103T143000Z", ics)
        self.assertNotIn("Karachi", ics)

    def test_no_email_to_the_client_without_a_mail_server(self):
        self.hours()
        with override_settings(EMAIL_CONFIGURED=False), self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.book("2026-11-03T14:00:00Z").status_code, 201)
        self.assertEqual([m.to for m in mail.outbox], [["saif@example.com"]])

    def test_a_taken_time_is_refused_and_closing_frees_it(self):
        self.hours()
        self.assertEqual(self.book("2026-11-03T14:00:00Z").status_code, 201)
        res = self.book("2026-11-03T14:00:00Z", email="other@example.com")
        self.assertEqual(res.status_code, 400)
        self.assertIn("scheduled_at", res.json())
        Inquiry.objects.update(status=Inquiry.Status.CLOSED)
        self.assertEqual(self.book("2026-11-03T14:00:00Z", email="other@example.com").status_code, 201)

    def test_the_database_itself_refuses_a_double_booking(self):
        at = datetime(2026, 11, 3, 14, 0, tzinfo=UTC)
        Inquiry.objects.create(kind="consultation", name="A", email="a@example.com", scheduled_at=at, call_minutes=30)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Inquiry.objects.create(kind="consultation", name="B", email="b@example.com", scheduled_at=at, call_minutes=30)

    def test_two_people_at_the_same_instant_get_a_clear_answer(self):
        self.hours()
        # both pass the check; the database turns the second one away
        with patch("leads.serializers.is_open_slot", return_value=True):
            self.assertEqual(self.book("2026-11-03T14:00:00Z").status_code, 201)
            res = self.book("2026-11-03T14:00:00Z", email="other@example.com")
        self.assertEqual(res.status_code, 409)
        self.assertIn("scheduled_at", res.json())

    def test_a_time_outside_the_hours_is_refused(self):
        self.hours()
        self.assertEqual(self.book("2026-11-03T02:00:00Z").status_code, 400)
        self.assertEqual(self.book("2026-11-03T14:10:00Z").status_code, 400)
        self.assertEqual(self.book("not a date").status_code, 400)

    def test_only_a_video_call_can_have_a_time(self):
        self.hours()
        self.assertEqual(self.book("2026-11-03T14:00:00Z", kind="quote", details="x").status_code, 400)

    def test_a_made_up_time_zone_is_dropped(self):
        self.hours()
        self.assertEqual(self.book("2026-11-03T14:00:00Z", client_timezone="Mars/Olympus").status_code, 201)
        self.assertEqual(Inquiry.objects.get().client_timezone, "")

    def test_requests_without_a_time_still_work(self):
        res = self.client.post("/api/inquiries/", {"kind": "consultation", "name": "K", "email": "k@example.com"})
        self.assertEqual(res.status_code, 201)
        self.assertIsNone(Inquiry.objects.get().scheduled_at)
        self.assertNotIn("call", res.json())

    def test_long_invite_lines_are_folded(self):
        self.hours()
        inq = Inquiry(pk=1, name="K", email="k@example.com", scheduled_at=datetime(2026, 11, 3, 14, 0, tzinfo=UTC), call_minutes=30)
        ics = call_invite(inq, "Video call", "word, " * 60, "https://meet.example.com/abc")
        self.assertTrue(all(len(line.encode()) <= 75 for line in ics.split("\r\n")))
        self.assertIn("\\,", ics)
        self.assertIn("LOCATION:https://meet.example.com/abc", ics)

    def test_admin_shows_the_time_in_both_zones(self):
        at = datetime(2026, 11, 3, 14, 0, tzinfo=UTC)
        inq = Inquiry.objects.create(kind="consultation", name="A", email="a@example.com", scheduled_at=at, call_minutes=30, client_timezone="America/New_York")
        User.objects.create_superuser("saif", "s@example.com", "pw-123456!")
        self.client.login(username="saif", password="pw-123456!")
        self.assertContains(self.client.get("/admin/leads/inquiry/?call=upcoming"), "7:00 PM")
        page = self.client.get(f"/admin/leads/inquiry/{inq.pk}/change/")
        self.assertContains(page, "7:00 PM")
        self.assertContains(page, "9:00 AM")

    def test_settings_page_has_the_hours_tables(self):
        User.objects.create_superuser("saif", "s@example.com", "pw-123456!")
        self.client.login(username="saif", password="pw-123456!")
        page = self.client.get(f"/admin/content/sitesettings/{self.site.pk}/change/")
        self.assertContains(page, "Video calls")
        self.assertContains(page, "call_hours-0-weekday")
        self.assertContains(page, "call_days_off-0-first_day")

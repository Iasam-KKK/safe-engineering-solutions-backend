import shutil
import tempfile

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

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

"""sync_drive against a fake Google Drive laid out like the team's real folder."""

import io
import shutil
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

from django.core.management import call_command
from django.test import TestCase, override_settings
from PIL import Image

from content.models import Partner, Review, SiteSettings, TeamMember
from portfolio.models import Category, Project

FOLDER = "application/vnd.google-apps.folder"
SHEET = "application/vnd.google-apps.spreadsheet"
DOC = "application/vnd.google-apps.document"
MEDIA = tempfile.mkdtemp()


def jpg(color=(120, 160, 200)):
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), color).save(buf, "JPEG")
    return buf.getvalue()


def pdf_bytes():
    from portfolio.tests_import import make_pdf

    tmp = Path(tempfile.mkdtemp()) / "x.pdf"
    make_pdf(tmp)
    data = tmp.read_bytes()
    shutil.rmtree(tmp.parent, ignore_errors=True)
    return data


class FakeDrive:
    """Answers the three Drive REST calls the mirror makes: list children, download, export."""

    def __init__(self):
        self.files = {}  # id -> dict(name, mimeType, parent, data, md5)
        self.n = 0

    def add(self, parent, name, mime, data=b""):
        self.n += 1
        fid = f"id{self.n}"
        self.files[fid] = {"id": fid, "name": name, "mimeType": mime, "parent": parent, "data": data, "md5": f"v{self.n}"}
        return fid

    def get(self, url, params=None, stream=False):
        params = params or {}
        if url.endswith("/files"):
            parent = unquote(params["q"]).split("'")[1]
            kids = [
                {"id": f["id"], "name": f["name"], "mimeType": f["mimeType"],
                 **({} if f["mimeType"] in (FOLDER, SHEET, DOC) else {"md5Checksum": f["md5"]}),
                 "modifiedTime": f["md5"]}
                for f in self.files.values() if f["parent"] == parent
            ]
            return Resp(json_data={"files": kids})
        fid = url.rstrip("/").split("/")[-1] if not url.endswith("/export") else url.split("/")[-2]
        return Resp(content=self.files[fid]["data"])


class Resp:
    def __init__(self, json_data=None, content=b"", status_code=200):
        self._json, self._content, self.status_code = json_data, content, status_code
        self.reason = "OK" if status_code < 400 else "Forbidden"

    def raise_for_status(self):
        pass

    def json(self):
        return self._json

    def iter_content(self, chunk_size=1):
        yield self._content


def build_drive():
    d = FakeDrive()
    projects = d.add("ROOT", "01 Projects", FOLDER)
    modular = d.add(projects, "Modular homes", FOLDER)
    floating = d.add(modular, "Floating homes", FOLDER)
    d.add(floating, "j 1065 floating house R3.pdf", "application/pdf", pdf_bytes())
    d.add(projects, "Project details (optional)", SHEET,
          "Project (folder or PDF name),City and state,One line about the project,Show on homepage? (yes/no),"
          "Names and addresses to hide on the drawings (separate with ;),Notes\n"
          "j 1065 floating house R3,\"Lake Travis, Texas\",Floating lake house with a roof deck,yes,,\n".encode())
    about = d.add("ROOT", "02 About us", FOLDER)
    d.add(about, "Team", SHEET,
          "Name (as on the website),Job title,Degrees and licenses,One line about them,Show on website? (yes/no)\n"
          "Saif ul Haq,Founder and lead engineer,B.Sc. Civil Engineering,Checks every sheet.,yes\n".encode())
    photos = d.add(about, "Team photos", FOLDER)
    d.add(photos, "Saif ul Haq.jpg", "image/jpeg", jpg())
    partners = d.add("ROOT", "03 Partners", FOLDER)
    d.add(partners, "Partner list", SHEET,
          "Firm name (as on the website),Their website (optional),Show on website? (yes/no),Notes\n"
          "KMW Properties,kmwproperties.com,yes,\n".encode())
    logos = d.add(partners, "Logos", FOLDER)
    d.add(logos, "KMW Properties.png", "image/png", jpg((200, 30, 30)))
    reviews = d.add("ROOT", "04 Reviews", FOLDER)
    d.add(reviews, "Reviews", SHEET,
          "Client (initials),Country,Stars (1-5),Review (copy it exactly),Kind of project,Month and year,"
          "Link to the review,Can we show their full name? (yes/no),Show on homepage? (yes/no)\n"
          "K. P.,United States,5,Great drawings and fast revisions.,Home extension,OCT 2026,,no,yes\n".encode())
    contact = d.add("ROOT", "05 Company and contact", FOLDER)
    d.add(contact, "Contact details", DOC,
          "CONTACT DETAILS\n\nEmail on the website: saifulhaq_kkk@yahoo.com\n\n"
          "Link for booking a video call (Calendly or similar): https://calendly.com/safe/30min\n\n"
          "How long is the video call? (for example 30 minutes): 30 minutes\n\n"
          "California license number: C 12345\n".encode())
    d.add("ROOT", "READ ME FIRST", DOC, b"read me")
    return d


@override_settings(MEDIA_ROOT=MEDIA, REVALIDATE_SECRET="", GOOGLE_SERVICE_ACCOUNT_FILE="key.json",
                   DRIVE_FOLDER_ID="ROOT", IMPORT_NOTIFY_EMAILS=["saif@example.com"])
class SyncDriveTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Category.objects.create(slug="modular-homes", name="Modular homes")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.mirror = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.mirror, True)
        self.drive = build_drive()

    def sync(self):
        out = StringIO()
        with override_settings(DRIVE_MIRROR_DIR=self.mirror), \
                mock.patch("portfolio.drive_sync.session", return_value=self.drive):
            call_command("sync_drive", stdout=out)
        return out.getvalue()

    def test_first_sync_loads_everything(self):
        from django.core import mail

        out = self.sync()
        p = Project.objects.get(slug="floating-house")
        self.assertFalse(p.is_published)                      # a draft until someone checks it
        self.assertTrue(p.show_on_home)
        self.assertEqual(p.location, "Lake Travis, Texas")    # from the details sheet
        self.assertEqual(p.subtitle, "Floating lake house with a roof deck")
        self.assertEqual(p.media.count(), 2)
        saif = TeamMember.objects.get(name="Saif ul Haq")
        self.assertTrue(saif.photo)
        self.assertEqual(saif.credentials, "B.Sc. Civil Engineering")
        kmw = Partner.objects.get(name="KMW Properties")
        self.assertTrue(kmw.logo)
        self.assertEqual(kmw.website, "https://kmwproperties.com")
        self.assertEqual(Review.objects.get().author, "K. P.")
        s = SiteSettings.load()
        self.assertEqual(s.booking_url, "https://calendly.com/safe/30min")
        self.assertEqual(s.consultation_length, "30 minutes")
        self.assertEqual(s.stamping_license, "C 12345")
        self.assertIn("new or changed: 01 Projects/Modular homes/Floating homes/j 1065 floating house R3.pdf", out)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("floating house", mail.outbox[0].body.lower())

    def test_second_sync_without_changes_does_nothing(self):
        self.sync()
        out = self.sync()
        self.assertIn("no changes", out)
        self.assertEqual(Project.objects.get(slug="floating-house").media.count(), 2)

    def test_new_file_is_picked_up(self):
        self.sync()
        floating = next(f["id"] for f in self.drive.files.values() if f["name"] == "Floating homes")
        tower = self.drive.add(floating, "Barge cabin - Miami FL", FOLDER)
        renders = self.drive.add(tower, "Renders", FOLDER)
        self.drive.add(renders, "R01 Deck.jpg", "image/jpeg", jpg())
        out = self.sync()
        self.assertIn("changes found", out)
        cabin = Project.objects.get(slug="barge-cabin")
        self.assertEqual(cabin.location, "Miami, FL")
        self.assertEqual(cabin.media.get().kind, "render")

    def test_removed_file_leaves_the_mirror(self):
        self.sync()
        fid = next(f["id"] for f in self.drive.files.values() if f["name"] == "KMW Properties.png")
        del self.drive.files[fid]
        self.sync()
        self.assertFalse((self.mirror / "03 Partners" / "Logos" / "KMW Properties.png").exists())


class DriveOff:
    """Google's answer when the Drive API isn't enabled for the key's Cloud project."""

    def get(self, url, params=None, stream=False):
        return Resp(json_data={"error": {"code": 403, "message": "Google Drive API has not been used in project 1 before or it is disabled.",
                                          "errors": [{"reason": "accessNotConfigured"}]}}, status_code=403)


@override_settings(GOOGLE_SERVICE_ACCOUNT_FILE="key.json", DRIVE_FOLDER_ID="ROOT")
class SyncDriveSetupTests(TestCase):
    def test_api_switched_off_says_how_to_fix_it(self):
        from django.core.management import CommandError

        mirror = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, mirror, True)
        with override_settings(DRIVE_MIRROR_DIR=mirror), mock.patch("portfolio.drive_sync.session", return_value=DriveOff()):
            with self.assertRaisesMessage(CommandError, "Google Drive API is switched off"):
                call_command("sync_drive", stdout=StringIO())

    def test_missing_key_file_spots_the_double_extension(self):
        from portfolio import drive_sync

        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        (folder / "drive-sync-key.json.json").write_text("{}")
        with override_settings(GOOGLE_SERVICE_ACCOUNT_FILE=str(folder / "drive-sync-key.json")):
            with self.assertRaisesMessage(drive_sync.DriveError, "rename it to drive-sync-key.json"):
                drive_sync.session()


@override_settings(MEDIA_ROOT=MEDIA, REVALIDATE_SECRET="", GOOGLE_SERVICE_ACCOUNT_FILE="key.json", DRIVE_FOLDER_ID="ROOT")
class PartnerLogoTests(TestCase):
    """The real Drive: logos without extensions, names that only roughly match the list."""

    def setUp(self):
        self.mirror = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.mirror, True)
        Partner.objects.create(name="Excellenceengd", is_active=True)  # renamed in the list since
        d = FakeDrive()
        partners = d.add("ROOT", "03 Partners", FOLDER)
        d.add(partners, "Partner list", SHEET,
              "Firm name (as on the website),Their website (optional),Show on website? (yes/no),Notes\n"
              "Excellence engineered ,,yes,\nNexus Engineering,,yes,\nKMW Properties,,yes,\n"
              "Rocky Mountains Prefab,,yes,\nBlackwell group ,,yes,\nStorm Fitness,,yes,\n".encode())
        logos = d.add(partners, "Logos", FOLDER)
        for name in ["Excellence Engineered", "NEXUS Design and Engineering", "KMW Properties LLC",
                     "Rocky Mountain Prefabs", "Black well group", "NGUYEN Architecture"]:
            d.add(logos, name, "image/jpeg", jpg())  # no extension, as uploaded
        self.drive = d

    def test_logos_match_loosely_and_old_names_are_hidden(self):
        out = StringIO()
        with override_settings(DRIVE_MIRROR_DIR=self.mirror), mock.patch("portfolio.drive_sync.session", return_value=self.drive):
            call_command("sync_drive", stdout=out)
        with_logo = set(Partner.objects.exclude(logo="").values_list("name", flat=True))
        self.assertEqual(with_logo, {"Excellence engineered", "Nexus Engineering", "KMW Properties",
                                     "Rocky Mountains Prefab", "Blackwell group", "NGUYEN Architecture"})
        self.assertFalse(Partner.objects.get(name="Storm Fitness").logo)
        self.assertTrue(all(n.endswith("-mark2.png") for n in Partner.objects.exclude(logo="").values_list("logo", flat=True)))
        self.assertFalse(Partner.objects.get(name="Excellenceengd").is_active)
        self.assertTrue(Partner.objects.get(name="NGUYEN Architecture").is_active)
        log = out.getvalue()
        self.assertIn("added 'NGUYEN Architecture' from its logo", log)
        self.assertIn("picture 'NEXUS Design and Engineering' used for 'Nexus Engineering'", log)
        self.assertIn("hidden, no longer in the Partner list: Excellenceengd", log)


class AddressPatternTests(TestCase):
    def test_dimensions_are_not_addresses(self):
        from portfolio.media_tools import ADDRESS

        for note in ["15 METER OF PIPE RUN", "3 FT CLEAR PATH TO STREET", "2 x 6 STUDS AT 16 IN O.C. TO RIDGE ROAD"]:
            self.assertIsNone(ADDRESS.search(note), note)
        for addr in ["1234 Deer Run", "1840 N Lake Shore Dr", "22 Main St, Apt 4", "12 A Street"]:
            self.assertIsNotNone(ADDRESS.search(addr), addr)


class LogoMarkTests(TestCase):
    def test_logo_on_black_becomes_ink_on_transparent(self):
        from PIL import ImageDraw

        from portfolio.media_tools import INK, logo_mark

        img = Image.new("RGB", (400, 200), (0, 0, 0))
        ImageDraw.Draw(img).rectangle([100, 60, 300, 140], fill=(200, 200, 200))
        mark = Image.open(io.BytesIO(logo_mark(img)))
        self.assertEqual(mark.mode, "RGBA")
        self.assertLess(mark.width, 260)  # trimmed to the drawing
        self.assertEqual(mark.getpixel((0, 0))[3], 0)  # background gone
        self.assertEqual(mark.getpixel((mark.width // 2, mark.height // 2)), INK + (255,))

    def test_white_lettering_on_transparent_is_kept(self):
        from PIL import ImageDraw

        from portfolio.media_tools import logo_mark

        img = Image.new("RGBA", (300, 100), (255, 255, 255, 0))
        ImageDraw.Draw(img).rectangle([20, 20, 280, 80], fill=(255, 255, 255, 255))
        mark = Image.open(io.BytesIO(logo_mark(img)))
        self.assertEqual(mark.getpixel((mark.width // 2, mark.height // 2))[3], 255)


class StreetWithoutSuffixTests(TestCase):
    def test_location_block_without_rd_or_st_is_found(self):
        """Storm Fitness Center's cover: LOCATION / 18340 MIDDLEBELT / LIVONIA, MI 48152."""
        import pymupdf

        from portfolio.media_tools import private_text

        doc = pymupdf.open()
        page = doc.new_page(width=1728, height=1152)
        page.insert_text((60, 100), "PROJECT", fontsize=11)
        page.insert_text((60, 116), "STORM FITNESS CENTER", fontsize=11)
        page.insert_text((60, 150), "LOCATION", fontsize=11)
        page.insert_text((60, 166), "18340 MIDDLEBELT", fontsize=11)
        page.insert_text((60, 182), "LIVONIA, MI 48152", fontsize=11)
        page.insert_text((600, 600), "LOCATION OF EXISTING PANEL", fontsize=11)  # a drawing note, not an address
        found = private_text(page)
        self.assertIn("18340 MIDDLEBELT", found)
        self.assertIn("LIVONIA, MI 48152", found)
        self.assertNotIn("STORM FITNESS CENTER", found)
        self.assertFalse(any("PANEL" in f for f in found))

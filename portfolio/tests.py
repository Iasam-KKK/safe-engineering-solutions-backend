import atexit
import json
import shutil
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from content.models import Partner
from portfolio.management.commands.seed_design import DEFAULT_ASSETS, SEED
from portfolio.models import Category, Project


def seed_assets() -> str:
    """The design's image folder. Where it isn't (GitHub Actions has only this repo), small
    stand-in pictures with the same names, so the seed and these tests still run."""
    if DEFAULT_ASSETS.is_dir():
        return str(DEFAULT_ASSETS)
    from PIL import Image

    folder = Path(tempfile.mkdtemp(prefix="seed-assets-"))
    atexit.register(shutil.rmtree, folder, True)
    for p in json.loads(SEED.read_text(encoding="utf-8"))["projects"]:
        for m in p["media"]:
            for key in ("image", "before_image"):
                if m.get(key):
                    Image.new("RGB", (64, 48), (200, 200, 200)).save(folder / m[key], "JPEG")
    return str(folder)


@override_settings(MEDIA_ROOT="test_media_tmp", REVALIDATE_SECRET="")
class SeededApiTests(TestCase):
    """Runs against the real design seed so the API matches what the frontend expects."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_design", assets=seed_assets(), stdout=StringIO())

    @classmethod
    def tearDownClass(cls):
        import shutil

        shutil.rmtree("test_media_tmp", ignore_errors=True)
        super().tearDownClass()

    def test_seed_counts(self):
        self.assertEqual(Project.objects.count(), 20)
        self.assertEqual(Partner.objects.count(), 8)
        self.assertTrue(Category.objects.filter(slug="structures", show_on_home=True).exists())

    def test_media_urls_can_be_relative(self):
        """For one-address setups (a Tailscale link): the site forwards /media/ to Django."""
        with self.settings(RELATIVE_MEDIA_URLS=True):
            data = self.client.get("/api/home/").json()
        self.assertTrue(data["running"][0]["cover"]["url"].startswith("/media/"))

    @override_settings(RELATIVE_MEDIA_URLS=False)
    def test_home_payload(self):
        data = self.client.get("/api/home/").json()
        tabs = [t["label"] for t in data["best_work"]]
        self.assertEqual(tabs, ["NEW HOME", "HOUSE MODIFICATION", "COMMERCIAL", "ADU", "GARAGE", "STRUCTURES", "MODULAR HOMES"])
        ext = next(p for t in data["best_work"] for p in t["projects"] if p["slug"] == "home-extension")
        self.assertEqual([s["sheet_no"] for s in ext["sheets"]], [f"A10{i}" for i in range(8)])
        self.assertEqual(ext["media_kind"], "SHEETS")
        self.assertEqual(len(data["running"]), 4)
        self.assertTrue(data["running"][0]["cover"]["url"].startswith("http://testserver/media/"))

    def test_placeholder_sheet(self):
        data = self.client.get("/api/projects/telecom-steel-structures/").json()
        first = data["media"][0]
        self.assertIsNone(first["image"])
        self.assertEqual(first["placeholder_glyph"], "tower")
        self.assertEqual(first["sheet_no"], "S101")

    def test_pair(self):
        data = self.client.get("/api/projects/home-extension/").json()
        pair = data["media"][0]
        self.assertEqual(pair["before"]["label"], "EXISTING")
        self.assertEqual(pair["after_label"], "PROPOSED")
        self.assertTrue(pair["before"]["image"]["width"] > 0)

    def test_filters_and_404(self):
        self.assertEqual(len(self.client.get("/api/projects/?category=structures").json()), 2)
        self.assertEqual(len(self.client.get("/api/projects/?category=mep").json()), 4)
        self.assertEqual(self.client.get("/api/projects/nope/").status_code, 404)

    def test_unpublished_hidden(self):
        Project.objects.filter(slug="pelican-house").update(is_published=False)
        slugs = [p["slug"] for p in self.client.get("/api/projects/").json()]
        self.assertNotIn("pelican-house", slugs)
        self.assertEqual(self.client.get("/api/projects/pelican-house/").status_code, 404)

    def test_site_payload(self):
        data = self.client.get("/api/site/").json()
        self.assertEqual(data["settings"]["whatsapp_url"], "https://wa.me/923329866561")
        self.assertEqual(data["settings"]["rating"]["value"], 4.9)
        self.assertEqual(len(data["partners"]), 8)


@override_settings(MEDIA_ROOT="test_media_tmp", REVALIDATE_SECRET="")
class AdminScreensTests(TestCase):
    """Every admin list and edit screen Saif uses loads without errors."""

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import User

        call_command("seed_design", assets=seed_assets(), stdout=StringIO())
        User.objects.create_superuser("saif", "s@example.com", "pw-123456!")

    def setUp(self):
        self.client.login(username="saif", password="pw-123456!")

    def test_screens(self):
        from content.models import SiteSettings

        p = Project.objects.get(slug="home-extension")
        urls = [
            "/admin/",
            "/admin/portfolio/project/", f"/admin/portfolio/project/{p.pk}/change/", "/admin/portfolio/project/add/",
            "/admin/portfolio/category/",
            "/admin/content/partner/", "/admin/content/review/", "/admin/content/review/add/",
            "/admin/content/teammember/add/", "/admin/leads/inquiry/",
            f"/admin/content/sitesettings/{SiteSettings.load().pk}/change/",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        # the settings list jumps straight to the single settings form
        self.assertEqual(self.client.get("/admin/content/sitesettings/").status_code, 302)


@override_settings(REVALIDATE_SECRET="s3cret", FRONTEND_URL="http://front")
class RevalidateTests(TestCase):
    def test_saving_a_project_queues_its_tags(self):
        from config import revalidate as rv

        with mock.patch.object(rv.threading, "Thread") as thread, self.captureOnCommitCallbacks(execute=True):
            Project.objects.create(title="Another job", subtitle="x")
        args = thread.call_args.kwargs["args"][0]
        self.assertIn("projects", args)
        self.assertIn("project:another-job", args)


class BestWorkTests(TestCase):
    def test_every_published_project_appears_in_its_tab(self):
        """"Our best work" lists every published project of the category; "Show on home" only puts it first."""
        from portfolio.models import Category, Project, ProjectMedia

        c = Category.objects.create(slug="mep-test", name="MEP test", show_on_home=True, home_label="MEP TEST")
        plain = Project.objects.create(title="Plain job", slug="plain-job", is_published=True, show_on_home=False)
        star = Project.objects.create(title="Star job", slug="star-job", is_published=True, show_on_home=True)
        for p in (plain, star):
            p.categories.add(c)
            ProjectMedia.objects.create(project=p, kind="sheet", sheet_no="P101", title="Plumbing", alt="x", placeholder_glyph="plan")
        data = self.client.get("/api/home/").json()
        tab = next(t for t in data["best_work"] if t["slug"] == "mep-test")
        self.assertEqual([x["slug"] for x in tab["projects"]], ["star-job", "plain-job"])

"""Importer tests: a small but realistic import folder is generated on the fly."""

import csv
import shutil
import subprocess
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase, override_settings
from PIL import Image

from portfolio import media_tools as mt
from portfolio.models import Category, Project

MEDIA = tempfile.mkdtemp()


def make_pdf(path: Path):
    """Two ARCH-D style pages with a right-hand title block holding an address and a client name."""
    import pymupdf

    doc = pymupdf.open()
    for no, title in (("A101", "EXISTING FLOOR PLAN"), ("A201", "PROPOSED ELEVATIONS")):
        page = doc.new_page(width=1728, height=1152)
        page.draw_rect(pymupdf.Rect(100, 100, 1300, 1000), color=(0, 0, 0), width=2)
        page.draw_line((300, 300), (1100, 800), color=(0, 0, 0), width=2)
        page.insert_text((1420, 200), "SAFE ENGINEERING SOLUTIONS", fontsize=14)
        page.insert_text((1420, 300), "123 Secret Lane, Fairfax VA", fontsize=12)
        page.insert_text((1420, 340), "Client: Jane Client", fontsize=12)
        page.insert_text((1420, 900), title, fontsize=14)
        page.insert_text((1500, 1050), no, fontsize=28)
    doc.save(path)


def make_folder(root: Path, with_video=True):
    proj = root / "garage"
    for sub in ("sheets", "renders", "before", "videos"):
        (proj / sub).mkdir(parents=True)
    make_pdf(proj / "sheets" / "set.pdf")
    Image.new("RGB", (3000, 2000), (120, 160, 200)).save(proj / "renders" / "R01 Front.jpg")
    Image.new("RGB", (1600, 1000), (90, 90, 90)).save(proj / "before" / "R01 Front.jpg")
    if with_video:
        subprocess.run(
            [mt.ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=640x360:rate=24",
             "-pix_fmt", "yuv420p", str(proj / "videos" / "walkthrough.mov")],
            check=True,
        )
    with (root / "projects.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["folder", "title", "categories", "subtitle", "location", "scope", "show_on_home", "redact_text"])
        w.writerow(["garage", "Test garage", "home-modification", "Two-storey garage", "Fairfax, Virginia",
                    "Permit set", "yes", "123 Secret Lane|Jane Client|Nobody Here"])


@override_settings(MEDIA_ROOT=MEDIA, REVALIDATE_SECRET="")
class ImporterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Category.objects.create(slug="home-modification", name="Home modification")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def run_cmd(self, *args):
        out = StringIO()
        call_command("import_projects", str(self.root), *args, stdout=out)
        return out.getvalue()

    def test_sheet_number_and_title_read_from_title_block(self):
        make_pdf(self.root / "set.pdf")
        pages, found = mt.pdf_to_pages(self.root / "set.pdf", ["Jane Client"], render=False)
        self.assertEqual([(p.sheet_no, p.title) for p in pages], [("A101", "Existing floor plan"), ("A201", "Proposed elevations")])
        self.assertEqual(found["Jane Client"], 2)

    def test_redaction_removes_text(self):
        """The address area is ink before redaction and plain white after; the source PDF is untouched."""
        import io

        import pymupdf

        make_pdf(self.root / "set.pdf")
        plain, _ = mt.pdf_to_pages(self.root / "set.pdf")
        redacted, found = mt.pdf_to_pages(self.root / "set.pdf", ["123 Secret Lane"])
        self.assertEqual(found["123 Secret Lane"], 2)
        k = 2400 / 1728  # page points -> pixels
        r = pymupdf.open(self.root / "set.pdf")[0].search_for("123 Secret Lane")[0]  # where the street is drawn
        box = (int(r.x0 * k) + 2, int(r.y0 * k) + 2, int(r.x1 * k) - 2, int(r.y1 * k) - 2)

        def darkest(jpeg):
            with Image.open(io.BytesIO(jpeg)) as im:
                self.assertEqual(max(im.size), 2400)
                return min(im.convert("L").crop(box).getdata())

        self.assertLess(darkest(plain[0].jpeg), 100)      # text there before
        self.assertGreater(darkest(redacted[0].jpeg), 235)  # white after
        doc = pymupdf.open(self.root / "set.pdf")
        self.assertTrue(doc[0].search_for("123 Secret Lane"))

    def test_dry_run_writes_nothing(self):
        make_folder(self.root, with_video=False)
        out = self.run_cmd("--dry-run")
        self.assertIn("NEW    Test garage", out)
        self.assertIn("sheet numbers: A101, A201", out)
        self.assertIn("'Nobody Here' not found", out)
        self.assertFalse(Project.objects.exists())

    def test_import_creates_draft_with_sheets_render_pair_and_video(self):
        make_folder(self.root)
        self.run_cmd()
        p = Project.objects.get(slug="test-garage")
        self.assertFalse(p.is_published)
        self.assertTrue(p.show_on_home)
        self.assertEqual(p.location, "Fairfax, Virginia")
        self.assertEqual([f.label for f in p.facts.all()], ["LOCATION", "SCOPE"])
        media = list(p.media.all())
        # the before/after render leads, then the video, then the sheets
        self.assertEqual([(m.kind, m.sheet_no) for m in media], [("render", "R01"), ("video", ""), ("sheet", "A101"), ("sheet", "A201")])
        render = media[0]
        self.assertTrue(render.before_image)
        self.assertEqual((render.before_label, render.after_label), ("EXISTING", "PROPOSED"))
        video = media[1]
        self.assertTrue(video.video.name.endswith(".mp4"))  # .mov was converted
        self.assertTrue(video.image)                        # with a poster
        self.assertFalse(video.in_pile)
        self.assertEqual(max(media[2].width, media[2].height), 2400)  # the first sheet

    def test_rerun_does_not_duplicate(self):
        make_folder(self.root, with_video=False)
        self.run_cmd()
        out = self.run_cmd()
        self.assertEqual(Project.objects.get().media.count(), 3)
        self.assertIn("3 already there", out)

    def test_bad_category_stops_everything(self):
        make_folder(self.root, with_video=False)
        text = (self.root / "projects.csv").read_text(encoding="utf-8-sig").replace("home-modification", "spaceships")
        (self.root / "projects.csv").write_text(text, encoding="utf-8-sig")
        out = self.run_cmd()
        self.assertIn("unknown category 'spaceships'", out)
        self.assertFalse(Project.objects.exists())

    def test_publish_flag(self):
        make_folder(self.root, with_video=False)
        self.run_cmd("--publish")
        self.assertTrue(Project.objects.get().is_published)
        api = self.client.get("/api/projects/test-garage/").json()
        self.assertEqual(api["media"][0]["before"]["label"], "EXISTING")

    def test_template(self):
        out = StringIO()
        call_command("import_projects", "--template", str(self.root / "tpl"), stdout=out)
        tpl = self.root / "tpl"
        self.assertTrue((tpl / "00 Project list.csv").exists())
        self.assertTrue((tpl / "06 Structures" / "Telecom steel structures").is_dir())
        self.assertTrue((tpl / "_TEMPLATE - Project name - City ST" / "3 Before photos").is_dir())
        self.assertIn("Home modification", (tpl / "00 READ ME.txt").read_text(encoding="utf-8"))


@override_settings(MEDIA_ROOT=MEDIA, REVALIDATE_SECRET="")
class AdminUploadTests(TestCase):
    def test_uploaded_pdf_images_and_video(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from portfolio.importer import Report, ingest_uploads

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        make_folder(root)
        p = Project.objects.create(title="Upload test", subtitle="x")
        up = lambda f: SimpleUploadedFile(f.name, f.read_bytes())  # noqa: E731
        g = root / "garage"
        report = Report()
        counts = ingest_uploads(
            p, [up(g / "sheets" / "set.pdf"), up(g / "renders" / "R01 Front.jpg"), up(g / "videos" / "walkthrough.mov")], "auto", report
        )
        self.assertEqual((counts["sheet"], counts["render"], counts["video"]), (2, 1, 1), report.warnings)
        counts = ingest_uploads(p, [up(g / "before" / "R01 Front.jpg")], "before", report)
        self.assertEqual(counts["pairs"], 1)
        # same file again replaces instead of duplicating
        ingest_uploads(p, [up(g / "renders" / "R01 Front.jpg")], "auto", report)
        self.assertEqual(p.media.count(), 4)


@override_settings(MEDIA_ROOT=MEDIA, REVALIDATE_SECRET="")
class TeamLayoutTests(TestCase):
    """The layout the team fills in on Drive: category folders, numbered subfolders, friendly headers."""

    @classmethod
    def setUpTestData(cls):
        Category.objects.create(slug="structures", name="Structures")

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        proj = self.root / "06 Structures" / "Telecom steel structures" / "Monopole 150 ft - Houston TX"
        for sub in ("1 Sheets", "2 Renders", "3 Before photos", "4 Videos", "5 Site photos"):
            (proj / sub).mkdir(parents=True)
        make_pdf(proj / "1 Sheets" / "monopole set.pdf")
        Image.new("RGB", (800, 600), (10, 120, 200)).save(proj / "2 Renders" / "R01 Tower.jpg")
        Image.new("RGB", (800, 600), (90, 90, 90)).save(proj / "3 Before photos" / "R01 Tower.jpg")
        Image.new("RGB", (800, 600), (30, 30, 30)).save(proj / "5 Site photos" / "Erected tower.jpg")
        Image.new("RGB", (800, 600), (200, 200, 200)).save(proj / "loose render.jpg")
        with (self.root / "00 Project list - Untitled.csv").open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["Folder name", "Title on website", "Categories", "One-line description", "City, State",
                        "Show on homepage (yes/no)", "Status (team)", "Notes (team)"])
            w.writerow(["Monopole 150 ft - Houston TX", "Monopole 150 ft", "Structures", "Telecom monopole",
                        "Houston, Texas", "no", "Ready", "client ok'd"])

    def test_team_layout(self):
        call_command("import_projects", str(self.root), stdout=StringIO())
        p = Project.objects.get(slug="monopole-150-ft")
        self.assertEqual(p.location, "Houston, Texas")
        media = list(p.media.all())
        self.assertEqual([m.kind for m in media], ["render", "render", "render", "sheet", "sheet"])  # renders lead
        titles = [m.title for m in media]
        self.assertIn("Erected tower", titles)                 # site photo
        self.assertIn("Loose render", titles)                  # file straight in the project folder
        tower = next(m for m in media if m.sheet_no == "R01")
        self.assertTrue(tower.before_image)                    # paired from "3 Before photos"
        photo = next(m for m in media if m.title == "Erected tower")
        self.assertFalse(photo.in_pile)

    def test_duplicate_folder_names_stop_the_import(self):
        (self.root / "07 Modular homes" / "Monopole 150 ft - Houston TX").mkdir(parents=True)
        out = StringIO()
        call_command("import_projects", str(self.root), stdout=out)
        self.assertIn("2 folders are named", out.getvalue())
        self.assertFalse(Project.objects.exists())


@override_settings(MEDIA_ROOT=MEDIA, REVALIDATE_SECRET="")
class SimpleLayoutTests(TestCase):
    """The layout the non-technical team uses: no spreadsheet, folders say everything."""

    @classmethod
    def setUpTestData(cls):
        for slug, name in [("structures", "Structures"), ("modular-homes", "Modular homes")]:
            Category.objects.create(slug=slug, name=name)
        # the website's placeholder project, waiting for real sheets
        from portfolio.models import ProjectMedia

        ph = Project.objects.create(title="Floating house", slug="floating-house", subtitle="Modular home")
        ProjectMedia.objects.create(project=ph, sheet_no="A201", title="Elevations", alt="x", placeholder_glyph="float")

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        projects = self.root / "01 Projects"
        floating = projects / "Modular homes" / "Floating homes"
        floating.mkdir(parents=True)
        make_pdf(floating / "j 1065 floating house R3.pdf")
        tower = projects / "Structures" / "Telecom steel structures" / "Monopole 150 ft - Houston TX"
        (tower / "Renders").mkdir(parents=True)
        Image.new("RGB", (900, 600), (10, 120, 200)).save(tower / "Renders" / "R01 Tower.jpg")
        make_pdf(tower / "monopole set.pdf")
        (projects / "_Example project folder - Name - City ST" / "1 Sheets").mkdir(parents=True)
        (self.root / "02 About us").mkdir()
        with (self.root / "01 Projects" / "Project details (optional) - Sheet1.csv").open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["Project (folder or PDF name)", "City and state", "One line about the project",
                        "Show on homepage? (yes/no)", "Names and addresses to hide on the drawings (separate with ;)", "Notes"])
            w.writerow(["Monopole 150 ft - Houston TX", "Houston, Texas", "150 ft monopole with antenna mounts", "yes", "", ""])

    def run_cmd(self, *args):
        out = StringIO()
        call_command("import_projects", str(self.root), *args, stdout=out)
        return out.getvalue()

    def test_folder_next_to_the_pdf_is_the_same_project(self):
        """Renders added later in a folder join the loose PDF's project; an older revision is skipped."""
        self.run_cmd()
        floating = self.root / "01 Projects" / "Modular homes" / "Floating homes"
        make_pdf(floating / "j 1065 floating house R2.pdf")
        folder = floating / "Floating house - Lake Travis TX"
        (folder / "2 Renders").mkdir(parents=True)
        (folder / "3 Before photos").mkdir()
        Image.new("RGB", (900, 600), (20, 90, 160)).save(folder / "2 Renders" / "R01 Deck.jpg")
        Image.new("RGB", (900, 600), (160, 140, 120)).save(folder / "3 Before photos" / "R01 Deck.jpg")
        out = self.run_cmd()
        self.assertIn("imported as one project", out)
        self.assertIn("older set of Floating house, skipped (using j 1065 floating house R3.pdf)", out)
        self.assertFalse(Project.objects.filter(slug__startswith="floating-house-").exists())
        p = Project.objects.get(slug="floating-house")
        render = p.media.get(kind="render")
        self.assertTrue(render.before_image)  # the before photo became a before/after slider
        self.assertEqual(p.media.filter(kind="sheet").count(), 2)  # R3's two sheets once, not R2's as well
        # the render arrived after the PDF but leads the viewer and is the card picture
        first = p.media.order_by("order").first()
        self.assertEqual(first.kind, "render")
        self.assertEqual(p.cover_media(), first)
        kinds = list(p.media.order_by("order").values_list("kind", flat=True))
        self.assertEqual(kinds, sorted(kinds, key=lambda k: {"render": 0, "video": 1, "sheet": 2}[k]))

    def test_renders_in_a_zip(self):
        """A renders ZIP (as the team uploads them) imports like loose files, safely and only once."""
        import io
        import zipfile

        folder = self.root / "01 Projects" / "Structures" / "Telecom steel structures" / "Monopole 150 ft - Houston TX"
        def jpg(colour):
            buf = io.BytesIO()
            Image.new("RGB", (900, 600), colour).save(buf, "JPEG")
            return buf.getvalue()
        with zipfile.ZipFile(folder / "Renders" / "Renders-1.zip", "w") as zf:
            zf.writestr("Renders-1/R02 Antenna deck.jpg", jpg((30, 90, 160)))
            zf.writestr("Renders-1/R03 Base.png", jpg((60, 60, 60)))
            zf.writestr("Renders-1/Before/R02 Antenna deck.jpg", jpg((150, 140, 120)))
            zf.writestr("__MACOSX/Renders-1/._R02 Antenna deck.jpg", b"junk")
            zf.writestr("Renders-1/notes.txt", b"not a picture")
            zf.writestr("../escape.jpg", jpg((255, 0, 0)))
        out = self.run_cmd()
        p = Project.objects.get(slug="monopole-150-ft")
        renders = {m.sheet_no for m in p.media.filter(kind="render")}
        self.assertTrue({"R02", "R03"} <= renders, out)
        self.assertTrue(p.media.get(sheet_no="R02").before_image)  # the Before folder inside the ZIP paired up
        self.assertFalse((self.root.parent / "escape.jpg").exists())
        self.assertFalse(any("escape" in m.source for m in p.media.all()))
        self.assertTrue(any("Renders-1.zip!/" in m.source for m in p.media.all()))
        before = p.media.count()
        self.run_cmd()
        self.assertEqual(p.media.count(), before)  # second run: nothing added twice

    def test_folder_with_nothing_yet_makes_no_project(self):
        started = self.root / "01 Projects" / "Modular homes" / "Floating homes" / "Lake cabin - Austin TX"
        (started / "2 Renders").mkdir(parents=True)
        (started / "2 Renders" / "brief.txt").write_text("renders coming")
        out = self.run_cmd()
        self.assertFalse(Project.objects.filter(slug="lake-cabin").exists())
        self.assertIn("nothing to import yet", out)

    def test_clean_title(self):
        from portfolio.importer import clean_title

        self.assertEqual(clean_title("j 1065 floating house R3"), ("Floating house", ""))
        self.assertEqual(clean_title("Monopole 150 ft - Houston TX"), ("Monopole 150 ft", "Houston, TX"))
        self.assertEqual(clean_title("150 ft monopole rev 2"), ("150 ft monopole", ""))

    def test_scan_import(self):
        out = self.run_cmd()
        self.assertIn("Found 2 project(s)", out)
        # the loose PDF filled the placeholder project; its placeholder sheet is gone
        fh = Project.objects.get(slug="floating-house")
        self.assertEqual([m.sheet_no for m in fh.media.all()], ["A101", "A201"])
        self.assertTrue(all(m.image for m in fh.media.all()))
        self.assertIn("placeholder sheet(s) replaced", out)
        # the folder project: title and location from its name, details from the sheet
        tower = Project.objects.get(slug="monopole-150-ft")
        self.assertEqual(tower.location, "Houston, Texas")
        self.assertEqual(tower.subtitle, "150 ft monopole with antenna mounts")
        self.assertTrue(tower.show_on_home)
        self.assertFalse(tower.is_published)
        self.assertEqual([c.slug for c in tower.categories.all()], ["structures"])
        self.assertEqual(sorted(m.kind for m in tower.media.all()), ["render", "sheet", "sheet"])
        # private text found and hidden automatically, and reported
        self.assertIn("hidden automatically", out)
        self.assertIn("123 Secret Lane", out)
        self.assertIn("Jane Client", out)

    def test_same_name_from_another_pdf_gets_its_own_page(self):
        self.run_cmd()
        make_pdf(self.root / "01 Projects" / "Modular homes" / "Floating homes" / "j 1102 floating house R1.pdf")
        self.run_cmd()
        self.assertTrue(Project.objects.filter(slug="floating-house-2").exists())
        self.assertEqual(Project.objects.get(slug="floating-house").media.count(), 2)  # unchanged


class TitleBlockTests(TestCase):
    """Modelled on the team's real floating-house set: CLIENT label with the name under it,
    DRAWING NAME label, MEP101 / A100a sheet numbers, client name repeated elsewhere."""

    def make_set(self, path: Path):
        import pymupdf

        doc = pymupdf.open()
        for no, title, label_client in (("A100a", "ARCHITECTURAL DRAWINGS", True), ("MEP101", "ELECTRICAL PLAN FFL", True),
                                        ("S101", "DOCK FLOATS", False)):
            page = doc.new_page(width=1728, height=1152)
            page.draw_rect(pymupdf.Rect(80, 80, 1380, 1070), color=(0, 0, 0), width=2)
            page.insert_text((1420, 200), "www.thesafesolutions.com", fontsize=10)
            if label_client:
                page.insert_text((1420, 300), "CLIENT", fontsize=10)
                page.insert_text((1420, 320), "JOSEPH S. KARL.", fontsize=12)
                page.insert_text((1420, 340), "FLOATING LAKE HOUSE", fontsize=12)
            else:  # the name printed in a view title, no label nearby
                page.insert_text((200, 1000), "FRONT VIEW JOSEPH S. KARL.", fontsize=12)
            page.insert_text((1420, 860), "DRAWING NAME", fontsize=10)
            page.insert_text((1420, 880), title, fontsize=12)
            page.insert_text((1420, 1000), "Project number J-106524   Date 4 AUGUST 2024", fontsize=8)
            page.insert_text((1520, 1050), no, fontsize=24)
        doc.save(path)

    def test_numbers_titles_and_client_on_every_sheet(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        self.make_set(root / "set.pdf")
        pages, found = mt.pdf_to_pages(root / "set.pdf", render=False, auto_hide=True)
        self.assertEqual([p.sheet_no for p in pages], ["A100a", "MEP101", "S101"])
        self.assertEqual([p.title for p in pages], ["Architectural Drawings", "Electrical Plan Ffl", "Dock Floats"])
        self.assertEqual(found.get("auto: JOSEPH S. KARL."), 3)  # hidden on all three sheets

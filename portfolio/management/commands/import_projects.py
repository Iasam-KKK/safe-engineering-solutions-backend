"""Import projects, drawing sheets, renders and videos from a folder.

    uv run python manage.py import_projects --template ../import      # make an empty, explained import folder
    uv run python manage.py import_projects ../import --dry-run       # check: what would be imported, any problems
    uv run python manage.py import_projects ../import                 # import (new projects arrive as drafts)

Options: --publish (make imported projects live at once), --update (overwrite title/text/facts of
projects that already exist from the CSV), --refresh (re-process files already imported),
--no-transcode (keep MP4s as they are instead of compressing them).
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from portfolio.importer import run_import
from portfolio.models import Category

HOW_TO = """SES WEBSITE DATA: HOW TO FILL THIS IN
(The same instructions are in "00 READ ME" in the team's Google Drive folder.)

1. WHERE A PROJECT GOES
Put each project in the folder of its kind (01 Home modification ... 08 Civil). Structures,
Modular homes and Civil have a folder per type. A project that fits two kinds goes in one folder
only; list both kinds in the project list's Categories column.

2. NAMING A PROJECT FOLDER
   Project name - City ST        e.g.  Monopole 150 ft - Houston TX
No client names, no street addresses, and no two project folders with the same name.
Copy the folder name exactly into the "Folder name" column of the project list.

3. INSIDE A PROJECT FOLDER (as in "_TEMPLATE - Project name - City ST")
   1 Sheets        the drawing set as PDF (every page = one sheet), or images named "A101 Floor plan.jpg"
   2 Renders       "R01 Front.jpg", "R02 Dusk.jpg"
   3 Before photos existing photos/drawings named like the render or sheet they pair with ("R01 Front.jpg")
   4 Videos        walkthroughs, animations (MP4 or MOV)
   5 Site photos   the job during construction or finished
Empty folders are fine. Loose files in the project folder are sorted by type.

4. QUALITY
PDF exports from Revit/CAD (no scans; Revit/CAD files can't be read). Best 3 to 8 sheets per project.
Images at least 2000 px wide. Videos: MP4 1080p, under 3 minutes.

5. THE PROJECT LIST (projects.csv, or "00 Project list" downloaded from Drive as CSV)
Folder name, Title on website, Categories (separate with ;), One-line description, City, State,
Scope, Codes, Delivered, Description, Show on homepage (yes/no), Stage (running/built),
Stage label, Stage date, Card text, Text to hide on sheets (separate with |), Areas to hide
(right:0.18 / bottom:0.12 / x0,y0,x1,y1), Order. "Status (team)" and "Notes (team)" are ignored.

6. BEFORE IMPORTING, CHECK EVERY SHEET FOR
client names and street addresses, other firms' names and logos, engineer stamps, seals and
signatures (never publish these), and the client's permission. Imported projects arrive as drafts.

7. RUN (on the computer with the website code)
   uv run python manage.py import_projects <this folder> --dry-run
   uv run python manage.py import_projects <this folder>
"""


class Command(BaseCommand):
    help = "Import projects, drawing sheets, renders and videos from a folder (see --template)."

    def add_arguments(self, parser):
        parser.add_argument("folder", nargs="?", help="Folder with projects.csv and one subfolder per project")
        parser.add_argument("--template", metavar="DIR", help="Write an empty import folder with instructions to DIR and stop")
        parser.add_argument("--dry-run", action="store_true", help="Only report what would be imported")
        parser.add_argument("--publish", action="store_true", help="Make imported projects live straight away")
        parser.add_argument("--update", action="store_true", help="Overwrite text and facts of existing projects from the CSV")
        parser.add_argument("--refresh", action="store_true", help="Re-process files that were already imported")
        parser.add_argument("--no-transcode", action="store_true", help="Keep MP4 videos as they are")
        parser.add_argument("--no-auto-hide", action="store_true",
                            help="Don't hide addresses, emails, phone numbers and CLIENT/OWNER lines automatically")

    def handle(self, *args, **o):
        if o["template"]:
            return self._template(Path(o["template"]))
        if not o["folder"]:
            raise CommandError("Give the import folder, or --template DIR to create one.")
        root = Path(o["folder"]).resolve()
        if not root.is_dir():
            raise CommandError(f"Folder not found: {root}")
        self.stdout.write(f"Importing from {root}{'  (dry run)' if o['dry_run'] else ''}\n")
        report = run_import(
            root, dry_run=o["dry_run"], publish=o["publish"], update=o["update"], refresh=o["refresh"],
            transcode_videos=not o["no_transcode"], auto_hide=not o["no_auto_hide"], out=self.stdout.write,
        )
        for line in report.lines:
            self.stdout.write(self.style.SUCCESS(line))
        for w in report.warnings:
            self.stdout.write(self.style.WARNING("CHECK: " + w))
        if not o["dry_run"] and report.lines:
            self.stdout.write(
                "\nNext: open each project in the admin and look through every sheet for names, addresses and stamps"
                + (" (they are already live)." if o["publish"] else ", then tick Published.")
            )

    def _template(self, root: Path):
        """The same layout as the team's Google Drive folder, empty."""
        import csv

        from portfolio.importer import TEAM_HEADER

        if any(root.glob("*roject list*.csv")) or (root / "projects.csv").exists():
            raise CommandError(f"{root} already has a project list; not overwriting it.")
        groups = {
            "01 Home modification": [], "02 New residential": [], "03 New commercial": [],
            "04 ADU and garage": [], "05 MEP": [],
            "06 Structures": ["Telecom steel structures", "Solar panel stands", "Other structures"],
            "07 Modular homes": ["Container homes", "Floating homes", "Green houses"],
            "08 Civil": ["Land and site planning", "Storm and drainage"],
        }
        for cat, subs in groups.items():
            (root / cat).mkdir(parents=True, exist_ok=True)
            for sub in subs:
                (root / cat / sub).mkdir(exist_ok=True)
        tpl = root / "_TEMPLATE - Project name - City ST"
        for sub in ("1 Sheets", "2 Renders", "3 Before photos", "4 Videos", "5 Site photos"):
            (tpl / sub).mkdir(parents=True, exist_ok=True)
        with (root / "00 Project list.csv").open("w", encoding="utf-8-sig", newline="") as fh:
            csv.writer(fh).writerow(TEAM_HEADER)
        cats = "\n".join(f"   {c.name}" for c in Category.objects.order_by("order"))
        (root / "00 READ ME.txt").write_text(HOW_TO + "\nCATEGORIES (for the Categories column)\n" + cats + "\n", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Import folder ready: {root}"))

"""Load the projects, categories and partners from the finished design into the database.

    uv run python manage.py seed_design            # adds what's missing, keeps admin edits
    uv run python manage.py seed_design --reset    # deletes portfolio + partners first

Images are copied from references/design/assets (the files the design used,
with addresses and other firms' names already removed).
"""

import json
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from content.models import Partner, SiteSettings
from portfolio.models import Category, Project, ProjectFact, ProjectMedia

SEED = Path(settings.BASE_DIR) / "seed" / "design_seed.json"
DEFAULT_ASSETS = Path(settings.BASE_DIR).parent / "references" / "design" / "assets"


class Command(BaseCommand):
    help = "Load the design's projects, categories and partners (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete existing projects, categories and partners first")
        parser.add_argument("--assets", default=str(DEFAULT_ASSETS), help="Folder with the design's images")

    def handle(self, *args, reset=False, assets=None, **opts):
        assets = Path(assets)
        if not assets.is_dir():
            raise CommandError(f"Image folder not found: {assets}")
        data = json.loads(SEED.read_text(encoding="utf-8"))

        with transaction.atomic():
            if reset:
                Project.objects.all().delete()
                Category.objects.all().delete()
                Partner.objects.all().delete()

            cats = {}
            for c in data["categories"]:
                cats[c["slug"]], _ = Category.objects.get_or_create(slug=c["slug"], defaults=c)

            added = 0
            for p in data["projects"]:
                if Project.objects.filter(slug=p["slug"]).exists():
                    continue
                proj = Project.objects.create(
                    title=p["title"], slug=p["slug"], subtitle=p["subtitle"], location=p["location"],
                    order=p["order"], show_on_home=p["show_on_home"],
                    stage=p.get("stage", ""), stage_label=p.get("stage_label", ""),
                    stage_date=p.get("stage_date", ""), card_text=p.get("card_text", ""),
                )
                proj.categories.set([cats[s] for s in p["categories"]])
                for i, f in enumerate(p["facts"], start=1):
                    ProjectFact.objects.create(project=proj, order=i, **f)
                cover = None
                for i, m in enumerate(p["media"], start=1):
                    row = ProjectMedia(
                        project=proj, order=i, kind=m["kind"], sheet_no=m.get("sheet_no", ""), title=m["title"],
                        alt=m["alt"], placeholder_glyph=m.get("placeholder_glyph", ""),
                        before_alt=m.get("before_alt", ""), before_label=m.get("before_label", ""),
                        after_label=m.get("after_label", ""), before_missing_note=m.get("before_missing_note", ""),
                        in_gallery=m["in_gallery"], in_pile=m["in_pile"],
                    )
                    self._attach(row.image, assets, m.get("image"))
                    self._attach(row.before_image, assets, m.get("before_image"))
                    row.save()
                    if p.get("cover_image") and m.get("image") == p["cover_image"] and cover is None:
                        cover = row
                if cover:
                    proj.cover = cover
                    proj.save(update_fields=["cover"])
                added += 1

            for pt in data["partners"]:
                Partner.objects.get_or_create(name=pt["name"], defaults={"order": pt["order"]})

            s = SiteSettings.load()
            for k, v in data["settings"].items():
                if not getattr(s, k):
                    setattr(s, k, v)
            s.save()

        self.stdout.write(self.style.SUCCESS(
            f"{added} projects added ({Project.objects.count()} total), "
            f"{Category.objects.count()} categories, {Partner.objects.count()} partners"
        ))

    @staticmethod
    def _attach(field, folder, name):
        if not name:
            return
        path = folder / name
        if not path.exists():
            raise CommandError(f"Missing image {path}")
        with path.open("rb") as fh:
            field.save(name, File(fh), save=False)

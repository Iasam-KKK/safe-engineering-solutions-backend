"""Bulk import of projects from a folder (see `manage.py import_projects --help`).

Folder layout (category folders are optional and only for the team's own order):

    import/
      projects.csv                one row per project (export of the "Project list" sheet)
      06 Structures/              any grouping folders...
        Monopole 150 ft - Houston TX/     ...one folder per project, found by its name
          1 Sheets/               PDF sets (every page = one sheet) and/or sheet images
          2 Renders/              render images
          3 Before photos/        existing photos/drawings, matched to a sheet or render by number or name
          4 Videos/               walkthroughs / animations (mp4, mov, ...)
          5 Site photos/          photos of the job on site or finished
          6 3D model/             a .glb exported from Revit: shown first, visitors turn and zoom it
          sheets.csv              optional per-file overrides: file,page,sheet_no,title,alt,in_pile,in_gallery,skip

Subfolders are recognised by a word in their name (sheet, render, before, video, photo, 3d / model);
files dropped straight into the project folder are sorted by type (PDF = sheets, video = videos,
.glb = 3D model, "A101 ..." images = sheets, "before ..." images = before, other images = renders).
A .glb is picked up from any of the project's folders.

Re-running is safe: each sheet/render/video remembers the file (and page) it came from,
so it is skipped (or refreshed with --refresh) instead of being added twice.
"""

import csv
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from django.core.files import File
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.utils.text import slugify

from config.revalidate import batched

from . import media_tools as mt
from .models import Category, Project, ProjectFact, ProjectMedia

# Column titles in the team's "Project list" sheet -> importer field. Matching ignores case,
# punctuation and anything in brackets, so "City, State" and "Show on homepage (yes/no)" work.
HEADER_ALIASES = {
    "foldername": "folder", "folder": "folder", "projectfolder": "folder",
    "title": "title", "titleonwebsite": "title", "projecttitle": "title", "projectname": "title",
    "categories": "categories", "category": "categories",
    "subtitle": "subtitle", "onelinedescription": "subtitle", "shortdescription": "subtitle",
    "location": "location", "citystate": "location", "city": "location",
    "scope": "scope", "codes": "codes", "delivered": "delivered", "description": "description",
    "showonhome": "show_on_home", "showonhomepage": "show_on_home", "homepage": "show_on_home",
    "stage": "stage", "stagelabel": "stage_label", "stagedate": "stage_date", "cardtext": "card_text",
    "redacttext": "redact_text", "texttohideonsheets": "redact_text", "texttohide": "redact_text",
    "redactboxes": "redact_boxes", "areastohide": "redact_boxes", "areastohideonsheets": "redact_boxes",
    "order": "order", "slug": "slug",
}
TEAM_HEADER = [
    "Folder name", "Title on website", "Categories", "One-line description", "City, State", "Scope",
    "Codes", "Delivered", "Description (optional)", "Show on homepage (yes/no)", "Stage (running/built)",
    "Stage label", "Stage date", "Card text", "Text to hide on sheets (separate with |)", "Areas to hide",
    "Order", "Status (team)", "Notes (team)",
]

TEMPLATE_HEADER = [
    "folder", "title", "categories", "subtitle", "location", "scope", "codes", "delivered",
    "description", "show_on_home", "stage", "stage_label", "stage_date", "card_text",
    "redact_text", "redact_boxes", "order",
]
PILE_MAX = 10  # the homepage pile has poses for up to 11 sheets
YES = {"yes", "y", "true", "1", "x"}


@dataclass
class Item:
    """One thing to import: a sheet (maybe one page of a PDF), a render, a video, a 3D model or a 'before' image."""

    path: Path
    role: str              # sheet | render | video | model | before
    key: str               # stable source key, e.g. "garage/sheets/set.pdf#p3"
    page: int = 0          # PDF page, 1-based (0 = not a PDF)
    sheet_no: str = ""
    title: str = ""
    alt: str = ""
    in_pile: bool | None = None
    in_gallery: bool = True
    jpeg: bytes = b""      # rendered page / web image (filled in when processed)


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def say(self, msg):
        self.lines.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)


# ---------- reading the folder ----------
def _files(folder: Path, exts: set[str]) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in exts), key=lambda p: str(p).lower())


def _boxes(text: str) -> list[tuple[float, float, float, float]]:
    """'0.8,0,1,1; 0,0.9,1,1' → [(0.8, 0, 1, 1), (0, 0.9, 1, 1)]. Shorthands: 'right:0.2', 'bottom:0.12'."""
    out = []
    for part in [p.strip() for p in text.split(";") if p.strip()]:
        if ":" in part:
            side, size = part.split(":", 1)
            f = float(size)
            out.append({"right": (1 - f, 0, 1, 1), "bottom": (0, 1 - f, 1, 1), "left": (0, 0, f, 1), "top": (0, 0, 1, f)}[side.strip().lower()])
        else:
            nums = [float(x) for x in part.split(",")]
            if len(nums) != 4:
                raise ValueError(f"redact box needs 4 numbers: {part!r}")
            out.append(tuple(nums))
    return out


def _overrides(folder: Path) -> dict[tuple[str, int], dict]:
    f = folder / "sheets.csv"
    if not f.exists():
        return {}
    with f.open(encoding="utf-8-sig", newline="") as fh:
        return {
            (row.get("file", "").strip().lower(), int(row.get("page") or 0)): {k: (v or "").strip() for k, v in row.items()}
            for row in csv.DictReader(fh)
        }


ROLE_WORDS = [("before", "before"), ("sheet", "sheet"), ("drawing", "sheet"), ("render", "render"),
              ("video", "video"), ("photo", "photo"), ("3d", "model"), ("model", "model")]


def _role_of_dir(name: str) -> str | None:
    n = name.lower()
    return next((role for word, role in ROLE_WORDS if word in n), None)


def _loose_role(p: Path) -> str | None:
    """A file dropped straight into the project folder."""
    ext = p.suffix.lower()
    if ext in mt.PDF_EXT:
        return "sheet"
    if ext in mt.VIDEO_EXT:
        return "video"
    if ext in mt.MODEL_EXT:
        return "model"
    if ext in mt.IMAGE_EXT:
        if p.stem.lower().startswith("before"):
            return "before"
        no, _ = mt.split_name(p.stem)
        return "sheet" if no and not no.startswith(("R", "V")) else "render"
    return None


# ---------- ZIP files (e.g. "2 Renders/Renders-1.zip") ----------
ZIP_MAX_BYTES = 8 * 1024**3   # unpacked size cap, against broken or hostile archives
ZIP_MAX_FILES = 3000
UNZIP_DIR = ".unzipped"       # hidden cache under the import root; reused while the ZIP is unchanged


def unzip(zp: Path, base: Path) -> list[tuple[Path, str]]:
    """The importable files inside a ZIP: [(unpacked path, path inside the ZIP)].

    Unpacked once into <base>/.unzipped/<name>-<size>-<mtime>/ and reused while the ZIP is
    unchanged. Only pictures, videos and PDFs come out; paths can't escape the cache folder;
    Mac and hidden junk is skipped.
    """
    import zipfile

    st = zp.stat()
    dest = base / UNZIP_DIR / f"{slugify(zp.stem)[:50] or 'zip'}-{st.st_size}-{int(st.st_mtime)}"
    keep = mt.IMAGE_EXT | mt.VIDEO_EXT | mt.PDF_EXT | mt.MODEL_EXT
    with zipfile.ZipFile(zp) as zf:
        members = [
            m for m in zf.infolist()
            if not m.is_dir()
            and Path(m.filename).suffix.lower() in keep
            and not any(part.startswith((".", "__MACOSX")) for part in Path(m.filename).parts)
        ]
        if len(members) > ZIP_MAX_FILES or sum(m.file_size for m in members) > ZIP_MAX_BYTES:
            raise ValueError(f"{zp.name}: too large to unpack ({len(members)} files)")
        out = []
        done = dest / ".done"
        for m in members:
            inner = Path(*[p for p in Path(m.filename.replace("\\", "/")).parts if p not in ("..", "/", "")])
            target = (dest / inner).resolve()
            if not str(target).startswith(str(dest.resolve())):
                continue
            if not done.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(m) as src, target.open("wb") as fh:
                    while chunk := src.read(1 << 20):
                        fh.write(chunk)
            out.append((target, inner.as_posix()))
    dest.mkdir(parents=True, exist_ok=True)
    done.touch()
    return sorted(out, key=lambda t: _natural(t[1]))


def _natural(name: str) -> str:
    """Sort key: "Photo" before "Photo (2)", and "SITE_2" before "SITE_14"."""
    stem, dot, ext = name.lower().rpartition(".")
    stem = stem if dot else ext
    if not re.search(r"\(\d+\)\s*$", stem):
        stem += " (1)"
    return re.sub(r"\d+", lambda m: m.group(0).zfill(6), stem)


# words export tools add to file names that mean nothing on the website
EXPORT_NOISE = re.compile(r"\b(final|photo|img|copy|export|edit(?:ed)?|v\d+|\d+)\b", re.I)


def tidy_title(title: str) -> str:
    """'front final_1 - Photo (2)' → 'Front'; 'SITE_14 - Photo' → 'Site'; 'Aerial shot 2' → 'Aerial shot'."""
    t = re.sub(r"\(\d+\)", " ", title.replace("_", " "))
    t = EXPORT_NOISE.sub(" ", t)
    t = re.sub(r"[\s\-–.,]+", " ", t).strip()
    return (t[:1].upper() + t[1:].lower()) if t else ""


def _number_repeats(items: list) -> None:
    """Pictures that tidy to the same title get numbers: Front 1, Front 2..."""
    from collections import Counter

    counts = Counter(it.title for it in items if it.role in ("render", "video", "model") and not it.sheet_no and it.title)
    seen: Counter = Counter()
    for it in items:
        if it.role in ("render", "video", "model") and not it.sheet_no and counts.get(it.title, 0) > 1:
            seen[it.title] += 1
            it.title = f"{it.title} {seen[it.title]}"


def _zip_role(inner: str, path: Path, default: str | None) -> str | None:
    """Role of a file from a ZIP: a role folder inside the ZIP wins, then the folder the ZIP sits in
    (by file type: a video in a renders ZIP is still a video), then the loose-file rules."""
    if path.suffix.lower() in mt.MODEL_EXT:
        return "model"  # a .glb is a 3D model whatever folder it sits in
    for part in reversed(Path(inner).parts[:-1]):  # the folder closest to the file wins
        role = _role_of_dir(part)
        if role:
            return role
    ext = path.suffix.lower()
    if default:
        if ext in mt.VIDEO_EXT:
            return "video"
        if ext in mt.PDF_EXT:
            return "sheet"
        return default
    return _loose_role(path)


def collect(folder: Path, base: Path, report: "Report | None" = None) -> list[Item]:
    """Everything importable in one project folder, in display order:
    3D models, sheets, renders, site photos, videos (then 'before' images, which pair with the others).
    ZIP files are opened and their contents treated as if they were in the folder."""
    keys: dict[Path, str] = {}
    rel = lambda p: keys.get(p) or p.relative_to(base).as_posix()  # noqa: E731
    found: dict[str, list[Path]] = {"model": [], "sheet": [], "render": [], "photo": [], "video": [], "before": []}
    exts = {"sheet": mt.PDF_EXT | mt.IMAGE_EXT, "render": mt.IMAGE_EXT, "photo": mt.IMAGE_EXT,
            "video": mt.VIDEO_EXT, "before": mt.IMAGE_EXT, "model": set()}

    def add_zip(zp: Path, default: str | None):
        try:
            members = unzip(zp, base)
        except Exception as exc:  # a broken ZIP shouldn't stop the rest of the import
            if report:
                report.warn(f"{zp.relative_to(base).as_posix()}: couldn't open the ZIP ({exc})")
            return
        for path, inner in members:
            role = _zip_role(inner, path, default)
            if role:
                keys[path] = f"{zp.relative_to(base).as_posix()}!/{inner}"  # stable across re-runs
                found[role].append(path)

    for sub in sorted((d for d in folder.iterdir() if d.is_dir()), key=lambda d: d.name.lower()):
        role = _role_of_dir(sub.name)
        # a .glb is a 3D model in whatever folder it sits ("3D model", "Renders", "Revit export"...)
        found["model"] += _files(sub, mt.MODEL_EXT)
        if role:
            found[role] += _files(sub, exts[role])
            for zp in _files(sub, {".zip"}):
                add_zip(zp, None if role == "model" else role)
    for p in sorted((f for f in folder.iterdir() if f.is_file()), key=lambda f: f.name.lower()):
        if p.suffix.lower() == ".zip":
            add_zip(p, None)
            continue
        role = _loose_role(p)
        if role:
            found[role].append(p)

    items: list[Item] = []
    for role in ("model", "sheet", "render", "photo", "video", "before"):
        for p in found[role]:
            if role == "sheet" and p.suffix.lower() in mt.PDF_EXT:
                items.append(Item(p, "sheet", rel(p)))  # expanded into pages later
                continue
            if role == "model":
                # export names ("j1065 floating house R3") mean nothing to visitors
                items.append(Item(p, "model", rel(p), title="3D model", in_pile=False))
                continue
            stem = p.stem
            if role == "before" and stem.lower().startswith("before"):
                stem = stem[len("before"):].lstrip(" _-")
            no, title = mt.split_name(stem)
            if not no and role != "sheet":
                title = tidy_title(title) or {"video": "Walkthrough", "before": ""}.get(role, "View")
            # site photos show like renders, but stay out of the homepage drawing pile
            items.append(Item(p, "render" if role == "photo" else role, rel(p), sheet_no=no, title=title,
                              in_pile=False if role in ("photo", "video") else None))
    _number_repeats(items)
    return items


def find_folder(root: Path, name: str) -> list[Path]:
    """The project folder: a path relative to root, or a folder of that name anywhere below it."""
    direct = root / name
    if direct.is_dir():
        return [direct]
    want = name.strip().lower()
    return [d for d in root.rglob("*") if d.is_dir() and d.name.strip().lower() == want]


# ---------- processing ----------
def expand_pdfs(items: list[Item], redact_text: list[str], boxes, render: bool, report: Report, label: str,
                auto_hide: bool = False) -> list[Item]:
    """Replace each PDF item by one item per page (rendered unless render=False)."""
    out = []
    for it in items:
        if it.path.suffix.lower() not in mt.PDF_EXT:
            out.append(it)
            continue
        try:
            pages, found = mt.pdf_to_pages(it.path, redact_text, boxes, render=render, auto_hide=auto_hide)
        except Exception as exc:
            report.warn(f"{label}: could not read {it.path.name}: {exc}")
            continue
        auto = sorted(k[6:] for k in found if k.startswith("auto: "))
        if auto:
            report.warn(f"{label}: hidden automatically in {it.path.name} (check it was private): " + "; ".join(repr(a) for a in auto[:12])
                        + (f" and {len(auto) - 12} more" if len(auto) > 12 else ""))
        for s, n in found.items():
            if not n and not s.startswith("auto: "):
                report.warn(f"{label}: redact text {s!r} not found in {it.path.name} (check spelling, or the PDF may be a scan)")
        scanned = [pg.page for pg in pages if not pg.has_text]
        if scanned:
            report.warn(
                f"{label}: {it.path.name} pages {scanned} have no text layer (scanned?): sheet numbers can't be read "
                "and text redaction can't work there. Check those pages by eye."
            )
        for pg in pages:
            out.append(Item(it.path, "sheet", f"{it.key}#p{pg.page}", page=pg.page, sheet_no=pg.sheet_no, title=pg.title, jpeg=pg.jpeg))
    return out


def apply_overrides(items: list[Item], folder: Path) -> list[Item]:
    ov = _overrides(folder)
    kept = []
    for it in items:
        row = ov.get((it.path.name.lower(), it.page)) or (ov.get((it.path.name.lower(), 0)) if not it.page else None)
        if row:
            if row.get("skip", "").lower() in YES:
                continue
            it.sheet_no = row.get("sheet_no") or it.sheet_no
            it.title = row.get("title") or it.title
            it.alt = row.get("alt") or it.alt
            if row.get("in_pile"):
                it.in_pile = row["in_pile"].lower() in YES
            if row.get("in_gallery"):
                it.in_gallery = row["in_gallery"].lower() in YES
        kept.append(it)
    return kept


def _default_title(it: Item) -> str:
    if it.title:
        return it.title[:80]
    if it.role == "sheet":
        return f"Sheet {it.sheet_no}" if it.sheet_no else f"Sheet {it.page or 1}"
    return {"render": "Render", "video": "Walkthrough", "model": "3D model"}.get(it.role, "View")


def _default_alt(it: Item, project: Project, title: str) -> str:
    if it.alt:
        return it.alt[:200]
    if it.role == "model":
        return f"3D model of {project.title} that you can turn and zoom"[:200]
    what = {"sheet": "Drawing sheet", "render": "Render", "video": "Video"}.get(it.role, "Image")
    no = f" {it.sheet_no}" if it.sheet_no and it.role == "sheet" else ""
    return f"{what}{no}, {title.lower()}, {project.title}"[:200]


def _store_video(media: ProjectMedia, src: Path, transcode: bool):
    """Compress to a web MP4 (unless it's already an .mp4 and transcode=False), then make the poster."""
    with tempfile.TemporaryDirectory() as tmp:
        out = src
        if transcode or src.suffix.lower() not in {".mp4", ".webm"}:
            out = Path(tmp) / (src.stem + ".mp4")
            mt.transcode_video(src, out)
        with out.open("rb") as fh:
            if media.video:  # re-processing: don't leave the old file behind
                media.video.delete(save=False)
            media.video.save(slugify(src.stem)[:80] + out.suffix.lower(), ContentFile(fh.read()), save=False)
        poster = mt.video_poster(out)
    if media.image:
        media.image.delete(save=False)
    media.image.save(slugify(src.stem)[:80] + "-poster.jpg", ContentFile(poster), save=False)


def _store_model(media: ProjectMedia, src: Path, report: Report, label: str):
    """Copy the .glb as it is (compressing it is the exporter's job). Its poster, if the admin
    picked one, stays."""
    if not mt.is_glb(src):
        raise ValueError("not a binary glTF file (export it from Revit as .glb, not .gltf)")
    size = src.stat().st_size
    if size > mt.MODEL_WARN_BYTES:
        report.warn(f"{label}: 3D model {src.name} is {size // 1024**2} MB, slow on phones; "
                    "compress it (gltf-transform optimize ... --compress draco) and upload it again")
    if media.model:  # re-processing: don't leave the old file behind
        media.model.delete(save=False)
    with src.open("rb") as fh:
        media.model.save((slugify(src.stem)[:80] or "model") + ".glb", File(fh), save=False)


def ingest(project: Project, items: list[Item], *, refresh=False, transcode_videos=True, report: Report, label: str) -> dict:
    """Create (or refresh) ProjectMedia rows for the items. Returns counts."""
    counts = {"sheet": 0, "render": 0, "video": 0, "model": 0, "pairs": 0, "skipped": 0}
    existing = {m.source: m for m in project.media.exclude(source="")}
    next_order = max([m.order for m in project.media.all()] or [0]) + 1
    pile_used = sum(1 for m in project.media.all() if m.in_pile)
    befores = [it for it in items if it.role == "before"]

    for it in items:
        if it.role == "before":
            continue
        m = existing.get(it.key)
        if m and not refresh:
            counts["skipped"] += 1
            continue
        title = _default_title(it)
        if m is None:
            m = ProjectMedia(project=project, order=next_order, source=it.key)
            next_order += 1
        m.kind = {"sheet": ProjectMedia.Kind.SHEET, "render": ProjectMedia.Kind.RENDER, "video": ProjectMedia.Kind.VIDEO,
                  "model": ProjectMedia.Kind.MODEL}[it.role]
        m.sheet_no = it.sheet_no[:12]
        m.title = title
        m.alt = _default_alt(it, project, title)
        m.in_gallery = it.in_gallery
        if it.in_pile is not None:
            m.in_pile = it.in_pile
        elif m.pk is None:
            m.in_pile = it.role not in ("video", "model") and pile_used < PILE_MAX
            pile_used += m.in_pile
        if it.role == "model":
            m.in_pile = False
            try:
                _store_model(m, it.path, report, label)
            except Exception as exc:
                report.warn(f"{label}: 3D model {it.path.name} failed: {exc}")
                continue
        elif it.role == "video":
            m.in_pile = False
            try:
                _store_video(m, it.path, transcode_videos)
            except Exception as exc:
                report.warn(f"{label}: video {it.path.name} failed: {exc}")
                continue
        else:
            jpeg = it.jpeg or mt.image_file_to_jpeg(it.path)
            name = slugify(f"{it.path.stem}-p{it.page}" if it.page else it.path.stem)[:80] or "sheet"
            if m.image:  # re-processing: replace, don't pile up copies
                m.image.delete(save=False)
            m.image.save(name + ".jpg", ContentFile(jpeg), save=False)
        m.save()
        counts[it.role] += 1

    # "before" images: match by sheet/render number, else by title
    if befores:
        media = list(project.media.all())
        for b in befores:
            target = next((m for m in media if b.sheet_no and m.sheet_no.upper() == b.sheet_no.upper()), None) or next(
                (m for m in media if b.title and m.title.lower() == b.title.lower()), None
            )
            if not target:
                report.warn(f"{label}: before image {b.path.name} matches no sheet or render (name it like the sheet, e.g. 'A101.jpg' or 'R01 Front.jpg')")
                continue
            if target.before_image and not refresh:
                continue
            target.before_image.save(slugify(b.path.stem)[:80] + "-before.jpg", ContentFile(mt.image_file_to_jpeg(b.path)), save=False)
            target.before_alt = b.alt or f"Existing condition, {target.title.lower()}, {project.title}"[:200]
            target.before_label = target.before_label or "EXISTING"
            target.after_label = target.after_label or "PROPOSED"
            target.save()
            counts["pairs"] += 1
    return counts


# ---------- the whole import ----------
def _categories() -> dict[str, Category]:
    out = {}
    for c in Category.objects.all():
        out[c.slug.lower()] = c
        out[c.name.lower()] = c
        out[c.tab_label.lower()] = c
    return out


def project_list(root: Path) -> Path:
    """projects.csv, or the sheet as Google Drive downloads it ("00 Project list - Untitled.csv")."""
    f = root / "projects.csv"
    if f.exists():
        return f
    found = sorted(root.glob("*roject list*.csv"))
    if found:
        return found[0]
    raise FileNotFoundError(
        f"No project list in {root}: download '00 Project list' as CSV (File > Download > Comma-separated values) into it"
    )


def read_rows(root: Path) -> list[dict]:
    f = project_list(root)
    def key(h: str) -> str | None:
        norm = re.sub(r"[^a-z0-9]", "", re.sub(r"\(.*?\)", "", h.lower()))
        return HEADER_ALIASES.get(norm)

    with f.open(encoding="utf-8-sig", newline="") as fh:
        rows = []
        for row in csv.DictReader(fh):
            out = {}
            for k, v in row.items():
                field = key(k) if k else None
                if field:  # team-only columns (status, notes...) are ignored
                    out[field] = (v or "").strip()
            rows.append(out)
    return [r for r in rows if r.get("folder") or r.get("title")]


def validate(root: Path, rows: list[dict]) -> list[str]:
    """Problems that stop the import before anything is written."""
    cats = _categories()
    errors = []
    for n, r in enumerate(rows, start=2):  # row 1 is the header
        where = f"projects.csv row {n}"
        for col in ("folder", "title", "categories"):
            if not r.get(col):
                errors.append(f"{where}: '{col}' is empty")
        if r.get("folder"):
            hits = find_folder(root, r["folder"])
            if not hits:
                errors.append(f"{where}: no folder named '{r['folder']}' in the import folder")
            elif len(hits) > 1:
                errors.append(f"{where}: {len(hits)} folders are named '{r['folder']}'; give each project its own name")
        for c in [c.strip() for c in r.get("categories", "").split(";") if c.strip()]:
            if c.lower() not in cats:
                errors.append(f"{where}: unknown category '{c}' (use one of: {', '.join(sorted({x.slug for x in cats.values()}))})")
        if r.get("stage") and r["stage"].lower() not in {"running", "built"}:
            errors.append(f"{where}: stage must be 'running', 'built' or empty")
        try:
            _boxes(r.get("redact_boxes", ""))
        except (ValueError, KeyError) as exc:
            errors.append(f"{where}: redact_boxes: {exc}")
    return errors


def _apply_project_fields(p: Project, r: dict, cats: dict[str, Category], creating: bool, update: bool):
    if creating or update:
        p.title = r["title"][:120]
        p.subtitle = (r.get("subtitle") or p.subtitle)[:200]
        p.location = (r.get("location") or p.location)[:120]
        p.description = r.get("description") or p.description
        p.show_on_home = r.get("show_on_home", "").lower() in YES
        p.stage = r.get("stage", "").lower()
        p.stage_label = r.get("stage_label", "")[:40]
        p.stage_date = r.get("stage_date", "")[:40]
        p.card_text = r.get("card_text", "")[:200]
        if r.get("order"):
            p.order = int(r["order"])


# ---------- scan mode: the team's simple layout, no spreadsheet needed ----------
# 01 Projects / <kind> / [<type>] / <project folder or loose PDF>
CATEGORY_WORDS = [  # first match wins
    (("mep", "plumbing", "electrical", "mechanical"), "mep"),
    (("adu", "garage"), "adu-and-garage"),
    (("modular", "container", "floating", "green house", "greenhouse"), "modular-homes"),
    (("structur", "steel", "telecom", "solar"), "structures"),
    (("civil", "land", "storm", "drainage", "site planning"), "civil"),
    (("commercial",), "new-commercial"),
    (("new home", "residential"), "new-residential"),
    (("modification", "extension", "renovation", "addition", "remodel"), "home-modification"),
]
GROUP_FOLDERS = {
    "telecom steel structures", "solar panel stands", "other structures", "container homes", "floating homes",
    "green houses", "greenhouses", "land and site planning", "storm and drainage",
}
JOB_PREFIX = re.compile(r"^(?:j|job)\s*#?\s*\d{2,6}[a-z]?\b[\s._-]*", re.I)
REVISION = re.compile(r"[\s._-]*\b(?:r|rev|revision)\s*\.?\s*-?\s*\d+[a-z]?$", re.I)
CITY_ST = re.compile(r"^(?P<city>.+?)[,\s]+(?P<st>[A-Z]{2})$")


def category_for(name: str) -> str | None:
    n = name.lower()
    return next((slug for words, slug in CATEGORY_WORDS if any(w in n for w in words)), None)


def clean_title(stem: str) -> tuple[str, str]:
    """'j 1065 floating house R3' -> ('Floating house', ''); 'Monopole 150 ft - Houston TX' -> ('Monopole 150 ft', 'Houston, TX')."""
    s = re.sub(r"_+", " ", stem).strip()
    location = ""
    if " - " in s:
        head, tail = s.rsplit(" - ", 1)
        m = CITY_ST.match(tail.strip())
        if m:
            s, location = head, f"{m.group('city').strip()}, {m.group('st')}"
    s = REVISION.sub("", JOB_PREFIX.sub("", s))
    s = re.sub(r"\s{2,}", " ", s).strip(" -")
    return (s[:1].upper() + s[1:]) if s else stem, location


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\.(pdf|mp4|mov)$", "", name.strip().lower()))


def _is_project_dir(d: Path) -> bool:
    if d.name.lower() in GROUP_FOLDERS:
        return False
    for c in d.iterdir():
        if c.is_dir() and _role_of_dir(c.name):
            return True
        if c.is_file() and (_loose_role(c) or c.suffix.lower() == ".zip"):
            return True
    return False


def projects_root(root: Path) -> Path:
    """The folder holding the kinds of project: root itself, or its "Projects" folder."""
    for d in sorted(x for x in root.iterdir() if x.is_dir()):
        if "project" in d.name.lower() and not d.name.startswith(("_", ".")):
            return d
    return root


def read_details(root: Path) -> dict[str, dict]:
    """The optional "Project details" sheet (downloaded as CSV), by project folder or PDF name."""
    files = sorted(root.rglob("*roject details*.csv"))
    if not files:
        return {}
    aliases = {**HEADER_ALIASES, "project": "folder", "projectfolderorpdfname": "folder", "cityandstate": "location",
               "onelineabouttheproject": "subtitle", "namesandaddressestohideonthedrawings": "redact_text",
               "namesandaddressestohide": "redact_text"}
    out = {}
    with files[0].open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            rec = {}
            for k, v in row.items():
                if not k:
                    continue
                norm = re.sub(r"[^a-z0-9]", "", re.sub(r"\(.*?\)", "", k.lower()))
                field = aliases.get(norm)
                if field:
                    rec[field] = (v or "").strip()
            if rec.get("folder"):
                out[_norm(rec["folder"])] = rec
    return out


def discover(root: Path, report: Report) -> list[dict]:
    """Every project in the layout, as rows like the project list's (plus _path and _key)."""
    proot = projects_root(root)
    details = read_details(root)
    rows: list[dict] = []

    def add(path: Path, cat: str, group: str):
        title, location = clean_title(path.stem if path.is_file() else path.name)
        # no description by default: the category is already on the card ("Project details" can add one)
        row = {"folder": path.name, "title": title, "categories": cat, "location": location,
               "subtitle": "", "_group": group, "_path": path, "_key": path.relative_to(root).as_posix()}
        extra = details.get(_norm(path.name)) or details.get(_norm(path.stem))
        if extra:
            row.update({k: v for k, v in extra.items() if v and k != "folder"})
        rows.append(row)

    def walk(d: Path, cat: str, group: str):
        for f in sorted(x for x in d.iterdir() if x.is_file()):
            if f.suffix.lower() in mt.PDF_EXT:
                add(f, cat, group)
            elif f.suffix.lower() in mt.IMAGE_EXT | mt.VIDEO_EXT | mt.MODEL_EXT:
                report.warn(f"{f.relative_to(root)}: pictures, videos and 3D models need a project folder; this file was skipped")
        for sub in sorted(x for x in d.iterdir() if x.is_dir() and not x.name.startswith(("_", "."))):
            if _is_project_dir(sub):
                add(sub, cat, group)
            else:
                walk(sub, cat, sub.name)

    for kind in sorted(x for x in proot.iterdir() if x.is_dir() and not x.name.startswith(("_", "."))):
        cat = category_for(kind.name)
        if not cat:
            report.warn(f"{kind.name}: not a known kind of project, skipped (rename it like the other folders)")
            continue
        label = Category.objects.filter(slug=cat).values_list("name", flat=True).first() or kind.name
        walk(kind, cat, label)
    return _merge_same_project(rows, report)


JOB_NO = re.compile(r"^(?:j|job)\s*#?\s*(\d{2,6}[a-z]?)\b", re.I)


def _job(stem: str) -> str:
    m = JOB_NO.match(re.sub(r"_+", " ", stem).strip())
    return m.group(1).lower() if m else ""


def _revision(stem: str) -> int:
    m = REVISION.search(re.sub(r"_+", " ", stem).strip())
    return int(re.sub(r"\D", "", m.group(0)) or 0) if m else 0


def _merge_same_project(rows: list[dict], report: Report) -> list[dict]:
    """Rows in the same folder that name the same project:

    - revisions of one set ("j 1065 floating house R2.pdf", "... R3.pdf"): only the newest is used;
    - a different job with the same name ("j 1102 floating house R1.pdf") stays its own project;
    - a project folder next to a loose PDF set of that project ("Floating house - Miami FL/" and
      "j 1065 floating house R3.pdf") become one project: the folder's pictures and videos, the PDF's sheets.
    """
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((r["_path"].parent, slugify(r["title"])), []).append(r)
    out: list[dict] = []
    for rs in groups.values():
        if len(rs) == 1:
            out += rs
            continue
        dirs = [r for r in rs if not r["_path"].is_file()]
        by_job: dict[str, list[dict]] = {}
        for r in rs:
            if r["_path"].is_file():
                by_job.setdefault(_job(r["_path"].stem), []).append(r)
        sets = []
        for same in by_job.values():
            newest = max(same, key=lambda r: (_revision(r["_path"].stem), r["_path"].stat().st_mtime))
            for r in same:
                if r is not newest:
                    report.warn(f"{r['_key']}: older set of {r['title']}, skipped (using {newest['_path'].name})")
            sets.append(newest)
        if dirs and len(sets) == 1:
            keep, pdf = dirs[0], sets[0]
            keep["_extra"] = [(pdf["_path"], pdf["_key"])]
            report.say(f"{keep['title']}: the folder {keep['folder']} and {pdf['_path'].name} imported as one project")
            out += [keep] + dirs[1:]
            continue
        if dirs and len(sets) > 1:
            report.warn(f"{dirs[0]['_key']}: several PDF sets are named like this folder; "
                        "move the right PDF into the folder so they import together")
        out += sets + dirs  # anything still sharing a name gets its own page (-2, -3)
    return out


def _unique_slug(row: dict, used: set[str]) -> str:
    """Same name as another project (in this run, or imported earlier from another folder): add -2, -3..."""
    base = slugify(row.get("slug") or row["title"])[:130] or "project"
    slug, n = base, 1
    while True:
        clash = slug in used
        if not clash:
            existing = Project.objects.filter(slug=slug).first()
            if existing:
                sources = [m.source for m in existing.media.exclude(source="")]
                keys = [row["_key"]] + [k for _, k in row.get("_extra", [])]
                clash = bool(sources) and not any(src.startswith(k) for src in sources for k in keys)
        if not clash:
            used.add(slug)
            return slug
        n += 1
        slug = f"{base}-{n}"


def run_import(root: Path, *, dry_run=False, publish=False, update=False, refresh=False, transcode_videos=True,
               auto_hide=True, out=print) -> Report:
    report = Report()
    try:
        rows = read_rows(root)
        errors = validate(root, rows)
    except FileNotFoundError:
        rows = discover(root, report)  # the simple layout: no project list, folders say it all
        errors = []
        out(f"Found {len(rows)} project(s) in the folders.")
    if errors:
        for e in errors:
            report.warn(e)
        report.say("Nothing imported: fix the problems above first.")
        return report
    cats = _categories()
    used: set[str] = set()

    with batched():
        for r in rows:
            label = r["title"]
            if "_path" in r:
                path = r["_path"]
                slug = _unique_slug(r, used)
                if path.is_file():
                    folder = path.parent
                    items = [Item(path, "sheet", r["_key"])]
                else:
                    folder = path
                    # plus a loose PDF set of the same project sitting next to the folder
                    items = collect(folder, root, report) + [Item(x, "sheet", k) for x, k in r.get("_extra", [])]
            else:
                folder = find_folder(root, r["folder"])[0]
                slug = slugify(r.get("slug") or r["title"])[:140]
                items = collect(folder, root, report)
            redact_text = [s.strip() for s in re.split(r"[|;]", r.get("redact_text", "")) if s.strip()]
            boxes = _boxes(r.get("redact_boxes", ""))
            items = expand_pdfs(items, redact_text, boxes, render=not dry_run, report=report, label=label,
                                auto_hide=auto_hide)
            items = apply_overrides(items, folder)
            n = {k: sum(1 for i in items if i.role == k) for k in ("sheet", "render", "video", "model", "before")}
            nos = [i.sheet_no for i in items if i.role == "sheet" and i.sheet_no]
            missing_no = sum(1 for i in items if i.role == "sheet" and not i.sheet_no)
            exists = Project.objects.filter(slug=slug).first()
            out(
                f"{'UPDATE' if exists else 'NEW   '} {label} ({slug}): {n['sheet']} sheets, {n['render']} renders, "
                f"{n['video']} videos, {n['model']} 3D models, {n['before']} before images"
                + (f"\n         sheet numbers: {', '.join(nos)}" if nos else "")
                + (f"\n         {missing_no} sheets without a readable number (add them in sheets.csv or the admin)" if missing_no else "")
            )
            if not items:
                # e.g. a folder the team has only just started: no empty project on the site meanwhile
                report.warn(f"{label}: nothing to import yet in {folder.name} (drawings, pictures or videos)")
                continue
            if dry_run:
                continue
            with transaction.atomic():
                creating = exists is None
                p = exists or Project(slug=slug, is_published=publish)
                _apply_project_fields(p, r, cats, creating, update)
                if publish:
                    p.is_published = True
                p.save()
                if creating or update:
                    p.categories.set({cats[c.strip().lower()] for c in r["categories"].split(";") if c.strip().lower() in cats})
                    facts = [("LOCATION", r.get("location")), ("SCOPE", r.get("scope")), ("CODES", r.get("codes")), ("DELIVERED", r.get("delivered"))]
                    if update:
                        p.facts.filter(label__in=[f[0] for f in facts]).delete()
                    for i, (label_, value) in enumerate(facts, start=1):
                        if value:
                            ProjectFact.objects.create(project=p, label=label_, value=value[:200], order=i)
                counts = ingest(p, items, refresh=refresh, transcode_videos=transcode_videos, report=report, label=label)
                if p.media.exclude(image="").exists():
                    gone, _ = p.media.filter(image="").exclude(placeholder_glyph="").delete()
                    if gone:
                        report.say(f"{label}: {gone} placeholder sheet(s) replaced by the real ones")
                # renders that arrive after the PDF still lead: 3D models, pairs, renders, videos, then sheets
                if any(counts[k] for k in ("sheet", "render", "video", "model", "pairs")):
                    p.reorder_media()
            report.say(
                f"{label}: {counts['sheet']} sheets, {counts['render']} renders, {counts['video']} videos, "
                f"{counts['model']} 3D models, {counts['pairs']} before/after pairs added"
                + (f", {counts['skipped']} already there" if counts["skipped"] else "")
                + ("" if p.is_published else "  [draft: tick Published in the admin after checking]")
            )
    return report


# ---------- admin upload box ----------
def ingest_uploads(project: Project, uploads: list[UploadedFile], as_kind: str, report: Report) -> dict:
    """Files dropped into a project's admin page: PDFs become sheets, images sheets/renders, videos videos,
    .glb files 3D models."""
    with tempfile.TemporaryDirectory() as tmp, batched():
        base = Path(tmp)
        items = []
        for up in uploads:
            path = base / Path(up.name).name
            with path.open("wb") as fh:
                for chunk in up.chunks():
                    fh.write(chunk)
            ext = path.suffix.lower()
            no, title = mt.split_name(path.stem)
            key = f"upload/{path.name}"
            if ext in mt.PDF_EXT:
                items.append(Item(path, "sheet", key))
            elif ext in mt.VIDEO_EXT:
                items.append(Item(path, "video", key, sheet_no=no, title=title, in_pile=False))
            elif ext in mt.MODEL_EXT:
                items.append(Item(path, "model", key, title="3D model", in_pile=False))
            elif ext in mt.IMAGE_EXT:
                role = as_kind if as_kind in {"sheet", "render", "before"} else ("sheet" if no and not no.startswith("R") else "render")
                items.append(Item(path, role, key, sheet_no=no, title=title))
            else:
                report.warn(f"{up.name}: not a PDF, image, video or .glb 3D model, skipped")
        items = expand_pdfs(items, [], [], render=True, report=report, label=project.title)
        # admin uploads are already web-sized videos most of the time; only odd formats get converted
        counts = ingest(project, items, refresh=True, transcode_videos=False, report=report, label=project.title)
    project.reorder_media()
    return counts

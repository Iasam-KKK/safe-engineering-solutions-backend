"""Team, partners, reviews and contact details from the team's Drive folder (mirrored locally).

    02 About us/Team.csv            + Team photos/<Name>.jpg
    03 Partners/Partner list.csv    + Logos/<Firm name>.png
    04 Reviews/Reviews.csv
    05 Company and contact/Contact details.txt     "Label: value" lines

Everything is matched by name, so running it again only updates what changed.
"""

import csv
import re
from pathlib import Path

from django.core.files.base import ContentFile
from PIL import Image

from portfolio import media_tools as mt

from .models import Partner, Review, SiteSettings, TeamMember

YES = {"yes", "y", "true", "1", "x"}


def _key(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\(.*?\)", "", header.lower()))


def _rows(path: Path, aliases: dict[str, str]) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        out = []
        for row in csv.DictReader(fh):
            rec = {}
            for k, v in row.items():
                if k and _key(k) in aliases:
                    rec[aliases[_key(k)]] = (v or "").strip()
            out.append(rec)
        return out


def _find(root: Path, folder_word: str, name_glob: str) -> Path | None:
    for d in sorted(root.iterdir()):
        if d.is_dir() and folder_word in d.name.lower():
            hits = sorted(d.rglob(name_glob))
            return hits[0] if hits else None
    return None


# words that don't help tell firms apart: "KMW Properties LLC" is "KMW Properties"
NAME_NOISE = {"llc", "pllc", "inc", "ltd", "lp", "co", "corp", "company", "the", "and", "of", "by"}


def _words(name: str) -> list[str]:
    """Lower-case words without punctuation, company suffixes or plural s ("Rocky Mountains Prefab" ≈ "Rocky Mountain Prefabs")."""
    out = []
    for w in re.sub(r"[^a-z0-9]+", " ", name.lower()).split():
        if w in NAME_NOISE:
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def _label(p: Path) -> str:
    """A picture's name without its extension. Drive often has none ("KMW Properties LLC")."""
    return p.stem if p.suffix.lower() in mt.IMAGE_EXT else p.name


def _is_image(p: Path) -> bool:
    if p.suffix.lower() in mt.IMAGE_EXT:
        return True
    try:  # no or unknown extension: ask the file itself
        with Image.open(p) as img:
            img.verify()
        return True
    except Exception:
        return False


def _match_images(folder: Path | None, names: list[str]) -> tuple[dict[str, Path], list[Path], list[str]]:
    """Pair each name with its picture in the folder.

    First the same name once tidied (capitals, spaces, "LLC", plurals: "Black well group" = "Blackwell group"),
    then a picture whose name holds all of the name's words, or the other way round, when only one does
    ("Nexus Engineering" → "NEXUS Design and Engineering").
    Returns {name: picture}, the pictures nobody claimed, and a note for each loose match to check.
    """
    if not folder or not folder.is_dir():
        return {}, [], []
    files = [p for p in sorted(folder.iterdir()) if p.is_file() and not p.name.startswith(".") and _is_image(p)]
    fw = {p: _words(_label(p)) for p in files}
    out: dict[str, Path] = {}
    free = list(files)
    for n in names:
        key = "".join(_words(n))
        hit = next((p for p in free if key and "".join(fw[p]) == key), None)
        if hit:
            out[n] = hit
            free.remove(hit)
    notes = []
    for n in names:
        w = set(_words(n))
        if n in out or not w:
            continue
        cands = [p for p in free if fw[p] and (w <= set(fw[p]) or set(fw[p]) <= w)]
        if len(cands) == 1:
            out[n] = cands[0]
            free.remove(cands[0])
            notes.append(f"picture '{cands[0].name}' used for '{n}'")
    return out, free, notes


def _set_image(field, path: Path, max_side: int):
    """Store a resized copy; skip when the same source file is already there."""
    from django.utils.text import slugify

    name = f"{slugify(path.stem)[:60]}-{int(path.stat().st_mtime)}.jpg"
    if field and Path(field.name).name.startswith(name[:-4]):
        return False
    with Image.open(path) as img:
        field.save(name, ContentFile(mt.to_web_jpeg(img, max_side)), save=False)
    return True


def _set_logo(field, path: Path) -> bool:
    """Store the logo as a one-colour mark (see media_tools.logo_mark); skip when this file already is."""
    from django.utils.text import slugify

    name = f"{slugify(_label(path))[:60]}-{int(path.stat().st_mtime)}-mark{mt.MARK_VERSION}.png"
    if field and Path(field.name).name.startswith(name[:-4]):
        return False
    with Image.open(path) as img:
        field.save(name, ContentFile(mt.logo_mark(img)), save=False)
    return True


def sync_team(root: Path, say) -> int:
    sheet = _find(root, "about", "Team*.csv")
    if not sheet:
        return 0
    photos = next((d for d in sheet.parent.iterdir() if d.is_dir() and "photo" in d.name.lower()), None)
    aliases = {"name": "name", "nameasonthewebsite": "name", "jobtitle": "role", "role": "role",
               "degreesandlicenses": "credentials", "credentials": "credentials", "onelineaboutthem": "bio",
               "showonwebsite": "show"}
    rows = [r for r in _rows(sheet, aliases) if r.get("name")]
    pics, spare, notes = _match_images(photos, [r["name"] for r in rows])
    kept = []
    for i, r in enumerate(rows, start=1):
        m = TeamMember.objects.filter(name__iexact=r["name"]).first() or TeamMember(name=r["name"], role=r.get("role", ""))
        m.name = r["name"]
        m.role = r.get("role") or m.role
        m.credentials = r.get("credentials", "")
        m.bio = r.get("bio", "")
        m.is_active = r.get("show", "yes").lower() in YES
        m.order = i
        if r["name"] in pics:
            _set_image(m.photo, pics[r["name"]], 900)
        m.save()
        kept.append(m.pk)
    for note in notes:
        say(f"CHECK: Team: {note}")
    if spare:
        say("CHECK: Team: photos with no matching name in the Team sheet: " + ", ".join(p.name for p in spare))
    # people taken off the sheet (or renamed there) are hidden, not deleted
    gone = list(TeamMember.objects.filter(is_active=True).exclude(pk__in=kept))
    for m in gone:
        m.is_active = False
        m.save()
    if gone:
        say("Team: hidden, no longer in the Team sheet: " + ", ".join(m.name for m in gone))
    say(f"Team: {len(rows)} people, {len(pics)} photos")
    return len(rows)


def sync_partners(root: Path, say) -> int:
    sheet = _find(root, "partner", "Partner list*.csv")
    if not sheet:
        return 0
    logos = next((d for d in sheet.parent.iterdir() if d.is_dir() and "logo" in d.name.lower()), None)
    aliases = {"firmname": "name", "name": "name", "theirwebsite": "website", "website": "website",
               "showonwebsite": "show"}
    rows = [r for r in _rows(sheet, aliases) if r.get("name")]
    pics, spare, notes = _match_images(logos, [r["name"] for r in rows])
    kept = []
    for i, r in enumerate(rows, start=1):
        p = Partner.objects.filter(name__iexact=r["name"]).first() or Partner(name=r["name"])
        p.name = r["name"]
        website = r.get("website", "")
        p.website = website if website.startswith("http") else (f"https://{website}" if website else "")
        p.is_active = r.get("show", "yes").lower() in YES
        p.order = i
        if r["name"] in pics:
            _set_logo(p.logo, pics[r["name"]])
        p.save()
        kept.append(p.pk)
    # a logo with no row in the list: the team clearly wants that firm shown, so add it
    for k, f in enumerate(spare, start=len(rows) + 1):
        name = re.sub(r"\s+", " ", _label(f)).strip()
        p = Partner.objects.filter(name__iexact=name).first() or Partner(name=name)
        p.is_active, p.order = True, k
        _set_logo(p.logo, f)
        p.save()
        kept.append(p.pk)
        say(f"CHECK: Partners: added '{name}' from its logo (it isn't in the Partner list)")
    # firms taken off the list (or renamed) are hidden, not deleted, so admin edits survive
    gone = list(Partner.objects.filter(is_active=True).exclude(pk__in=kept))
    for p in gone:
        p.is_active = False
        p.save()
    if gone:
        say("Partners: hidden, no longer in the Partner list: " + ", ".join(p.name for p in gone))
    for note in notes:
        say(f"CHECK: Partners: {note}")
    say(f"Partners: {len(kept)} firms, {len(pics) + len(spare)} logos")
    return len(kept)


def sync_reviews(root: Path, say) -> int:
    sheet = _find(root, "review", "Reviews*.csv")
    if not sheet:
        return 0
    aliases = {"client": "author", "country": "country", "stars": "rating", "review": "text",
               "kindofproject": "kind", "monthandyear": "date", "linktothereview": "url",
               "canweshowtheirfullname": "full_name_ok", "showonhomepage": "show"}
    n = 0
    for i, r in enumerate(_rows(sheet, aliases), start=1):
        text = r.get("text", "")
        if not text or not r.get("author"):
            continue
        rating = int(re.sub(r"\D", "", r.get("rating", "")) or 5)
        rv, _ = Review.objects.get_or_create(author=r["author"], text=text, defaults={"rating": min(5, max(1, rating))})
        rv.country = r.get("country", "")
        rv.rating = min(5, max(1, rating))
        rv.source_url = r.get("url", "") if r.get("url", "").startswith("http") else ""
        rv.is_featured = r.get("show", "yes").lower() in YES
        rv.order = i
        rv.save()
        n += 1
    say(f"Reviews: {n}")
    return n


# "Label: value" lines in the Contact details doc -> SiteSettings fields
CONTACT_FIELDS = [
    ("email", "email"),
    ("whatsapp", "whatsapp_number"),
    ("linkedin", "linkedin_url"),
    ("instagram", "instagram_url"),
    ("fiverr", "fiverr_url"),
    ("booking", "booking_url"),
    ("how long", "consultation_length"),
    ("engineer", "stamping_engineer"),
    ("license", "stamping_license"),
]


def sync_contact(root: Path, say) -> int:
    doc = _find(root, "contact", "Contact details*.txt")
    if not doc:
        return 0
    s = SiteSettings.load()
    changed = 0
    for line in doc.read_text(encoding="utf-8-sig").splitlines():
        if ":" not in line:
            continue
        label, value = line.split(":", 1)
        label, value = label.strip().lower(), value.strip()
        if not value or value.startswith("(") or label.startswith(("http", "https")):
            continue
        field = next((f for word, f in CONTACT_FIELDS if word in label), None)
        if not field:
            continue
        if field == "whatsapp_number":
            value = re.sub(r"\D", "", value)
        if field.endswith("_url") and not value.startswith("http"):
            continue
        if getattr(s, field) != value:
            setattr(s, field, value[:200])
            changed += 1
    if changed:
        s.save()
    say(f"Contact details: {changed} field(s) updated")
    return changed


# which Drive files feed each section (the sheet or doc, and its pictures folder)
SECTIONS = {
    "team": ("about", ["Team*.csv"], "photo"),
    "partners": ("partner", ["Partner list*.csv"], "logo"),
    "reviews": ("review", ["Reviews*.csv"], None),
    "contact": ("contact", ["Contact details*.txt"], None),
}
STATE_FILE = ".content-state.json"


def _signature(root: Path, section: str) -> str:
    """Names, sizes and times of the files behind a section: changes when the team edits them."""
    word, globs, pics = SECTIONS[section]
    folder = next((d for d in sorted(root.iterdir()) if d.is_dir() and word in d.name.lower()), None)
    if not folder:
        return ""
    files = [f for g in globs for f in folder.rglob(g)]
    if pics:
        files += [f for d in folder.iterdir() if d.is_dir() and pics in d.name.lower() for f in d.rglob("*") if f.is_file()]
    return "|".join(sorted(f"{f.relative_to(root).as_posix()}:{f.stat().st_size}:{int(f.stat().st_mtime)}" for f in files))


def sync_content(root: Path, say=print, force: bool = False) -> dict:
    """Team, partners, reviews and contact details from the Drive.

    A section is only re-applied when its own files changed in the Drive (or with force), so edits
    made in the admin stay put until someone edits that sheet: a project upload doesn't roll back
    a name fixed on the website."""
    import json

    from config.revalidate import batched

    state_path = root / STATE_FILE
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    steps = {"team": sync_team, "partners": sync_partners, "reviews": sync_reviews, "contact": sync_contact}
    out = {}
    with batched():  # one refresh of the site at the end, not one per saved row
        for name, fn in steps.items():
            sig = _signature(root, name)
            if not force and sig and state.get(name) == sig:
                out[name] = None  # unchanged in the Drive
                continue
            out[name] = fn(root, say)
            state[name] = sig
    try:
        state_path.write_text(json.dumps(state, indent=1), encoding="utf-8")
    except OSError:
        pass
    return out

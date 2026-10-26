"""Turn design_raw.json (pulled from the finished design) into design_seed.json for `manage.py seed_design`.

    node seed/extract_design.mjs ../references/design/canvas > seed/design_raw.json
    python seed/build_seed.py ../references/design/assets/blob-map.json

The Projects page list is the master list of projects and their gallery images.
The homepage "Best work" pile adds which projects and sheets go on the homepage.
"""

import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
raw = json.loads((HERE / "design_raw.json").read_text(encoding="utf-8"))
blob_map = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))

WHITE = "#FFFFFF"

# design id -> (slug, filter name, home tab label, filter order, home order, in filter, on home, intro)
CATEGORIES = {
    "mod": ("home-modification", "Home modification", "HOUSE MODIFICATION", 1, 2, True, True,
            "Extensions, additions and renovations of existing homes, from measured existing conditions to a permit-ready set."),
    "res": ("new-residential", "New residential", "NEW HOME", 2, 1, True, True,
            "New homes: architectural drawings, models and renders."),
    "com": ("new-commercial", "New commercial", "COMMERCIAL", 3, 3, True, True,
            "Hotels, retail, offices and industrial buildings: design drawings, models and renders."),
    "adu": ("adu-and-garage", "ADU and garage", "ADU", 4, 4, True, True,
            "Accessory dwelling units and garages, including MEP sheets."),
    "garage": ("garage", "Garage", "GARAGE", 9, 5, False, True, ""),
    "mep": ("mep", "MEP", "MEP", 5, 9, True, False,
            "Plumbing, electrical and mechanical sheets with load calculations."),
    "str": ("structures", "Structures", "STRUCTURES", 6, 6, True, True,
            "Structural engineering: telecom steel structures, solar panel stands and other steel, concrete and timber work."),
    "modular": ("modular-homes", "Modular homes", "MODULAR HOMES", 7, 7, True, True,
                "Container homes, floating houses, greenhouses and other modular builds."),
    "civil": ("civil", "Civil", "CIVIL", 8, 10, True, False,
              "Land and site planning, lot subdivision, storm and drainage design."),
}
HOME_SET_TO_CAT = {"new": "res", "mod": "mod", "com": "com", "adu": "adu", "garage": "garage", "str": "str", "modular": "modular"}


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def file_of(src):
    """'blob:<id>' -> asset file name; 'ph:<glyph>' -> None."""
    if not src or not src.startswith("blob:"):
        return None
    return blob_map[src[5:]]["file"]


def kind_of(fit, bg):
    return "sheet" if fit == "contain" and bg == WHITE else "render"


def is_placeholder(value):
    return value.strip().startswith("[")


projects = []
by_title = {}
for order, p in enumerate(raw["projects"]["P"], start=1):
    facts = [{"label": k, "value": v} for k, v in p["facts"]]
    loc = next((f["value"] for f in facts if f["label"] == "LOCATION"), "")
    media = []
    for it in p["items"]:
        if it["kind"] == "compare":
            m = {
                "title": it["tab"], "kind": kind_of(it["fit"], it["bg"]),
                "image": file_of(it["b"]), "alt": it["altB"],
                "before_image": file_of(it["a"]), "before_alt": it.get("altA", ""),
                "before_label": it["la"], "after_label": it["lb"],
                "before_missing_note": it.get("phNote", "") if it.get("ph") else "",
            }
        else:
            m = {"title": it["tab"], "kind": kind_of(it["fit"], it["bg"]), "image": file_of(it["src"]), "alt": it["alt"]}
            if it["src"].startswith("ph:"):
                m["placeholder_glyph"] = it["src"][3:]
        no = re.match(r"^([A-Z]\d{3})\s+(.*)$", m["title"])
        if no:  # "S101 arrangement" -> sheet S101, title "Arrangement"
            m["sheet_no"], m["title"] = no.group(1), no.group(2)[:1].upper() + no.group(2)[1:]
        m.update(in_gallery=True, in_pile=False)
        media.append(m)
    proj = {
        "title": p["title"], "slug": slugify(p["title"]), "subtitle": p["sub"],
        "location": "" if is_placeholder(loc) else loc,
        "categories": [CATEGORIES[c][0] for c in p["cats"]],
        "facts": facts, "media": media, "order": order, "show_on_home": False,
    }
    projects.append(proj)
    by_title[p["title"]] = proj


def find_project(title):
    for t, proj in by_title.items():
        if t == title or t.startswith(title) or title.startswith(t):
            return proj
    raise KeyError(title)


warnings = []
for s in raw["home"]["SETS"]:
    cat_slug = CATEGORIES[HOME_SET_TO_CAT[s["id"]]][0]
    for hp in s["projects"]:
        proj = find_project(hp["title"])
        proj["show_on_home"] = True
        if cat_slug not in proj["categories"]:
            proj["categories"].append(cat_slug)
        extra = []
        for k, it in enumerate(hp["items"]):  # pass 1: mark gallery images that also go in the pile
            f = file_of(it["src"])
            match = next((m for m in proj["media"] if f and m["image"] == f), None)
            if match is None and not f:  # placeholder sheet: match on sheet number
                match = next((m for m in proj["media"] if m.get("placeholder_glyph") and m.get("sheet_no") == it["no"]), None)
            if match:
                match.update(in_pile=True, sheet_no=it["no"], _pile=k)
                if match.get("before_label"):  # the pile shows only the "after" image of a pair
                    match["title"] = it["title"]
            else:
                m = {"title": it["title"], "sheet_no": it["no"], "kind": kind_of(it["fit"], it["bg"]),
                     "image": f, "alt": it["alt"], "in_gallery": False, "in_pile": True, "_pile": k}
                if it["src"].startswith("ph:"):
                    m["placeholder_glyph"] = it["src"][3:]
                extra.append(m)
        for m in extra:  # pass 2: pile-only sheets go before the first pile sheet that follows them
            at = next((i for i, x in enumerate(proj["media"]) if x.get("_pile", -1) > m["_pile"]), len(proj["media"]))
            proj["media"].insert(at, m)
        pile = [m["sheet_no"] for m in proj["media"] if m["in_pile"]]
        want = [it["no"] for it in hp["items"]]
        if pile != want:
            warnings.append(f"{proj['title']}: pile order {pile} differs from design {want}")

# running strip on the homepage
for r in raw["home"]["RUN"]:
    proj = find_project(r["title"].split(",")[0] if r["title"].startswith("Garage") or r["title"].startswith("Alexandria") else r["title"].replace(" CA", ""))
    label, _, date = r["label"].partition(" · ")
    proj.update(stage="running", stage_label=label, stage_date="" if is_placeholder(date) else date, card_text=r["text"],
                cover_image=file_of(r["src"]))

# Notes for Saif ("[To add]", "Sheets to add") stay out of the public data; the admin shows what's missing.
SUBTITLE_FIX = {"Green house": "Modular home design"}
for proj in projects:
    for m in proj["media"]:
        m.pop("_pile", None)
    proj["facts"] = [f for f in proj["facts"] if not is_placeholder(f["value"]) and f["value"] != "Sheets to add"]
    if is_placeholder(proj["subtitle"]):
        proj["subtitle"] = SUBTITLE_FIX.get(proj["title"], "")

seed = {
    "categories": [
        {"slug": v[0], "name": v[1], "home_label": v[2], "order": v[3], "home_order": v[4],
         "show_in_filter": v[5], "show_on_home": v[6], "intro": v[7]}
        for v in CATEGORIES.values()
    ],
    "projects": projects,
    "partners": [{"name": p["name"], "order": i} for i, p in enumerate(raw["home"]["PARTNERS"], start=1)],
    "settings": {
        "fiverr_url": "https://www.fiverr.com/saifulhaq_kkk",
        "rating_value": "4.9", "rating_count": 370, "rating_source": "Fiverr",
    },
}
(HERE / "design_seed.json").write_text(json.dumps(seed, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print(len(projects), "projects,", sum(len(p["media"]) for p in projects), "images,", len(seed["partners"]), "partners")
for w in warnings:
    print("WARN", w)

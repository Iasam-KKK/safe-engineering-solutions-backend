"""Turning Saif's exported files into web-ready media.

- PDF permit sets → one JPEG per page (sheet), with the sheet number read from the
  title block when the PDF has a text layer, and optional redaction of text or areas.
- Images → JPEG, at most 2400 px on the long side.
- Videos → H.264 MP4 (≤ 1080p, fast start) plus a poster frame.
"""

import io
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

# ffmpeg is a console program: started from the hidden scheduled sync it would open a window
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

MAX_SIDE = 2400
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".wmv"}
PDF_EXT = {".pdf"}
# 3D models: binary glTF, exported from Revit (geometry and materials only, no BIM data)
MODEL_EXT = {".glb"}
MODEL_WARN_BYTES = 30 * 1024**2  # bigger than this is slow on a phone: compress it first

# Sheet numbers like A101, A-101, S201, E1.01, FP101, M-101.1
SHEET_NO = re.compile(r"^(?:[A-Z]{1,4})-?\d{1,4}(?:\.\d{1,2})?[a-z]?$")
TITLE_WORDS = {
    "PLAN", "PLANS", "ELEVATION", "ELEVATIONS", "SECTION", "SECTIONS", "DETAIL", "DETAILS",
    "SCHEDULE", "SCHEDULES", "NOTES", "COVER", "SITE", "ROOF", "FOUNDATION", "FRAMING",
    "DIAGRAM", "RISER", "LAYOUT", "ISOMETRIC", "FLOOR", "DEMOLITION", "ELECTRICAL", "PLUMBING",
    "MECHANICAL", "LIGHTING", "POWER", "SANITARY", "STRUCTURAL", "GRADING", "DRAINAGE", "TITLE",
    "SHEET", "EXISTING", "PROPOSED", "CALCULATIONS", "GENERAL",
}


# ---------- images ----------
INK = (17, 23, 28)
# bump when logo_mark changes, so logos already stored get redone on the next sync
MARK_VERSION = 2
MARK_NAME = re.compile(r"-mark\d*\.png$")


def logo_mark(img: Image.Image, colour: tuple[int, int, int] = INK, max_width: int = 600) -> bytes:
    """Any logo as a one-colour mark on a transparent background, trimmed, as PNG bytes.

    Partners send logos on white, on black, on navy, as white lettering on transparent, even on a
    photo-like gradient. Shown as they come they look like a patchwork of boxes; as one ink colour
    they read as a calm row of marks. The shape comes from:
      - the transparency, when the file has real transparency;
      - else how far each pixel is from the flat background colour (sampled along the border);
      - else (gradient or photo behind the logo) how far it is from a blurred copy of itself.
    """
    from PIL import ImageChops, ImageFilter

    img = ImageOps.exif_transpose(img)
    rgba = img.convert("RGBA")
    w, h = rgba.size
    alpha = rgba.getchannel("A")
    transparent = sum(alpha.histogram()[:240]) / (w * h)
    if transparent > 0.05:
        mask = alpha
    else:
        rgb = rgba.convert("RGB")
        b = max(2, int(min(w, h) * 0.03))
        border = [rgb.crop(box) for box in ((0, 0, w, b), (0, h - b, w, h), (0, 0, b, h), (w - b, 0, w, h))]
        pixels = [px for part in border for px in part.getdata()]
        spread = max(max(c[i] for c in pixels) - min(c[i] for c in pixels) for i in range(3))
        if spread < 40:  # flat background
            bg = tuple(sorted(c[i] for c in pixels)[len(pixels) // 2] for i in range(3))
            behind = Image.new("RGB", (w, h), bg)
            lo, hi = 26, 96
        else:  # gradient or photo behind the logo: stricter, so its texture doesn't come through
            behind = rgb.filter(ImageFilter.GaussianBlur(max(w, h) * 0.04))
            lo, hi = 44, 120
        diff = ImageChops.difference(rgb, behind)
        r, g, bl = diff.split()
        dist = ImageChops.lighter(ImageChops.lighter(r, g), bl)
        mask = dist.point(lambda v: 0 if v <= lo else 255 if v >= hi else int((v - lo) * 255 / (hi - lo)))
    # faint logos (grey on black, thin lines) come out as solid as the rest: the strongest strokes
    # become full ink
    hist = mask.histogram()
    strong = sum(hist[49:])
    if strong:
        acc, top = 0, 255
        for v in range(255, 48, -1):
            acc += hist[v]
            if acc >= strong * 0.1:
                top = v
                break
        if top < 250:
            k = min(4.0, 255 / max(top, 1))
            mask = mask.point(lambda v: min(255, int(v * k)))
    box = mask.point(lambda v: 255 if v > 48 else 0).getbbox()
    out = Image.new("RGBA", (w, h), colour + (0,))
    out.putalpha(mask)
    if box:
        pad = max(2, int(max(w, h) * 0.01))
        out = out.crop((max(0, box[0] - pad), max(0, box[1] - pad), min(w, box[2] + pad), min(h, box[3] + pad)))
    if out.width > max_width:
        out = out.resize((max_width, max(1, round(out.height * max_width / out.width))), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def to_web_jpeg(img: Image.Image, max_side: int = MAX_SIDE, quality: int = 88) -> bytes:
    """Flatten transparency onto white, shrink to max_side, save as progressive JPEG."""
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
    return out.getvalue()


def image_file_to_jpeg(path: Path) -> bytes:
    with Image.open(path) as img:
        return to_web_jpeg(img)


# ---------- PDFs ----------
@dataclass
class PdfPage:
    page: int            # 1-based
    jpeg: bytes
    sheet_no: str        # "" when not found
    title: str           # "" when not found
    has_text: bool       # False → probably a scan; nothing could be read or redacted as text


# Title-block labels. The value is the text right after the label on the same line,
# or the nearest line below it in the same column.
CLIENT_LABELS = ("CLIENT", "OWNER", "PREPARED FOR", "CUSTOMER", "HOMEOWNER", "APPLICANT")
TITLE_LABELS = ("DRAWING NAME", "DRAWING TITLE", "SHEET TITLE", "SHEET NAME", "TITLE")
FIELD_NAMES = {
    "PROJECT NUMBER", "PROJECT NO", "DATE", "DRAWN BY", "CHECKED BY", "SCALE", "DRAWING NAME", "SHEET",
    "NOTES:", "NOTES", "SHEET NUMBER", "REVISION", "REV", "ISSUED FOR", "ADDRESS", "PROJECT", *CLIENT_LABELS,
}
JOB_TAIL = re.compile(r"\s*(?:J-?\d{3,}|\d{1,2}\s+[A-Z]+\s+\d{4}|AS INDICATED|\d+/\d+\" = ).*$", re.I)


def _lines(page):
    """Text lines with their boxes: [(x0, y0, x1, y1, text)]."""
    out = []
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            text = "".join(sp["text"] for sp in ln["spans"]).strip()
            if text:
                out.append((*ln["bbox"], text))
    return out


def _label_values(page, labels, wrap: bool = False) -> list[str]:
    """Values written next to / under any of the labels (title-block style).
    wrap=True follows a value onto the lines below it (drawing names that wrap)."""
    lines = _lines(page)
    r = page.rect
    out = []
    for x0, y0, x1, y1, text in lines:
        up = text.upper().rstrip(":").strip()
        label = next((lb for lb in labels if up == lb or up.startswith(lb + ":") or up.startswith(lb + " ")), None)
        if not label:
            continue
        rest = text[len(label):].lstrip(" :.-")
        if rest and rest.upper() not in FIELD_NAMES:
            out.append(rest)
            continue
        # nearest line below, horizontally overlapping the label's column
        below = [
            ln for ln in lines
            if ln[1] >= y1 - 1 and ln[1] - y1 < r.height * 0.06
            and ln[0] < x1 + r.width * 0.12 and ln[2] > x0 - r.width * 0.02
            and ln[4].upper().rstrip(":").strip() not in FIELD_NAMES
        ]
        if below:
            first = min(below, key=lambda ln: (ln[1], abs(ln[0] - x0)))
            out.append(" ".join(_wrapped(lines, first)) if wrap else first[4])
    return [v.strip() for v in out if v.strip()]


def _wrapped(lines, first, most: int = 3) -> list[str]:
    """A value that wraps onto more lines ("DOORS AND" / "WINDOWS"): follow the lines straight
    below in the same lettering size, stopping at a gap, smaller text or the next field."""
    parts, last = [first[4]], first
    h = first[3] - first[1]
    for _ in range(most - 1):
        nxt = [
            ln for ln in lines
            if ln[1] > last[1] + h * 0.5 and ln[1] - last[3] <= h * 0.9
            and ln[0] < first[2] and ln[2] > first[0]
            and abs((ln[3] - ln[1]) - h) <= h * 0.3
            and ln[4].upper().rstrip(":").strip() not in FIELD_NAMES
        ]
        if not nxt:
            break
        last = min(nxt, key=lambda ln: ln[1])
        parts.append(last[4])
    return parts


# abbreviations that stay in capitals in sheet titles
ACRONYMS = {
    "MEP", "HVAC", "ADA", "ADU", "FFL", "RCP", "CMU", "LVL", "PV", "DWV", "NTS", "EV", "AC", "GFCI",
    "TYP", "BIM", "3D", "2D", "MDF", "OSB", "PT", "HDR", "CL", "FFE", "SOG", "UNO", "DHW", "RTU", "VAV",
}


def sheet_title(raw: str, sheet_no: str = "") -> str:
    """Title block text as a website title: sentence case with abbreviations kept, and without a
    repeated sheet number. 'ELECTRICAL PLAN FFL' -> 'Electrical plan FFL';
    'A-3 DRAINAGE GUTTER PLAN' on sheet A-3 -> 'Drainage gutter plan'."""
    s = re.sub(r"\s+", " ", raw or "").strip(" -:")
    if sheet_no and s.upper().startswith(sheet_no.upper()):
        s = s[len(sheet_no):].strip(" -:")
    words = []
    for i, w in enumerate(s.split(" ")):
        core = re.sub(r"[^A-Za-z0-9/]", "", w).upper()
        if core in ACRONYMS or (any(ch.isdigit() for ch in w) and any(ch.isalpha() for ch in w)):
            words.append(w.upper())  # MEP, HVAC, A-3, TYPE-2
        else:
            words.append(w[:1].upper() + w[1:].lower() if i == 0 else w.lower())
    return " ".join(words)


def _guess_sheet(page) -> tuple[str, str]:
    """Sheet number: the sheet-number-looking word lowest and furthest right (title blocks sit
    bottom-right). Title: the value under DRAWING NAME (or similar); otherwise the line with the
    most drawing-title words near the number."""
    words = page.get_text("words")  # x0, y0, x1, y1, text, block, line, word
    cands = [w for w in words if SHEET_NO.match(w[4].strip())]
    if not cands:
        return "", ""
    best = max(cands, key=lambda w: (w[0] + w[2]) / 2 + (w[1] + w[3]) / 2)
    sheet_no = best[4].strip()
    sheet_no = sheet_no[:-1].upper() + sheet_no[-1] if sheet_no[-1].islower() else sheet_no.upper()

    named = [JOB_TAIL.sub("", v).strip(" -") for v in _label_values(page, TITLE_LABELS, wrap=True)]
    named = [v for v in named if v and v.upper() != sheet_no.upper() and len(v) <= 60]
    if named:
        return sheet_no, named[0].title()

    lines: dict[tuple[int, int], list] = {}
    for w in words:
        lines.setdefault((w[5], w[6]), []).append(w)
    bx, by = (best[0] + best[2]) / 2, (best[1] + best[3]) / 2
    scored = []
    for ws in lines.values():
        text = " ".join(w[4] for w in ws).strip()
        tokens = {t.strip(",.:").upper() for t in text.split()}
        hits = len(tokens & TITLE_WORDS)
        if not hits or len(text) > 60 or any(ch.isdigit() for ch in text.replace(sheet_no, "")):
            continue
        cx = sum((w[0] + w[2]) / 2 for w in ws) / len(ws)
        cy = sum((w[1] + w[3]) / 2 for w in ws) / len(ws)
        dist = ((cx - bx) ** 2 + (cy - by) ** 2) ** 0.5
        scored.append((hits, -dist, text))
    title = max(scored)[2] if scored else ""
    return sheet_no, sheet_title(title, sheet_no)


STREET = (r"St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ln|Lane|Blvd|Boulevard|Ct|Court|Way|Pl|Place|Pkwy|Parkway|"
          r"Hwy|Highway|Cir|Circle|Ter|Terrace|Trl|Trail|Loop|Run|Pike|Sq|Square")
# a number followed by a unit is a dimension or a drawing note ("15 METER OF PIPE RUN"), not a street number
UNITS = (r"m|mm|cm|km|meters?|metres?|ft|feet|foot|in|inch(?:es)?|yds?|yards?|psi|gpm|cfm|btu|amps?|"
         r"volts?|v|kw|kva|hp|deg(?:rees)?|sq|sf|sqft|lbs?|kg|kn|x|nos?|pcs?")
ADDRESS = re.compile(
    rf"\b\d{{1,6}}\s+(?!(?:{UNITS})\b)(?:[NSEW]\.?\s+)?(?:[A-Za-z0-9][\w'.-]*\s+){{1,4}}(?:{STREET})\b\.?"
    r"(?:,?\s+(?:Apt|Unit|Suite|Ste|#)\s*[\w-]+)?",
    re.I,
)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b")
PRIVATE_LABELS = ("CLIENT", "OWNER", "PREPARED FOR", "CUSTOMER", "HOMEOWNER")


# "LIVONIA, MI 48152": a city, a state and a ZIP code
CITY_ST_ZIP = re.compile(r"\b[A-Z][A-Za-z .'-]{1,40},\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\b")
# "18340 MIDDLEBELT": a street number and a name, even without "Rd" / "St"
STREET_LINE = re.compile(r"^\d{1,6}\s+(?:[NSEW]\.?\s+)?[A-Za-z][\w .'-]{1,40}$")
ADDRESS_LABELS = ("LOCATION", "ADDRESS", "SITE ADDRESS", "PROJECT ADDRESS", "PROPERTY ADDRESS", "JOB ADDRESS", "PROJECT LOCATION")


def private_text(page) -> list[str]:
    """Probably-private text on a sheet: street addresses, emails, phone numbers, and the
    client / owner named in the title block.

    Addresses are found three ways: "123 Main St"-style text anywhere; whatever is written under
    a LOCATION / ADDRESS label when it looks like an address; and a "City, ST 12345" line together
    with the numbered street line just above it ("18340 MIDDLEBELT" / "LIVONIA, MI 48152")."""
    found: list[str] = []
    lines = _lines(page)
    for x0, y0, x1, y1, ln in lines:
        for rx in (ADDRESS, EMAIL, PHONE, CITY_ST_ZIP):
            found += [m.group(0).strip(" ,.") for m in rx.finditer(ln)]
        if CITY_ST_ZIP.search(ln):
            h = y1 - y0
            above = [l for l in lines if l[3] <= y0 + 1 and y0 - l[3] < h * 1.8 and l[0] < x1 and l[2] > x0]
            if above:
                street = max(above, key=lambda l: l[3])[4].strip()
                if STREET_LINE.match(street):
                    found.append(street)
    # the line under a LOCATION / ADDRESS label, when it looks like a street (the city line under
    # it is caught by CITY_ST_ZIP above); each line on its own, so it matches the drawing exactly
    found += [v.strip() for v in _label_values(page, ADDRESS_LABELS) if STREET_LINE.match(v.strip()) or CITY_ST_ZIP.search(v)]
    found += _label_values(page, CLIENT_LABELS)
    seen, out = set(), []
    for t in found:
        t = t.strip()
        if len(t) >= 3 and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def pdf_to_pages(
    path: Path,
    redact_text: list[str] | None = None,
    redact_boxes: list[tuple[float, float, float, float]] | None = None,
    max_side: int = MAX_SIDE,
    render: bool = True,
    auto_hide: bool = False,
) -> tuple[list[PdfPage], dict[str, int]]:
    """Render every page. Redaction happens on the PDF itself before rendering:
    each string in redact_text is searched (case-insensitive) and painted white; each box is
    (x0, y0, x1, y1) as fractions of the page, painted white.
    Returns the pages and how many times each redact_text string was found.
    render=False only reads sheet numbers and counts matches (for a dry run).
    auto_hide=True also hides what private_text() finds; those strings are reported with the
    prefix "auto: " in the returned counts."""
    import pymupdf

    out = []
    found = {s: 0 for s in redact_text or []}
    with pymupdf.open(path) as doc:
        # a client named in one title block is hidden on every sheet of the set
        private: list[str] = []
        if auto_hide:
            for page in doc:
                for t in private_text(page):
                    if t.lower() not in {x.lower() for x in private}:
                        private.append(t)
        for i, page in enumerate(doc, start=1):
            has_text = bool(page.get_text("text").strip())
            sheet_no, title = _guess_sheet(page) if has_text else ("", "")
            r = page.rect
            hits = 0
            for s in redact_text or []:
                for quad in page.search_for(s, quads=False):
                    page.add_redact_annot(quad, fill=(1, 1, 1))
                    hits += 1
                    found[s] += 1
            if auto_hide and has_text:
                for s in private:
                    key = "auto: " + s
                    for quad in page.search_for(s, quads=False):
                        page.add_redact_annot(quad, fill=(1, 1, 1))
                        hits += 1
                        found[key] = found.get(key, 0) + 1
            if not render:
                out.append(PdfPage(i, b"", sheet_no, title, has_text))
                continue
            for x0, y0, x1, y1 in redact_boxes or []:
                page.add_redact_annot(
                    pymupdf.Rect(r.x0 + x0 * r.width, r.y0 + y0 * r.height, r.x0 + x1 * r.width, r.y0 + y1 * r.height),
                    fill=(1, 1, 1),
                )
                hits += 1
            if hits:
                page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)
            zoom = max_side / max(r.width, r.height)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            out.append(PdfPage(i, to_web_jpeg(img, max_side), sheet_no, title, has_text))
    return out, found


# ---------- video ----------
def ffmpeg_exe() -> str:
    """System ffmpeg if installed, else the copy bundled with the imageio-ffmpeg package."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def transcode_video(src: Path, dst: Path, max_height: int = 1080) -> None:
    """Web MP4: H.264 + AAC, at most 1080p, moov atom first so it starts playing at once.
    Quality-based, but capped at about 6 Mbit/s: busy footage (aerial shots over trees) would
    otherwise come out at 30+ Mbit/s, too heavy to stream."""
    cmd = [
        ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(src),
        "-vf", f"scale=-2:'min({max_height},ih)'",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-maxrate", "6M", "-bufsize", "12M",
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
        str(dst),
    ]
    subprocess.run(cmd, check=True, capture_output=True, creationflags=NO_WINDOW)


def video_poster(src: Path, at_seconds: float = 1.0) -> bytes:
    """A JPEG frame from the video (1 s in, or the first frame for very short clips)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "poster.png"
        for t in (at_seconds, 0):
            subprocess.run(
                [ffmpeg_exe(), "-y", "-loglevel", "error", "-ss", str(t), "-i", str(src), "-frames:v", "1", str(out)],
                capture_output=True,
                creationflags=NO_WINDOW,
            )
            if out.exists() and out.stat().st_size:
                break
        if not out.exists():
            raise RuntimeError(f"could not read a frame from {src.name}")
        return image_file_to_jpeg(out)


# ---------- names ----------
NAME_PREFIX = re.compile(r"^(?P<no>[A-Z]{1,2}-?\d{1,3}(?:\.\d{1,2})?|R\d{1,3}|V\d{1,3})(?:[\s_\-–]+(?P<title>.*))?$", re.I)


def split_name(stem: str) -> tuple[str, str]:
    """'A101 Existing first floor' → ('A101', 'Existing first floor'); 'Front view' → ('', 'Front view')."""
    stem = re.sub(r"[_]+", " ", stem).strip()
    m = NAME_PREFIX.match(stem)
    if m:
        title = (m.group("title") or "").strip()
        return m.group("no").upper(), title[:1].upper() + title[1:] if title else ""
    return "", stem[:1].upper() + stem[1:]


def is_glb(path: Path) -> bool:
    """A binary glTF starts with the magic bytes "glTF" (anything else renamed .glb is refused)."""
    with Path(path).open("rb") as fh:
        return fh.read(4) == b"glTF"

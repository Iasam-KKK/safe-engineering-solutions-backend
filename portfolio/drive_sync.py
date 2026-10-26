"""Keep a local mirror of the team's Google Drive folder ("SES Website Data").

Uses the Drive REST API with a service account (the folder is shared with the service
account's email as Viewer). Only new or changed files are downloaded. Google Sheets are
saved as .csv and Google Docs as .txt so the importer can read them; files removed from
Drive are removed from the mirror.

    report = mirror(session(), folder_id, Path("drive_mirror"))
    report.changed  -> True when anything was added, updated or removed
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings

API = "https://www.googleapis.com/drive/v3"
FOLDER = "application/vnd.google-apps.folder"
EXPORTS = {
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    "application/vnd.google-apps.document": ("text/plain", ".txt"),
}
MANIFEST = ".drive-manifest.json"
BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class MirrorReport:
    downloaded: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.downloaded or self.removed)


class DriveError(Exception):
    """A setup or access problem, worded so the person running the sync knows what to fix."""


def key_path() -> Path:
    """GOOGLE_SERVICE_ACCOUNT_FILE, relative paths taken from the backend folder."""
    p = Path(settings.GOOGLE_SERVICE_ACCOUNT_FILE)
    return p if p.is_absolute() else Path(settings.BASE_DIR) / p


def session():
    """An authorised HTTP session for the service account in GOOGLE_SERVICE_ACCOUNT_FILE (read-only)."""
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    key = key_path()
    if not key.is_file():
        twin = key.with_name(key.name + ".json")
        hint = f" (found {twin.name}: Windows hid the extension, rename it to {key.name})" if twin.is_file() else ""
        raise DriveError(f"The Google key file {key} doesn't exist{hint}.")
    creds = service_account.Credentials.from_service_account_file(
        str(key), scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    return AuthorizedSession(creds)


def _check(res, what: str):
    """Raise a readable DriveError for Google's usual setup answers, else the plain HTTP error."""
    if res.status_code < 400:
        return
    try:
        err = res.json().get("error", {})
    except ValueError:
        err = {}
    reasons = {e.get("reason") for e in err.get("errors", [])}
    if "accessNotConfigured" in reasons or "SERVICE_DISABLED" in str(err):
        raise DriveError(
            "The Google Drive API is switched off for this Google Cloud project. Open "
            "console.cloud.google.com > APIs & Services > Library > Google Drive API > Enable, "
            "wait a minute, then run the sync again. (Google said: " + err.get("message", "") + ")"
        )
    if res.status_code == 404:
        raise DriveError(
            f"Google can't see {what}. Share the Drive folder with the service account's email "
            "(client_email in the key file) as Viewer, and check DRIVE_FOLDER_ID."
        )
    if res.status_code in (401, 403):
        raise DriveError(f"Google refused access to {what}: {err.get('message') or res.reason}")
    res.raise_for_status()


def _children(sess, folder_id: str) -> list[dict]:
    files, token = [], None
    while True:
        params = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": "nextPageToken, files(id, name, mimeType, md5Checksum, modifiedTime, size)",
            "pageSize": 1000,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if token:
            params["pageToken"] = token
        res = sess.get(f"{API}/files", params=params)
        _check(res, "the folder")
        data = res.json()
        files += data.get("files", [])
        token = data.get("nextPageToken")
        if not token:
            return files


def walk(sess, folder_id: str, prefix: Path = Path()):
    """(relative path, file metadata) for every file below the folder."""
    for f in _children(sess, folder_id):
        name = BAD_CHARS.sub("_", f["name"]).strip() or f["id"]
        if f["mimeType"] == FOLDER:
            yield from walk(sess, f["id"], prefix / name)
        else:
            yield prefix / name, f


def _download(sess, f: dict, dest: Path):
    if f["mimeType"] in EXPORTS:
        url, params = f"{API}/files/{f['id']}/export", {"mimeType": EXPORTS[f["mimeType"]][0]}
    else:
        url, params = f"{API}/files/{f['id']}", {"alt": "media", "supportsAllDrives": "true"}
    res = sess.get(url, params=params, stream=True)
    _check(res, f"the file {f['name']}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with tmp.open("wb") as fh:
        for chunk in res.iter_content(chunk_size=1 << 20):
            if chunk:
                fh.write(chunk)
    tmp.replace(dest)


def mirror(sess, folder_id: str, dest: Path) -> MirrorReport:
    """Bring dest up to date with the Drive folder."""
    dest.mkdir(parents=True, exist_ok=True)
    manifest_path = dest / MANIFEST
    old = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    new: dict[str, dict] = {}
    report = MirrorReport()
    taken: set[str] = set()

    for rel, f in walk(sess, folder_id):
        mime = f["mimeType"]
        if mime.startswith("application/vnd.google-apps.") and mime not in EXPORTS:
            report.skipped.append(rel.as_posix())  # forms, drawings, shortcuts...
            continue
        if mime in EXPORTS:
            rel = rel.with_name(rel.name + EXPORTS[mime][1])
        key = rel.as_posix().lower()
        if key in taken:  # two files with the same name in one folder
            rel = rel.with_name(f"{rel.stem} ({f['id'][:6]}){rel.suffix}")
        taken.add(rel.as_posix().lower())

        version = f.get("md5Checksum") or f.get("modifiedTime", "")
        target = dest / rel
        prev = old.get(f["id"])
        if prev and prev["version"] == version and prev["path"] == rel.as_posix() and target.exists():
            new[f["id"]] = prev
            continue
        if prev and prev["path"] != rel.as_posix():  # renamed or moved on Drive
            (dest / prev["path"]).unlink(missing_ok=True)
        _download(sess, f, target)
        new[f["id"]] = {"path": rel.as_posix(), "version": version}
        report.downloaded.append(rel.as_posix())

    for fid, info in old.items():
        if fid not in new:
            (dest / info["path"]).unlink(missing_ok=True)
            report.removed.append(info["path"])
    manifest_path.write_text(json.dumps(new, indent=1), encoding="utf-8")
    return report


def fingerprint(root: Path) -> str:
    """Cheap change check for a plain local folder (e.g. Google Drive for desktop)."""
    import hashlib

    h = hashlib.sha1()
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name != MANIFEST:
            st = p.stat()
            h.update(f"{p.relative_to(root).as_posix()}|{st.st_size}|{int(st.st_mtime)}\n".encode())
    return h.hexdigest()

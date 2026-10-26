# SAFE website: backend (Django)

Django 6.1 + Django REST Framework. It holds the content (projects, partners,
reviews, team, contact details) that Saif edits in the admin, serves it as a
read-only JSON API to the Next.js frontend, and stores quote / consultation
requests sent from the site.

## Run it on a laptop

Needs [uv](https://docs.astral.sh/uv/) (it installs Python 3.14 by itself).

```bash
cd backend
cp .env.example .env              # then set REVALIDATE_SECRET (same value as frontend/.env.local)
uv sync
uv run python manage.py migrate
uv run python manage.py seed_design          # loads the 20 projects, categories and partners from the design
uv run python manage.py createsuperuser      # your admin login
uv run python manage.py runserver 127.0.0.1:8000
```

- Admin: http://127.0.0.1:8000/admin/
- API: http://127.0.0.1:8000/api/site/ (add `?format=api` while `DJANGO_DEBUG=true` for a browsable view)
- Tests: `uv run python manage.py test`

## What's in the admin

| Section | Use it for |
| --- | --- |
| Portfolio › Projects | Add a project: title, categories, location (no street address), facts (SCOPE, CODES...), then its sheets and renders. Tick **Show on home** to put it in the homepage "Best work" pile; set **Stage** to put it in the Running / Built strips. |
| Portfolio › Sheets and images | Inside each project. *Drawing sheet* = white paper shown whole; *Render or photo* = fills the frame. Fill **Before image** to make a before/after slider. Leave **Image** empty and pick a placeholder sketch until the real sheet arrives. **In gallery** = project page, **In pile** = homepage pile. |
| Portfolio › Categories | Filter chips on /projects and tabs on the homepage, with their order. |
| Content › Site settings | Email, WhatsApp, LinkedIn, Fiverr, **booking link (Calendly)**, call length, **California stamping engineer + license**, rating figures and the date you checked them. |
| Content › Partners | The homepage ticker. Upload a logo when the firm sends one. |
| Content › Reviews | Reviews copied word for word. Use initials unless the client agreed to their name. |
| Content › Team members | The About page. |
| Requests from the website › Inquiries | Quote, video-consultation and stamping requests. Attached files download from here only (they have no public link). Set the status as you follow up. |

Saving anything refreshes the affected pages of the public site within seconds
(Django calls the frontend's `/api/revalidate` with `REVALIDATE_SECRET`).

## API

All read endpoints are public and read-only.

| Endpoint | Returns |
| --- | --- |
| `GET /api/site/` | settings, partners, featured reviews, team, categories |
| `GET /api/home/` | homepage "Best work" tabs with their sheets, running and built strips |
| `GET /api/projects/` | published projects (`?category=<slug>`, `?stage=running`, `?home=1`) |
| `GET /api/projects/<slug>/` | one project with facts and every sheet / image |
| `POST /api/inquiries/` | multipart form from the site: `kind`, `name`, `email`, `role`, `project_type`, `location`, `timing`, `details`, `files` (repeat), honeypot `website`. Rate-limited (6 per hour per IP by default). |

## Going live

Set the variables listed in `.env.example` (secret key, allowed hosts,
`DATABASE_URL` for PostgreSQL, CORS origin = the site's address, email server
and `INQUIRY_NOTIFY_EMAILS`), then:

```bash
uv sync --no-dev
uv run python manage.py migrate
uv run python manage.py collectstatic --noinput
uv run gunicorn config.wsgi --bind 0.0.0.0:8000 --workers 3
```

Uploaded images live in `MEDIA_ROOT` and must be served at `/media/` (by nginx,
or set `SERVE_MEDIA=true` on a small host). Client attachments live in
`PRIVATE_MEDIA_ROOT` and are never served publicly. `requirements.txt` is
exported from `uv.lock` for hosts that use pip.

## Importing Saif's projects (sheets, renders, videos)

For the backlog, don't type projects into the admin one by one. The team fills the shared Google
Drive folder **SES Website Data** → `01 Projects`: one folder per kind of work (Home modification,
New homes, … with type folders inside Structures, Modular homes and Civil), and in it either a
loose PDF set or one folder per project named `Project name - City ST` with
`1 Sheets / 2 Renders / 3 Before photos / 4 Videos / 5 Site photos`. No spreadsheet is required;
"Project details (optional)" adds what a name can't carry. Normally `sync_drive` (next section)
does the import by itself. To import a copied folder by hand:

```bash
uv run python manage.py import_projects --template ../import   # an empty copy of the same layout
uv run python manage.py import_projects ../import --dry-run     # what would load, sheet numbers found, what gets hidden
uv run python manage.py import_projects ../import               # load (new projects arrive as drafts)
```

- PDF sets: every page becomes a sheet; the sheet number and title are read from the title block.
- Street addresses, emails, phone numbers and CLIENT / OWNER title-block values are whited out on every
  sheet automatically (`--no-auto-hide` turns this off). Extra words to hide go in the details sheet
  (or `redact_text` / `redact_boxes` in a projects.csv). The dry run warns when a page is a scan (no text
  to search). Stamps, seals and signatures still need a human check before publishing.
- Videos are compressed to web MP4 (H.264, ≤1080p) with a poster frame (ffmpeg; the bundled copy from
  `imageio-ffmpeg` is used when ffmpeg isn't installed).
- Re-running is safe: files already imported are skipped (`--refresh` re-processes, `--update` rewrites
  project text from the CSV, `--publish` makes new projects live at once).

For one-off additions, each project's admin page has an **Add files** box (several files at once,
same processing; MP4s are kept as uploaded). Large video files should be served from a web server or
object storage live: Django's own file serving can't seek inside videos.

## Drive sync: new work appears by itself

The team only drops files into the shared Google Drive folder **SES Website Data**
(see its "READ ME FIRST"). `manage.py sync_drive` mirrors that folder and loads what changed:

- `01 Projects/<kind>/[<type>]/<project folder or loose PDF>`: projects, as **drafts**. Names give
  the title and location ("Floating house - Miami FL"), the folder gives the category, job numbers
  and revisions are dropped ("j 1065 floating house R3" → "Floating house"). Street addresses,
  emails, phone numbers and the CLIENT / OWNER names in title blocks are whited out automatically
  and listed in the summary. "Project details (optional)" adds city, a line of text, homepage, and
  extra words to hide.
- A project folder can hold sheets, renders, videos (compressed to web MP4 with a poster) and site
  photos. A **before** photo becomes a before/after slider with the render or sheet of the same name
  or number (`3 Before photos/R01 Front.jpg` pairs with `2 Renders/R01 Front.jpg`).
- A folder named like a loose PDF next to it (`Floating house - Miami FL/` beside
  `j 1065 floating house R3.pdf`) joins that project. Of several revisions of one set (`R2`, `R3`) only
  the newest is used; a different job number (`j 1102 floating house`) stays its own project.
- `02 About us/Team` + `Team photos`, `03 Partners/Partner list` + `Logos`, `04 Reviews/Reviews`,
  `05 Company and contact/Contact details`: updated in place, matched by name. Pictures match loosely
  (no extension needed; capitals, spacing, "LLC" and plurals don't matter). A logo with no row in the
  Partner list adds that firm; firms no longer on the list are hidden, not deleted.
- Nothing happens when nothing changed. A summary email goes to `IMPORT_NOTIFY_EMAILS`.

One-time setup (Google Cloud, free):

1. console.cloud.google.com → new project → APIs & Services → enable **Google Drive API**.
2. IAM & Admin → Service accounts → Create (no roles) → Keys → Add key → JSON. Save it as
   `backend/drive-sync-key.json` (git-ignored; never share it).
3. In Drive, share **SES Website Data** with the service account's email (`…@….iam.gserviceaccount.com`) as Viewer.
4. `backend/.env`: `GOOGLE_SERVICE_ACCOUNT_FILE=drive-sync-key.json` and `DRIVE_FOLDER_ID=1dBUu5n3b8lMnDUz2vbuxwhvtdQSgxMW5`.

Run it:

```bash
uv run python manage.py sync_drive            # one check
uv run python manage.py sync_drive --watch 15 # keep checking every 15 minutes
# live server (cron):   */15 * * * * cd /srv/safe/backend && uv run python manage.py sync_drive >> /var/log/safe-sync.log 2>&1
# this laptop (hidden scheduled task, no window):  powershell -ExecutionPolicy Bypass -File scripts\schedule-drive-sync.ps1
```

Without Google Cloud, `--path "G:/My Drive/SES Website Data"` reads a folder synced by Google Drive
for desktop instead (sheets and docs can't be read that way; folders and files can).

## Re-seeding from the design

`seed/extract_design.mjs` reads the project data out of
`references/design/canvas/*.dc.html`; `seed/build_seed.py` merges the homepage
pile with the Projects page into `seed/design_seed.json`, which `seed_design`
loads. Run `seed_design --reset` only on an empty or throwaway database: it
deletes projects, categories and partners first.

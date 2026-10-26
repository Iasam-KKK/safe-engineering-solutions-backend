"""
Django settings for the SAFE Engineering Solutions backend.

Everything that differs between a laptop and the live server comes from
environment variables (or a .env file next to manage.py). See .env.example.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

# ---------- core ----------
DEBUG = env.bool("DJANGO_DEBUG", default=False)
SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="dev-only-insecure-key-change-me" if DEBUG else environ.Env.NOTSET,
)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

# The public Next.js site. Used for "View on site" links in the admin and for
# telling Next.js to refresh pages when content changes.
FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:3000").rstrip("/")
# where Django reaches the site to refresh its pages; on the server the site's own local address
REVALIDATE_BASE = env("REVALIDATE_BASE", default=FRONTEND_URL).rstrip("/")
# Shared secret for POST {FRONTEND_URL}/api/revalidate. Leave empty to skip.
REVALIDATE_SECRET = env("REVALIDATE_SECRET", default="")

INSTALLED_APPS = [
    "config.apps.SafeAdminConfig",  # Django's admin, counting new requests and Drive imports
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "portfolio",
    "content",
    "leads",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ---------- database ----------
# SQLite on a laptop; set DATABASE_URL=postgres://user:pass@host:5432/name live.
DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ---------- static and uploaded files ----------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", default=str(BASE_DIR / "media")))
# Absolute base for media links in API responses, e.g. https://api.example.com
# (left empty, links are built from the incoming request's host).
PUBLIC_MEDIA_BASE = env("PUBLIC_MEDIA_BASE", default="").rstrip("/")
# true: the API returns media as "/media/..." and the Next.js site forwards that path to Django
RELATIVE_MEDIA_URLS = env.bool("RELATIVE_MEDIA_URLS", default=False)
SERVE_MEDIA = env.bool("SERVE_MEDIA", default=False)

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # hashed, compressed admin CSS/JS live (needs collectstatic); plain files while developing
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# Inquiry uploads: drawings, photos and sketches clients attach to a quote request.
# Kept outside MEDIA_ROOT so they never get a public URL.
PRIVATE_MEDIA_ROOT = Path(env("PRIVATE_MEDIA_ROOT", default=str(BASE_DIR / "private_media")))
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
INQUIRY_MAX_FILES = env.int("INQUIRY_MAX_FILES", default=8)
INQUIRY_MAX_FILE_MB = env.int("INQUIRY_MAX_FILE_MB", default=25)

# ---------- API ----------
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=["http://localhost:3000"])
CORS_URLS_REGEX = r"^/api/.*$"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"]
    + (["rest_framework.renderers.BrowsableAPIRenderer"] if DEBUG else []),
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_THROTTLE_RATES": {"inquiries": env("INQUIRY_RATE", default="6/hour")},
    "UNAUTHENTICATED_USER": None,
    # number of proxies in front of Django (e.g. 1 behind nginx) so throttling sees the visitor's IP
    "NUM_PROXIES": env.int("DRF_NUM_PROXIES", default=0) or None,
}

# ---------- email (new-inquiry alerts to Saif) ----------
# Console output on a laptop; set EMAIL_HOST etc. on the server.
if env("EMAIL_HOST", default=""):
    MAILERS = {
        "default": {
            "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
            "OPTIONS": {
                "host": env("EMAIL_HOST"),
                "port": env.int("EMAIL_PORT", default=587),
                "username": env("EMAIL_HOST_USER", default=""),
                "password": env("EMAIL_HOST_PASSWORD", default=""),
                "use_tls": env.bool("EMAIL_USE_TLS", default=True),
                "timeout": 15,
            },
        },
    }
else:
    MAILERS = {"default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"}}
    # Live without EMAIL_HOST yet: requests are still kept in the admin and the alerts go to the
    # service's log, so Django's deploy check (which would stop every deploy) lets this pass.
    if not DEBUG:
        SILENCED_SYSTEM_CHECKS = ["mail.E001"]
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="SAFE website <no-reply@localhost>")
# Who gets an email for every new quote / consultation request (comma separated)
INQUIRY_NOTIFY_EMAILS = env.list("INQUIRY_NOTIFY_EMAILS", default=[])

# ---------- Google Drive sync (manage.py sync_drive) ----------
# A service account's JSON key; share the team's Drive folder with its email as Viewer.
GOOGLE_SERVICE_ACCOUNT_FILE = env("GOOGLE_SERVICE_ACCOUNT_FILE", default="")
# The folder's id: the last part of its link, drive.google.com/drive/folders/<id>
DRIVE_FOLDER_ID = env("DRIVE_FOLDER_ID", default="")
DRIVE_MIRROR_DIR = Path(env("DRIVE_MIRROR_DIR", default=str(BASE_DIR / "drive_mirror")))
# Who gets a summary when new work arrives from Drive (defaults to the inquiry list)
IMPORT_NOTIFY_EMAILS = env.list("IMPORT_NOTIFY_EMAILS", default=INQUIRY_NOTIFY_EMAILS)

# ---------- production hardening ----------
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env.int("DJANGO_HSTS_SECONDS", default=0)
    SECURE_CONTENT_TYPE_NOSNIFF = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {
        "safe": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
    },
}

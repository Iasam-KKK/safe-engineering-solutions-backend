"""Small helpers shared by the API serializers."""

from django.conf import settings


def file_url(request, field):
    """Absolute URL for an uploaded image so the Next.js server can fetch it."""
    if not field:
        return None
    url = field.url
    if url.startswith("http"):
        return url
    # "/media/...": the website forwards it to Django, so it works from any address the site is
    # opened on (localhost, a Tailscale link, a domain)
    if settings.RELATIVE_MEDIA_URLS:
        return url
    if settings.PUBLIC_MEDIA_BASE:
        return settings.PUBLIC_MEDIA_BASE + url
    return request.build_absolute_uri(url) if request else url


def image_obj(request, field, width=None, height=None):
    """{url, width, height} for an ImageField, or None when empty."""
    if not field:
        return None
    return {
        "url": file_url(request, field),
        "width": width if width is not None else getattr(field, "width", None),
        "height": height if height is not None else getattr(field, "height", None),
    }

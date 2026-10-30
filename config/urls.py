from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import path, re_path
from django.views.static import serve

from content.views import Site
from leads.views import CallSlots, InquiryCreate
from portfolio.views import CategoryList, Home, ProjectDetail, ProjectList

admin.site.site_header = "SAFE Engineering Solutions"
admin.site.site_title = "SAFE admin"
admin.site.index_title = "Website content"
admin.site.site_url = settings.FRONTEND_URL

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", lambda r: JsonResponse({"ok": True})),
    path("api/site/", Site.as_view(), name="api-site"),
    path("api/home/", Home.as_view(), name="api-home"),
    path("api/categories/", CategoryList.as_view(), name="api-categories"),
    path("api/projects/", ProjectList.as_view(), name="api-projects"),
    path("api/projects/<slug:slug>/", ProjectDetail.as_view(), name="api-project"),
    path("api/inquiries/", InquiryCreate.as_view(), name="api-inquiries"),
    path("api/booking/slots/", CallSlots.as_view(), name="api-booking-slots"),
]

# Uploaded project images. On a laptop Django serves them; live, the web server or
# object storage should. SERVE_MEDIA=true lets Django serve them on a small host.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif settings.SERVE_MEDIA:
    urlpatterns += [re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT})]

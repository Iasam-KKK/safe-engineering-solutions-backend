from django.db.models import Count, Prefetch, Q
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import NOT_IN_PILE, Category, Project, ProjectMedia
from .serializers import (
    CategorySerializer,
    MediaSerializer,
    ProjectDetailSerializer,
    ProjectSummarySerializer,
    media_kind,
)


def project_qs():
    return (
        Project.objects.published()
        .select_related("cover")
        .prefetch_related("categories", "facts", Prefetch("media", queryset=ProjectMedia.objects.order_by("order", "id")))
    )


def categories_qs():
    return Category.objects.annotate(
        project_count=Count("projects", filter=Q(projects__is_published=True), distinct=True)
    )


class CategoryList(generics.ListAPIView):
    """GET /api/categories/ — every category, in order, with how many published projects it has."""

    serializer_class = CategorySerializer
    pagination_class = None

    def get_queryset(self):
        return categories_qs()


class ProjectList(generics.ListAPIView):
    """GET /api/projects/ — published projects.

    ?category=<slug>   only that category
    ?stage=running|built
    ?home=1            only projects picked for the homepage
    """

    serializer_class = ProjectSummarySerializer
    pagination_class = None

    def get_queryset(self):
        qs = project_qs()
        p = self.request.query_params
        if p.get("category"):
            qs = qs.filter(categories__slug=p["category"])
        if p.get("stage"):
            qs = qs.filter(stage=p["stage"])
        if p.get("home") in ("1", "true"):
            qs = qs.filter(show_on_home=True)
        return qs.distinct()


class ProjectDetail(generics.RetrieveAPIView):
    """GET /api/projects/<slug>/ — one project with its facts and every sheet / image."""

    serializer_class = ProjectDetailSerializer
    lookup_field = "slug"

    def get_queryset(self):
        return project_qs()


PILE_SIZE = 10  # the homepage pile has poses for up to 11 sheets


class Home(APIView):
    """GET /api/home/ — everything the homepage needs from the portfolio in one call.

    best_work: the homepage tabs (categories marked "show on home"), each with
    its homepage projects and the sheets that go in the drawing pile.
    running / built: cards for the two strips.
    """

    def get(self, request):
        ctx = {"request": request}
        projects = list(project_qs())
        best_work = []
        for cat in Category.objects.filter(show_on_home=True).order_by("home_order", "name"):
            rows = []
            # every published project in the category, the ones ticked "Show on home" first
            in_cat = [p for p in projects if cat in p.categories.all()]
            in_cat.sort(key=lambda p: not p.show_on_home)  # stable: keeps the admin order otherwise
            for p in in_cat:
                # pictures, and placeholder sketches for sheets that haven't arrived yet
                pictures = [m for m in p.media.all() if (m.image or m.placeholder_glyph) and m.kind not in NOT_IN_PILE]
                # the sheets picked for the pile, or else its first pictures (renders lead, see reorder_media)
                pile = [m for m in pictures if m.in_pile] or pictures[:PILE_SIZE]
                if not pile:
                    continue
                rows.append({
                    "slug": p.slug,
                    "title": p.title,
                    "subtitle": p.subtitle,
                    "location": p.location,
                    "facts": [{"label": f.label, "value": f.value} for f in p.facts.all()],
                    "media_kind": media_kind(pile),
                    "sheets": MediaSerializer(pile, many=True, context=ctx).data,
                })
            if rows:
                best_work.append({"slug": cat.slug, "label": cat.tab_label, "projects": rows})

        def strip(stage):
            return ProjectSummarySerializer([p for p in projects if p.stage == stage], many=True, context=ctx).data

        return Response({"best_work": best_work, "running": strip("running"), "built": strip("built")})

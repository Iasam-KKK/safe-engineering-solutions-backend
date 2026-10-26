from rest_framework import serializers

from config.api_utils import file_url, image_obj

from .models import Category, Project, ProjectMedia


class CategorySerializer(serializers.ModelSerializer):
    tab_label = serializers.CharField(read_only=True)
    project_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Category
        fields = ["slug", "name", "tab_label", "intro", "order", "show_in_filter", "show_on_home", "project_count"]


class MediaSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    before = serializers.SerializerMethodField()
    video = serializers.SerializerMethodField()

    class Meta:
        model = ProjectMedia
        fields = [
            "id", "kind", "sheet_no", "title", "alt", "image", "placeholder_glyph",
            "before", "after_label", "video", "in_gallery", "in_pile",
        ]

    def get_image(self, m):
        return image_obj(self.context.get("request"), m.image, m.width, m.height)

    def get_video(self, m):
        """Videos: the file to play; `image` is its poster."""
        if m.kind != ProjectMedia.Kind.VIDEO or not m.video:
            return None
        return {"url": file_url(self.context.get("request"), m.video)}

    def get_before(self, m):
        if not m.is_pair:
            return None
        return {
            "image": image_obj(self.context.get("request"), m.before_image, m.before_width, m.before_height),
            "alt": m.before_alt,
            "label": m.before_label,
            "missing_note": m.before_missing_note,
        }


def media_kind(items):
    """SHEETS when every item is a drawing, RENDERS when every item is a render, else VIEWS."""
    kinds = {m.kind for m in items}
    if kinds == {ProjectMedia.Kind.SHEET}:
        return "SHEETS"
    if kinds == {ProjectMedia.Kind.RENDER}:
        return "RENDERS"
    return "VIEWS"


class ProjectSummarySerializer(serializers.ModelSerializer):
    categories = serializers.SlugRelatedField(slug_field="slug", many=True, read_only=True)
    cover = serializers.SerializerMethodField()
    media_count = serializers.SerializerMethodField()
    media_kind = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = [
            "slug", "title", "subtitle", "location", "categories", "cover", "media_count", "media_kind",
            "show_on_home", "stage", "stage_label", "stage_date", "card_text", "updated_at",
        ]

    def _gallery(self, p):
        return [m for m in p.media.all() if m.in_gallery]

    def get_cover(self, p):
        m = p.cover_media()
        if not m:
            return None
        return {
            "kind": m.kind,
            "alt": m.alt,
            **(image_obj(self.context.get("request"), m.image, m.width, m.height) or {}),
        }

    def get_media_count(self, p):
        return len(self._gallery(p))

    def get_media_kind(self, p):
        return media_kind(self._gallery(p))


class ProjectDetailSerializer(ProjectSummarySerializer):
    facts = serializers.SerializerMethodField()
    media = serializers.SerializerMethodField()
    seo_title = serializers.SerializerMethodField()
    seo_description = serializers.SerializerMethodField()

    class Meta(ProjectSummarySerializer.Meta):
        fields = ProjectSummarySerializer.Meta.fields + ["description", "facts", "media", "seo_title", "seo_description"]

    def get_facts(self, p):
        return [{"label": f.label, "value": f.value} for f in p.facts.all()]

    def get_media(self, p):
        return MediaSerializer(p.media.all(), many=True, context=self.context).data

    def get_seo_title(self, p):
        return p.seo_title or p.title

    def get_seo_description(self, p):
        return p.seo_description or p.subtitle

from rest_framework import serializers

from config.api_utils import image_obj

from .models import Partner, Review, SiteSettings, TeamMember


class SiteSettingsSerializer(serializers.ModelSerializer):
    whatsapp_url = serializers.SerializerMethodField()
    rating = serializers.SerializerMethodField()

    class Meta:
        model = SiteSettings
        fields = [
            "company_name", "email", "whatsapp_number", "whatsapp_url", "linkedin_url", "fiverr_url",
            "booking_url", "consultation_length", "stamping_engineer", "stamping_license", "rating",
        ]

    def get_whatsapp_url(self, s):
        digits = "".join(c for c in s.whatsapp_number if c.isdigit())
        return f"https://wa.me/{digits}" if digits else ""

    def get_rating(self, s):
        if s.rating_value is None:
            return None
        return {
            "value": float(s.rating_value),
            "count": s.rating_count,
            "source": s.rating_source,
            "as_of": s.rating_as_of.isoformat() if s.rating_as_of else None,
        }


class PartnerSerializer(serializers.ModelSerializer):
    logo = serializers.SerializerMethodField()

    class Meta:
        model = Partner
        fields = ["name", "logo", "website"]

    def get_logo(self, p):
        return image_obj(self.context.get("request"), p.logo)


class ReviewSerializer(serializers.ModelSerializer):
    project = serializers.SerializerMethodField()

    class Meta:
        model = Review
        fields = ["author", "country", "rating", "text", "source", "source_url", "date", "project"]

    def get_project(self, r):
        if not r.project_id or not r.project.is_published:
            return None
        return {"slug": r.project.slug, "title": r.project.title}


class TeamMemberSerializer(serializers.ModelSerializer):
    photo = serializers.SerializerMethodField()

    class Meta:
        model = TeamMember
        fields = ["name", "role", "credentials", "bio", "photo"]

    def get_photo(self, t):
        return image_obj(self.context.get("request"), t.photo)

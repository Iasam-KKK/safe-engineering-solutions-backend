from rest_framework.response import Response
from rest_framework.views import APIView

from portfolio.serializers import CategorySerializer
from portfolio.views import categories_qs

from .models import Partner, Review, SiteSettings, TeamMember
from .serializers import PartnerSerializer, ReviewSerializer, SiteSettingsSerializer, TeamMemberSerializer


class Site(APIView):
    """GET /api/site/ — details every page needs: contact info, partners, reviews, team, categories."""

    def get(self, request):
        ctx = {"request": request}
        return Response({
            "settings": SiteSettingsSerializer(SiteSettings.load(), context=ctx).data,
            "partners": PartnerSerializer(Partner.objects.filter(is_active=True), many=True, context=ctx).data,
            "reviews": ReviewSerializer(
                Review.objects.filter(is_featured=True).select_related("project"), many=True, context=ctx
            ).data,
            "team": TeamMemberSerializer(TeamMember.objects.filter(is_active=True), many=True, context=ctx).data,
            "categories": CategorySerializer(categories_qs(), many=True, context=ctx).data,
        })

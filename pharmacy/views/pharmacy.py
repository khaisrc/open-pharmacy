from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action

from pharmacy.filters import PharmacyFilter
from pharmacy.models import Pharmacy, PharmacyLocation
from pharmacy.serializers import PharmacyLocationSerializer, PharmacySerializer

from .base import SoftDeleteModelViewSet
from .tags import tagged


@extend_schema_view(**tagged("Pharmacies", "locations"))
class PharmacyViewSet(SoftDeleteModelViewSet):
    serializer_class = PharmacySerializer
    filterset_class = PharmacyFilter
    search_fields = ["name", "legal_name"]
    ordering_fields = ["created_at", "name"]
    protected_relations = (("locations", "pharmacy locations"),)

    def get_queryset(self):
        # Annotated, not stored: the count must exclude soft-deleted locations.
        # annotate() adds a GROUP BY, which makes QuerySet.ordered False even
        # though Meta.ordering still applies -- order_by keeps DRF pagination
        # from warning about an unordered list.
        return Pharmacy.objects.annotate(
            location_count=Count("locations", filter=Q(locations__deleted__isnull=True))
        ).order_by("-created_at")

    @extend_schema(
        summary="Locations belonging to this pharmacy",
        responses=PharmacyLocationSerializer(many=True),
    )
    @action(detail=True, methods=["get"])
    def locations(self, request, pk=None):
        pharmacy = self.get_object()
        queryset = PharmacyLocation.objects.filter(pharmacy=pharmacy).select_related(
            "pharmacy", "address"
        )
        page = self.paginate_queryset(queryset)
        serializer = PharmacyLocationSerializer(
            page, many=True, context=self.get_serializer_context()
        )
        return self.get_paginated_response(serializer.data)

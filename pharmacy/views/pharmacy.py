from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action

from pharmacy.filters import PharmacyFilter
from pharmacy.models import Pharmacy
from pharmacy.serializers import PharmacyLocationSerializer, PharmacySerializer

from .base import SoftDeleteModelViewSet, nested_list
from .pharmacy_location import PharmacyLocationViewSet
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
        return nested_list(PharmacyLocationViewSet, request, pharmacy=self.get_object())

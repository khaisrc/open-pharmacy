from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema_view

from pharmacy.filters import PharmacyFilter
from pharmacy.models import Pharmacy
from pharmacy.serializers import PharmacySerializer

from .base import SoftDeleteModelViewSet
from .tags import tagged


@extend_schema_view(**tagged("Pharmacies"))
class PharmacyViewSet(SoftDeleteModelViewSet):
    """Locations and staff are sub-resources, routed under /pharmacies/{id}/
    in urls.py rather than as actions here."""

    serializer_class = PharmacySerializer
    filterset_class = PharmacyFilter
    search_fields = ["name", "legal_name"]
    ordering_fields = ["created_at", "name"]
    protected_relations = (("locations", "pharmacy locations"), ("staff", "staff"))

    def get_queryset(self):
        # Annotated, not stored: the count must exclude soft-deleted locations.
        # annotate() adds a GROUP BY, which makes QuerySet.ordered False even
        # though Meta.ordering still applies -- order_by keeps DRF pagination
        # from warning about an unordered list.
        return Pharmacy.objects.annotate(
            location_count=Count("locations", filter=Q(locations__deleted__isnull=True))
        ).order_by("-created_at")

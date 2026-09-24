from drf_spectacular.utils import extend_schema_view

from pharmacy import repositories, services
from pharmacy.filters import PharmacyFilter
from pharmacy.serializers import PharmacySerializer

from .base import SoftDeleteModelViewSet
from .tags import tagged


@extend_schema_view(**tagged("Pharmacies"))
class PharmacyViewSet(SoftDeleteModelViewSet):
    """Locations and staff are sub-resources, routed under /pharmacies/{id}/
    in urls.py rather than as actions here."""

    repository = repositories.pharmacies
    service = services.pharmacies
    serializer_class = PharmacySerializer
    filterset_class = PharmacyFilter
    search_fields = ["name", "legal_name"]
    ordering_fields = ["created_at", "name"]

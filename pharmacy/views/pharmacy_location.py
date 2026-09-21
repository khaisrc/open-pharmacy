from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action

from pharmacy.filters import PharmacyLocationFilter
from pharmacy.models import PharmacyLocation
from pharmacy.serializers import PharmacyLocationSerializer, PrescriptionSerializer

from .base import SoftDeleteModelViewSet, nested_list
from .prescription import PrescriptionViewSet
from .tags import tagged


@extend_schema_view(**tagged("Pharmacy Locations", "prescriptions"))
class PharmacyLocationViewSet(SoftDeleteModelViewSet):
    """Top-level as well as nested under a pharmacy: the useful queries --
    every store in a state, the store holding an NCPDP ID -- cut across
    pharmacies."""

    queryset = PharmacyLocation.objects.select_related("pharmacy", "address")
    serializer_class = PharmacyLocationSerializer
    filterset_class = PharmacyLocationFilter
    search_fields = ["store_number", "ncpdp_id", "pharmacy__name"]
    ordering_fields = ["created_at", "store_number"]
    protected_relations = (("prescriptions", "prescriptions"),)

    @extend_schema(
        summary="Prescriptions routed to this location",
        responses=PrescriptionSerializer(many=True),
    )
    @action(detail=True, methods=["get"])
    def prescriptions(self, request, pk=None):
        return nested_list(PrescriptionViewSet, request, pharmacy_location=self.get_object())

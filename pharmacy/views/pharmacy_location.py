from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action

from pharmacy.filters import PharmacyLocationFilter
from pharmacy.models import PharmacyLocation
from pharmacy.serializers import PharmacyLocationSerializer, PrescriptionSerializer

from .base import PharmacyScopedViewSet, nested_list
from .prescription import PrescriptionViewSet
from .tags import PHARMACY_ID, tagged


@extend_schema_view(**tagged("Pharmacy Locations", "prescriptions", parameters=[PHARMACY_ID]))
class PharmacyLocationViewSet(PharmacyScopedViewSet):
    """Nested under the owning pharmacy only. Cross-pharmacy questions about
    prescriptions still go through /prescriptions/?pharmacy= and
    ?pharmacy_location=."""

    queryset = PharmacyLocation.objects.select_related(
        "pharmacy", "address", "pharmacist_in_charge"
    )
    serializer_class = PharmacyLocationSerializer
    filterset_class = PharmacyLocationFilter
    search_fields = ["store_number", "ncpdp_id"]
    ordering_fields = ["created_at", "store_number"]
    protected_relations = (("prescriptions", "prescriptions"),)

    @extend_schema(
        summary="Prescriptions routed to this location",
        responses=PrescriptionSerializer(many=True),
    )
    @action(detail=True, methods=["get"])
    def prescriptions(self, request, pk=None, pharmacy_id=None):
        return nested_list(PrescriptionViewSet, request, pharmacy_location=self.get_object())

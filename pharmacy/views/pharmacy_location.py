from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action

from pharmacy.filters import PharmacyLocationFilter
from pharmacy.models import PharmacyLocation, Prescription
from pharmacy.serializers import PharmacyLocationSerializer, PrescriptionSerializer

from .base import SoftDeleteModelViewSet
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
        location = self.get_object()
        queryset = Prescription.objects.filter(pharmacy_location=location).select_related(
            "patient",
            "prescriber",
            "medication",
            "pharmacy_location",
            "pharmacy_location__pharmacy",
            "pharmacy_location__address",
        )
        page = self.paginate_queryset(queryset)
        serializer = PrescriptionSerializer(page, many=True, context=self.get_serializer_context())
        return self.get_paginated_response(serializer.data)

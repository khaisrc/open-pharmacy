from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action

from pharmacy.filters import PrescriberFilter
from pharmacy.models import Prescriber, Prescription
from pharmacy.serializers import PrescriberSerializer, PrescriptionSerializer

from .base import SoftDeleteModelViewSet
from .tags import tagged


@extend_schema_view(**tagged("Prescribers", "prescriptions"))
class PrescriberViewSet(SoftDeleteModelViewSet):
    queryset = Prescriber.objects.select_related("address")
    serializer_class = PrescriberSerializer
    filterset_class = PrescriberFilter
    search_fields = ["first_name", "last_name", "npi"]
    ordering_fields = ["created_at", "last_name"]
    protected_relations = (("prescriptions", "prescriptions"),)

    @extend_schema(
        summary="Prescriptions written by this prescriber",
        responses=PrescriptionSerializer(many=True),
    )
    @action(detail=True, methods=["get"])
    def prescriptions(self, request, pk=None):
        prescriber = self.get_object()
        queryset = Prescription.objects.filter(prescriber=prescriber).select_related(
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

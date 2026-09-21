from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action

from pharmacy.filters import PatientFilter
from pharmacy.models import Patient, Prescription
from pharmacy.serializers import PatientSerializer, PrescriptionSerializer

from .base import SoftDeleteModelViewSet
from .tags import tagged


@extend_schema_view(**tagged("Patients", "prescriptions"))
class PatientViewSet(SoftDeleteModelViewSet):
    queryset = Patient.objects.select_related("address")
    serializer_class = PatientSerializer
    filterset_class = PatientFilter
    search_fields = ["first_name", "last_name"]
    ordering_fields = ["created_at", "last_name", "dob"]
    protected_relations = (("prescriptions", "prescriptions"),)

    @extend_schema(
        summary="Prescriptions written for this patient",
        responses=PrescriptionSerializer(many=True),
    )
    @action(detail=True, methods=["get"])
    def prescriptions(self, request, pk=None):
        patient = self.get_object()
        queryset = Prescription.objects.filter(patient=patient).select_related(
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

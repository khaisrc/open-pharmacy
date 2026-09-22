from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action

from pharmacy import repositories, services
from pharmacy.filters import PatientFilter
from pharmacy.serializers import PatientSerializer, PrescriptionSerializer

from .base import SoftDeleteModelViewSet, nested_list
from .prescription import PrescriptionViewSet
from .tags import tagged


@extend_schema_view(**tagged("Patients", "prescriptions"))
class PatientViewSet(SoftDeleteModelViewSet):
    repository = repositories.patients
    service = services.patients
    serializer_class = PatientSerializer
    filterset_class = PatientFilter
    search_fields = ["first_name", "last_name"]
    ordering_fields = ["created_at", "last_name", "dob"]

    @extend_schema(
        summary="Prescriptions written for this patient",
        responses=PrescriptionSerializer(many=True),
    )
    @action(detail=True, methods=["get"])
    def prescriptions(self, request, pk=None):
        return nested_list(PrescriptionViewSet, request, patient=self.get_object())

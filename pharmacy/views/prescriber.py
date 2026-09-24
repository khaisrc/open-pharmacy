from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action

from pharmacy import repositories, services
from pharmacy.filters import PrescriberFilter
from pharmacy.serializers import PrescriberSerializer, PrescriptionSerializer

from .base import SoftDeleteModelViewSet, nested_list
from .prescription import PrescriptionViewSet
from .tags import tagged


@extend_schema_view(**tagged("Prescribers", "prescriptions"))
class PrescriberViewSet(SoftDeleteModelViewSet):
    repository = repositories.prescribers
    service = services.prescribers
    serializer_class = PrescriberSerializer
    filterset_class = PrescriberFilter
    search_fields = ["first_name", "last_name", "npi"]
    ordering_fields = ["created_at", "last_name"]

    @extend_schema(
        summary="Prescriptions written by this prescriber",
        responses=PrescriptionSerializer(many=True),
    )
    @action(detail=True, methods=["get"])
    def prescriptions(self, request, pk=None):
        return nested_list(PrescriptionViewSet, request, prescriber=self.get_object())

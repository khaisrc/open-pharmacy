from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action

from pharmacy.filters import PrescriberFilter
from pharmacy.models import Prescriber
from pharmacy.serializers import PrescriberSerializer, PrescriptionSerializer

from .base import SoftDeleteModelViewSet, nested_list
from .prescription import PrescriptionViewSet
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
        return nested_list(PrescriptionViewSet, request, prescriber=self.get_object())

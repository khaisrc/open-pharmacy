from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action

from pharmacy import repositories, services
from pharmacy.auth.scopes import Scope, crud_scopes
from pharmacy.filters import PrescriberFilter
from pharmacy.serializers import PrescriberSerializer, PrescriptionSerializer

from .base import SoftDeleteModelViewSet, nested_list
from .prescription import PrescriptionViewSet
from .tags import tagged

SCOPES = crud_scopes(Scope.PRESCRIBERS_READ, Scope.PRESCRIBERS_WRITE) | {
    "prescriptions": {Scope.PRESCRIPTIONS_READ},
}


@extend_schema_view(**tagged("Prescribers", "prescriptions", scopes=SCOPES))
class PrescriberViewSet(SoftDeleteModelViewSet):
    repository = repositories.prescribers
    service = services.prescribers
    required_scopes = SCOPES
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

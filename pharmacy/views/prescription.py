from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.response import Response

from pharmacy import repositories, services
from pharmacy.filters import PrescriptionFilter
from pharmacy.serializers import PrescriptionSerializer

from .base import SoftDeleteModelViewSet
from .tags import tagged


@extend_schema_view(**tagged("Prescriptions", "cancel"))
class PrescriptionViewSet(SoftDeleteModelViewSet):
    repository = repositories.prescriptions
    service = services.prescriptions
    serializer_class = PrescriptionSerializer
    filterset_class = PrescriptionFilter
    search_fields = ["medication__name", "patient__last_name"]
    ordering_fields = ["written_at", "created_at"]

    def update(self, request, *args, **kwargs):
        # A lapsed prescription is refused as terminal rather than moved to
        # on_hold, so expiry is recorded before the serializer sees the row.
        self.service.expire_if_due(self.ctx, self.get_object().pk)
        return super().update(request, *args, **kwargs)

    @extend_schema(
        summary="Cancel a prescription",
        description=(
            "A clinical state change: the prescription stays visible with "
            "status=cancelled. Distinct from DELETE, which removes the record "
            "from the API. 409 if already terminal, or if a fill is in progress."
        ),
        request=None,
        responses=PrescriptionSerializer,
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        # get_object() answers the 404 (and, later, permissions); the service
        # re-reads the row under its own lock.
        prescription = self.service.cancel(self.ctx, self.get_object().pk)
        return Response(self.get_serializer(prescription).data)

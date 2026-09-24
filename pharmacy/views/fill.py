from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from pharmacy import repositories, services
from pharmacy.filters import FillFilter
from pharmacy.serializers import FillSerializer

from .base import ScopedViewSetMixin, ServiceViewSetMixin
from .tags import PRESCRIPTION_ID, tagged

TRANSITIONS = ["verify", "unverify", "complete", "cancel"]


@extend_schema_view(**tagged("Fills", parameters=[PRESCRIPTION_ID], actions=["list", "create"]))
class PrescriptionFillsViewSet(
    ScopedViewSetMixin,
    ServiceViewSetMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """The collection: /prescriptions/{prescription_id}/fills/. A fill is
    listed and started through its prescription; once it exists it has an
    id of its own and lives at /fills/{id}/."""

    parent_repository = repositories.prescriptions
    parent_url_kwarg = "prescription_id"
    parent_field = "prescription"
    repository = repositories.fills
    service = services.fills
    serializer_class = FillSerializer
    filterset_class = FillFilter
    ordering_fields = ["sequence", "created_at"]


@extend_schema_view(**tagged("Fills", *TRANSITIONS, actions=["retrieve", "partial_update"]))
class FillViewSet(
    ServiceViewSetMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """One fill: /fills/{id}/. No PUT: PATCH edits the two writable fields
    of a pending fill, and the status moves only through the actions."""

    repository = repositories.fills
    service = services.fills
    serializer_class = FillSerializer
    http_method_names = ["get", "patch", "post", "head", "options"]

    def _transition(self, name):
        # get_object() answers the 404 (and, later, permissions); the service
        # re-reads the fill under the prescription's lock.
        fill = getattr(self.service, name)(self.ctx, self.get_object())
        return Response(self.get_serializer(fill).data)

    @extend_schema(
        summary="Verify a fill",
        description=(
            "pending → verified, recording the X-Staff-Id pharmacist as verified_by. "
            "400 when the header is missing or names someone who is not a pharmacist; "
            "409 from any other status."
        ),
        request=None,
        responses=FillSerializer,
    )
    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        return self._transition("verify")

    @extend_schema(
        summary="Send a verified fill back",
        description="verified → pending, clearing verified_by. 409 from any other status.",
        request=None,
        responses=FillSerializer,
    )
    @action(detail=True, methods=["post"])
    def unverify(self, request, pk=None):
        return self._transition("unverify")

    @extend_schema(
        summary="Complete a fill",
        description=(
            "verified → completed: stamps dispensed_at and, when this fill dispenses "
            "the last of the authorized quantity, completes the prescription. 409 from "
            "any other status, or when the prescription is no longer active."
        ),
        request=None,
        responses=FillSerializer,
    )
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        return self._transition("complete")

    @extend_schema(
        summary="Cancel a fill",
        description=(
            "pending or verified → cancelled, releasing the quantity it held. Allowed "
            "whatever the prescription's status. 409 once terminal."
        ),
        request=None,
        responses=FillSerializer,
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self._transition("cancel")

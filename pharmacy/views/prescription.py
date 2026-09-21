from django.db import transaction
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.response import Response

from pharmacy.exceptions import Conflict
from pharmacy.filters import PrescriptionFilter
from pharmacy.models import Prescription, PrescriptionStatus
from pharmacy.serializers import PrescriptionSerializer

from .base import SoftDeleteModelViewSet
from .tags import tagged


@extend_schema_view(**tagged("Prescriptions", "fill", "cancel"))
class PrescriptionViewSet(SoftDeleteModelViewSet):
    serializer_class = PrescriptionSerializer
    filterset_class = PrescriptionFilter
    search_fields = ["medication__name", "patient__last_name"]
    ordering_fields = ["written_at", "created_at"]

    def get_queryset(self):
        # Without select_related, nested read serializers turn one list request
        # into 3N queries.
        return Prescription.objects.select_related(
            "patient",
            "prescriber",
            "medication",
            "patient__address",
            "prescriber__address",
            "pharmacy_location",
            "pharmacy_location__pharmacy",
            "pharmacy_location__address",
        )

    @extend_schema(
        summary="Record a fill",
        description=(
            "Increments fills_used and completes the prescription on the last "
            "fill. 409 when the prescription is not active, is already fully "
            "filled, has expired, or has no pharmacy location."
        ),
        request=None,
        responses=PrescriptionSerializer,
    )
    @action(detail=True, methods=["post"])
    def fill(self, request, pk=None):
        prescription = self.get_object()

        # Expiry is set lazily here rather than by a scheduled sweep, and it is
        # committed BEFORE the atomic block below: raising inside that block
        # rolls its own writes back, which would leave the record 'active'.
        if prescription.status == PrescriptionStatus.ACTIVE and prescription.is_expired:
            prescription.status = PrescriptionStatus.EXPIRED
            prescription.save(update_fields=["status", "updated_at"])
            raise Conflict(
                "This prescription expired and cannot be filled.",
                details={
                    "current_status": prescription.status,
                    "expires_at": prescription.expires_at.isoformat(),
                },
            )

        with transaction.atomic():
            prescription = Prescription.objects.select_for_update().get(pk=prescription.pk)

            if prescription.status != PrescriptionStatus.ACTIVE:
                raise Conflict(
                    f"Only an active prescription can be filled; this one is "
                    f"'{prescription.status}'.",
                    details={"current_status": prescription.status},
                )

            if prescription.pharmacy_location_id is None:
                raise Conflict(
                    "This prescription has no pharmacy location; a fill must name "
                    "a dispensing site.",
                    details={"field": "pharmacy_location_id"},
                )

            if prescription.fills_remaining <= 0:
                raise Conflict(
                    "Every authorized fill has already been used.",
                    details={
                        "fills_authorized": prescription.fills_authorized,
                        "fills_used": prescription.fills_used,
                    },
                )

            prescription.fills_used += 1
            if prescription.fills_remaining == 0:
                prescription.status = PrescriptionStatus.COMPLETED
            prescription.save(update_fields=["fills_used", "status", "updated_at"])

        return Response(self.get_serializer(prescription).data)

    @extend_schema(
        summary="Cancel a prescription",
        description=(
            "A clinical state change: the prescription stays visible with "
            "status=cancelled. Distinct from DELETE, which removes the record "
            "from the API. 409 if already terminal."
        ),
        request=None,
        responses=PrescriptionSerializer,
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        prescription = self.get_object()
        if prescription.is_terminal:
            raise Conflict(
                f"A '{prescription.status}' prescription cannot be cancelled.",
                details={"current_status": prescription.status},
            )
        prescription.status = PrescriptionStatus.CANCELLED
        prescription.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(prescription).data)

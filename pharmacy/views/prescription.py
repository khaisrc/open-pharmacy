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

    def _locked(self, prescription):
        """Re-read one row under a lock, keeping the joins the response needs.

        `of=("self",)` locks the prescription row only: the nullable address
        joins are outer joins, which Postgres refuses to lock, and nothing here
        writes to the related rows anyway.
        """
        return self.get_queryset().select_for_update(of=("self",)).get(pk=prescription.pk)

    def update(self, request, *args, **kwargs):
        # A lapsed prescription is refused as terminal rather than moved to
        # on_hold, so expiry is recorded before the serializer sees the row.
        with transaction.atomic():
            self._locked(self.get_object()).expire_if_due()
        return super().update(request, *args, **kwargs)

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

        # The refusal is raised only after the block commits: raising inside it
        # would roll back the expiry that expire_if_due just recorded.
        with transaction.atomic():
            prescription = self._locked(prescription)
            if prescription.expire_if_due():
                refusal = Conflict(
                    "This prescription expired and cannot be filled.",
                    code="expired",
                    details={
                        "status": prescription.status,
                        "expires_at": prescription.expires_at.isoformat(),
                    },
                )
            elif prescription.status != PrescriptionStatus.ACTIVE:
                refusal = Conflict(
                    f"Only an active prescription can be filled; this one is "
                    f"'{prescription.status}'.",
                    code="not_active",
                    details={"status": prescription.status},
                )
            elif prescription.fills_remaining <= 0:
                refusal = Conflict(
                    "Every authorized fill has already been used.",
                    code="no_fills_remaining",
                    details={
                        "status": prescription.status,
                        "fills_authorized": prescription.fills_authorized,
                        "fills_used": prescription.fills_used,
                    },
                )
            else:
                refusal = None
                prescription.fills_used += 1
                if prescription.fills_remaining == 0:
                    prescription.status = PrescriptionStatus.COMPLETED
                prescription.save(update_fields=["fills_used", "status", "updated_at"])
        if refusal:
            raise refusal
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

        # Locked, so a fill that completes the prescription concurrently is seen
        # here as 'completed' rather than overwritten with 'cancelled'.
        with transaction.atomic():
            prescription = self._locked(prescription)
            prescription.expire_if_due()
            if prescription.is_terminal:
                refusal = Conflict(
                    f"A '{prescription.status}' prescription cannot be cancelled.",
                    code="already_terminal",
                    details={"status": prescription.status},
                )
            else:
                refusal = None
                prescription.status = PrescriptionStatus.CANCELLED
                prescription.save(update_fields=["status", "updated_at"])
        if refusal:
            raise refusal
        return Response(self.get_serializer(prescription).data)

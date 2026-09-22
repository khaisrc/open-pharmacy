from django.utils import timezone
from rest_framework import serializers

from pharmacy import repositories
from pharmacy.exceptions import Conflict
from pharmacy.models import ALLOWED_TRANSITIONS, Prescription, PrescriptionStatus

from .medication import MedicationSummarySerializer
from .patient import PatientSummarySerializer
from .pharmacy_location import PharmacyLocationSummarySerializer
from .prescriber import PrescriberSummarySerializer

# A status a client cannot PATCH is not necessarily a status it cannot reach --
# it just does not get there through this endpoint. Each refusal carries the
# route that does own the transition, so a client is not left guessing.
TRANSITION_HINTS = {
    PrescriptionStatus.CANCELLED: (
        "POST /prescriptions/{id}/cancel/ is what cancels a prescription."
    ),
    PrescriptionStatus.COMPLETED: (
        "A prescription completes on its own when a fill dispenses the last of its "
        "authorized quantity: POST /prescriptions/{id}/fills/{fill_id}/complete/."
    ),
    PrescriptionStatus.EXPIRED: (
        "A prescription expires on its own once expires_at has passed; the next "
        "fill, cancel or update records it."
    ),
}


class PrescriptionSerializer(serializers.ModelSerializer):
    """Writes flat, reads nested, so rendering one prescription is one request."""

    patient = PatientSummarySerializer(read_only=True)
    patient_id = serializers.PrimaryKeyRelatedField(
        source="patient", queryset=repositories.patients.live(), write_only=True
    )
    prescriber = PrescriberSummarySerializer(read_only=True)
    prescriber_id = serializers.PrimaryKeyRelatedField(
        source="prescriber", queryset=repositories.prescribers.live(), write_only=True
    )
    medication = MedicationSummarySerializer(read_only=True)
    medication_id = serializers.PrimaryKeyRelatedField(
        source="medication", queryset=repositories.medications.live(), write_only=True
    )
    pharmacy_location = PharmacyLocationSummarySerializer(read_only=True)
    pharmacy_location_id = serializers.PrimaryKeyRelatedField(
        source="pharmacy_location",
        queryset=repositories.pharmacy_locations.live(),
        # Optional on the wire, not in the database: validate() substitutes the
        # home pharmacy location when a client leaves it out.
        required=False,
        write_only=True,
    )
    rx_number = serializers.IntegerField(read_only=True)
    # Every quantity is derived from the fills; the integers are the record
    # and the two fractions are for display, to eight places.
    quantity_authorized = serializers.IntegerField(read_only=True)
    quantity_dispensed = serializers.IntegerField(read_only=True)
    quantity_in_progress = serializers.IntegerField(read_only=True)
    quantity_remaining = serializers.IntegerField(read_only=True)
    fills_used = serializers.DecimalField(
        max_digits=20, decimal_places=8, coerce_to_string=False, read_only=True
    )
    fills_remaining = serializers.DecimalField(
        max_digits=20, decimal_places=8, coerce_to_string=False, read_only=True
    )

    class Meta:
        model = Prescription
        fields = [
            "id",
            "patient",
            "patient_id",
            "prescriber",
            "prescriber_id",
            "medication",
            "medication_id",
            "pharmacy_location",
            "pharmacy_location_id",
            "rx_number",
            "intake_source",
            "dose_amount",
            "quantity",
            "fills_authorized",
            "quantity_authorized",
            "quantity_dispensed",
            "quantity_in_progress",
            "quantity_remaining",
            "fills_used",
            "fills_remaining",
            "instructions",
            "status",
            "written_at",
            "expires_at",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_intake_source(self, value):
        # How the order arrived is a fact about its entry; it does not change.
        if self.instance is not None and value != self.instance.intake_source:
            raise serializers.ValidationError(
                "The intake source is set when the prescription is entered and cannot be changed."
            )
        return value

    def validate_status(self, value):
        if self.instance is None:
            if value != PrescriptionStatus.ACTIVE:
                raise serializers.ValidationError(
                    f"A new prescription starts as '{PrescriptionStatus.ACTIVE}'."
                )
            return value

        current = self.instance.status
        if value == current:
            return value

        allowed = ALLOWED_TRANSITIONS.get(current, set())
        if value not in allowed:
            raise serializers.ValidationError(
                {
                    "message": f"Cannot move a prescription from '{current}' to '{value}'.",
                    "status": current,
                    "allowed_targets": sorted(allowed),
                    "hint": self._transition_hint(current, value, allowed),
                }
            )
        return value

    @staticmethod
    def _transition_hint(current, target, allowed):
        if target in TRANSITION_HINTS:
            return TRANSITION_HINTS[target]
        if not allowed:
            return f"A '{current}' prescription is terminal; its status no longer changes."
        return f"From '{current}' this endpoint can set only: {', '.join(sorted(allowed))}."

    def validate(self, attrs):
        if self.instance is not None and self.instance.is_terminal:
            self._reject_changes_to_terminal(attrs)

        if self.instance is None and attrs.get("pharmacy_location") is None:
            # This service backs a single pharmacy, so a prescription written
            # through it is dispensed at that pharmacy's location and a client
            # should not have to say so.
            location = repositories.pharmacy_locations.home()
            if location is None:
                raise serializers.ValidationError(
                    {
                        "pharmacy_location_id": (
                            "This prescription names no dispensing site and the "
                            "configured home pharmacy location was not found."
                        )
                    }
                )
            attrs["pharmacy_location"] = location

        if self.instance is None and not attrs.get("written_at"):
            # Pinned here rather than left to the model default so the value
            # expires_at is checked against is the value that gets saved;
            # otherwise a past expires_at with no written_at would skip this
            # check and trip the database constraint instead.
            attrs["written_at"] = timezone.now()
        written_at = attrs.get("written_at") or getattr(self.instance, "written_at", None)
        expires_at = attrs.get("expires_at", getattr(self.instance, "expires_at", None))
        if written_at and expires_at and expires_at <= written_at:
            raise serializers.ValidationError(
                {"expires_at": "Expiry must be after the prescription was written."}
            )

        if self.instance is not None:
            self._reject_authorizing_less_than_dispensed(attrs)
        return attrs

    def _reject_authorizing_less_than_dispensed(self, attrs):
        """The authorized quantity cannot drop below what the fills already
        hold, dispensed or in progress. Reported on whichever of the two
        fields the request changed."""
        quantity = attrs.get("quantity", self.instance.quantity)
        fills_authorized = attrs.get("fills_authorized", self.instance.fills_authorized)
        dispensed, in_progress = self.instance.fill_totals()
        held = dispensed + in_progress
        if quantity * fills_authorized < held:
            changed = [f for f in ("quantity", "fills_authorized") if f in attrs]
            message = (
                f"Cannot authorize {quantity * fills_authorized} units when the fills "
                f"already hold {held} ({dispensed} dispensed, {in_progress} in progress)."
            )
            fields = changed or ["fills_authorized"]
            raise serializers.ValidationError(dict.fromkeys(fields, message))

    def _reject_changes_to_terminal(self, attrs):
        """A completed, cancelled or expired prescription is a closed record.

        validate_status already refuses a status change out of a terminal
        state; this covers every other field, so a completed prescription
        cannot quietly gain fills or move to another patient. A write that
        changes nothing is allowed through, so a PUT of the current body is
        still a 200.
        """
        changed = sorted(
            field for field, value in attrs.items() if getattr(self.instance, field) != value
        )
        if changed:
            raise Conflict(
                f"A '{self.instance.status}' prescription can no longer be changed.",
                code="already_terminal",
                details={"status": self.instance.status, "fields": changed},
            )

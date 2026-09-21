from django.conf import settings
from rest_framework import serializers

from pharmacy.models import (
    ALLOWED_TRANSITIONS,
    Medication,
    Patient,
    PharmacyLocation,
    Prescriber,
    Prescription,
    PrescriptionStatus,
)

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
        "A prescription completes on its own when POST /prescriptions/{id}/fill/ "
        "uses the last authorized fill."
    ),
    PrescriptionStatus.EXPIRED: (
        "A prescription expires on its own once expires_at has passed; the next "
        "POST /prescriptions/{id}/fill/ records it."
    ),
}


class PrescriptionSerializer(serializers.ModelSerializer):
    """Writes flat, reads nested, so rendering one prescription is one request."""

    patient = PatientSummarySerializer(read_only=True)
    patient_id = serializers.PrimaryKeyRelatedField(
        source="patient", queryset=Patient.objects.all(), write_only=True
    )
    prescriber = PrescriberSummarySerializer(read_only=True)
    prescriber_id = serializers.PrimaryKeyRelatedField(
        source="prescriber", queryset=Prescriber.objects.all(), write_only=True
    )
    medication = MedicationSummarySerializer(read_only=True)
    medication_id = serializers.PrimaryKeyRelatedField(
        source="medication", queryset=Medication.objects.all(), write_only=True
    )
    pharmacy_location = PharmacyLocationSummarySerializer(read_only=True)
    pharmacy_location_id = serializers.PrimaryKeyRelatedField(
        source="pharmacy_location",
        queryset=PharmacyLocation.objects.all(),
        # Optional on the wire, not in the database: validate() substitutes the
        # home pharmacy location when a client leaves it out.
        required=False,
        write_only=True,
    )
    fills_remaining = serializers.IntegerField(read_only=True)

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
            "dose_amount",
            "quantity",
            "fills_authorized",
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
        read_only_fields = ["id", "fills_used", "created_at", "updated_at"]

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
                    "current_status": current,
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

    def _home_pharmacy_location(self):
        """The dispensing site to use when a client names none.

        This service backs a single pharmacy, so a prescription written through
        it is dispensed at that pharmacy's location and a client should not have
        to say so. The location is read from HOME_PHARMACY_LOCATION_NCPDP_ID
        rather than inferred from "whichever one exists", so a second store
        opening -- or a test creating its own location -- cannot change which
        site a prescription is written against.
        """
        return PharmacyLocation.objects.filter(
            ncpdp_id=settings.HOME_PHARMACY_LOCATION_NCPDP_ID
        ).first()

    def validate(self, attrs):
        if self.instance is None and attrs.get("pharmacy_location") is None:
            location = self._home_pharmacy_location()
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

        written_at = attrs.get("written_at") or getattr(self.instance, "written_at", None)
        expires_at = attrs.get("expires_at", getattr(self.instance, "expires_at", None))
        if written_at and expires_at and expires_at <= written_at:
            raise serializers.ValidationError(
                {"expires_at": "Expiry must be after the prescription was written."}
            )

        authorized = attrs.get("fills_authorized", getattr(self.instance, "fills_authorized", 1))
        used = getattr(self.instance, "fills_used", 0)
        if authorized < used:
            raise serializers.ValidationError(
                {"fills_authorized": f"Cannot authorize fewer fills than the {used} already used."}
            )
        return attrs

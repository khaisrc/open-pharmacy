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
        required=False,
        allow_null=True,
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
                }
            )
        return value

    def validate(self, attrs):
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

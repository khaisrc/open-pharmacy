from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from pharmacy.exceptions import Conflict
from pharmacy.models import Medication
from pharmacy.validators import normalize_ndc


class MedicationSerializer(serializers.ModelSerializer):
    formatted_ndc = serializers.CharField(read_only=True)
    # Declared explicitly: the model field is max_length=11, and that validator
    # would reject a segmented "0002-8215-01" before validate_ndc can normalize it.
    ndc = serializers.CharField(max_length=13)

    class Meta:
        model = Medication
        fields = [
            "id",
            "name",
            "ndc",
            "formatted_ndc",
            "strength",
            "form",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_ndc(self, value):
        try:
            normalized = normalize_ndc(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc

        existing = Medication.objects.filter(ndc=normalized)
        if self.instance:
            existing = existing.exclude(pk=self.instance.pk)
        conflict = existing.first()
        if conflict:
            raise Conflict(
                "A live medication with this NDC already exists.",
                details={"field": "ndc", "ndc": normalized, "medication_id": str(conflict.pk)},
            )
        return normalized


class MedicationSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Medication
        fields = ["id", "name", "ndc", "strength", "form"]

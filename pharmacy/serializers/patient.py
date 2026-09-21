from django.utils import timezone
from rest_framework import serializers

from pharmacy.models import Address, Patient

from .address import AddressSummarySerializer


class PatientSerializer(serializers.ModelSerializer):
    # Flat in, nested out: a client writes an id and reads a whole address.
    address = AddressSummarySerializer(read_only=True)
    address_id = serializers.PrimaryKeyRelatedField(
        source="address",
        queryset=Address.objects.all(),
        required=False,
        allow_null=True,
        write_only=True,
    )
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Patient
        fields = [
            "id",
            "first_name",
            "last_name",
            "middle_name",
            "suffix",
            "full_name",
            "dob",
            "gender",
            "phone_number",
            "email",
            "sms_opt_in",
            "address",
            "address_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_dob(self, value):
        # Mirrors Patient.clean(); enforced here too because DRF does not call
        # full_clean() on its own.
        if value and value > timezone.localdate():
            raise serializers.ValidationError("Date of birth cannot be in the future.")
        return value


class PatientSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Patient
        fields = ["id", "first_name", "last_name", "dob"]

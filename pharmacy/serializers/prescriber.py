from rest_framework import serializers

from pharmacy.models import Address, Prescriber
from pharmacy.validators import validate_npi

from .address import AddressSummarySerializer
from .base import reject_live_duplicate


class PrescriberSerializer(serializers.ModelSerializer):
    address = AddressSummarySerializer(read_only=True)
    address_id = serializers.PrimaryKeyRelatedField(
        source="address",
        queryset=Address.objects.all(),
        required=False,
        allow_null=True,
        write_only=True,
    )
    full_name = serializers.CharField(read_only=True)
    # Declared explicitly to drop the UniqueValidator DRF 3.18 generates for the
    # conditional UniqueConstraint. That validator runs inside run_validation(),
    # before validate_npi, and would surface a duplicate as a bare 400 "unique"
    # instead of the 409 that names the conflicting prescriber.
    npi = serializers.CharField(max_length=10)

    class Meta:
        model = Prescriber
        fields = [
            "id",
            "first_name",
            "last_name",
            "middle_name",
            "suffix",
            "full_name",
            "practitioner_type",
            "npi",
            "address",
            "address_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_npi(self, value):
        validate_npi(value)
        reject_live_duplicate(
            Prescriber.objects.filter(npi=value),
            self.instance,
            record="prescriber",
            field="npi",
            label="NPI",
            value=value,
        )
        return value


class PrescriberSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Prescriber
        fields = ["id", "first_name", "last_name", "npi", "practitioner_type"]

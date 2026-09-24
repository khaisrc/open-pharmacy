from rest_framework import serializers

from pharmacy.models import Fill

from .pharmacy_location import PharmacyLocationSummarySerializer
from .staff import StaffSummarySerializer


class FillSerializer(serializers.ModelSerializer):
    """Two fields are writable -- the quantity and the days' supply -- and
    only on a pending fill; everything else is set by the workflow. The
    people come from the X-Staff-Id header, the status from the actions."""

    rx_number = serializers.IntegerField(source="prescription.rx_number", read_only=True)
    pharmacy_location = PharmacyLocationSummarySerializer(read_only=True)
    operator = StaffSummarySerializer(read_only=True)
    verified_by = StaffSummarySerializer(read_only=True)
    # Optional on create: the service defaults it to one written fill's worth
    # or whatever is left. The range is checked in the service, under the
    # lock, against totals that may be changing.
    quantity_dispensed = serializers.IntegerField(min_value=1, required=False)
    days_supply = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    is_partial = serializers.BooleanField(read_only=True)

    class Meta:
        model = Fill
        fields = [
            "id",
            "sequence",
            "status",
            "rx_number",
            "pharmacy_location",
            "operator",
            "verified_by",
            "quantity_dispensed",
            "is_partial",
            "days_supply",
            "dispensed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "sequence",
            "status",
            "dispensed_at",
            "created_at",
            "updated_at",
        ]

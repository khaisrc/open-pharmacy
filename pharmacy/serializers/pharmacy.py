from rest_framework import serializers

from pharmacy.models import RX_NUMBER_MAX, Pharmacy, RxCounter


class RxCounterSerializer(serializers.ModelSerializer):
    """Where the pharmacy's Rx numbers stand. Null on a pharmacy that has not
    been onboarded, which cannot create prescriptions."""

    class Meta:
        model = RxCounter
        fields = ["last_issued", "step"]


class PharmacySerializer(serializers.ModelSerializer):
    # Annotated by the repository, never stored: a counter column would drift
    # the first time a location is soft-deleted.
    location_count = serializers.IntegerField(read_only=True)
    rx_counter = RxCounterSerializer(read_only=True, allow_null=True)
    # Onboarding, on create or later: the value issuing starts after (the
    # first number is seed + step) and the increment. Write-only; the state
    # reads back as rx_counter. A seed never moves a counter backwards.
    rx_number_seed = serializers.IntegerField(
        min_value=0, max_value=RX_NUMBER_MAX - 1, required=False, write_only=True
    )
    step = serializers.IntegerField(min_value=1, max_value=32767, required=False, write_only=True)

    class Meta:
        model = Pharmacy
        fields = [
            "id",
            "name",
            "legal_name",
            "phone_number",
            "email",
            "website",
            "location_count",
            "rx_counter",
            "rx_number_seed",
            "step",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class PharmacySummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Pharmacy
        fields = ["id", "name"]

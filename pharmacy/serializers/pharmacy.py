from rest_framework import serializers

from pharmacy.models import Pharmacy


class PharmacySerializer(serializers.ModelSerializer):
    # Annotated by the viewset, never stored: a counter column would drift the
    # first time a location is soft-deleted.
    location_count = serializers.IntegerField(read_only=True)

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
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class PharmacySummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Pharmacy
        fields = ["id", "name"]

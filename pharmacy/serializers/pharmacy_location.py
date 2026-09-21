from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from pharmacy.exceptions import Conflict
from pharmacy.models import Address, Pharmacy, PharmacyLocation
from pharmacy.validators import normalize_ncpdp, validate_npi

from .address import AddressSummarySerializer
from .pharmacy import PharmacySummarySerializer


class PharmacyLocationSerializer(serializers.ModelSerializer):
    pharmacy = PharmacySummarySerializer(read_only=True)
    pharmacy_id = serializers.PrimaryKeyRelatedField(
        source="pharmacy", queryset=Pharmacy.objects.all(), write_only=True
    )
    address = AddressSummarySerializer(read_only=True)
    address_id = serializers.PrimaryKeyRelatedField(
        source="address", queryset=Address.objects.all(), write_only=True
    )
    # Same reason as Medication.ndc: separators have to survive field validation
    # long enough for validate_ncpdp_id to strip them.
    ncpdp_id = serializers.CharField(max_length=9)
    # Same reason as Prescriber.npi: keep duplicate detection in validate_npi so
    # it returns 409 rather than DRF UniqueValidator's 400.
    npi = serializers.CharField(max_length=10, required=False, allow_blank=True)

    class Meta:
        model = PharmacyLocation
        fields = [
            "id",
            "pharmacy",
            "pharmacy_id",
            "store_number",
            "ncpdp_id",
            "npi",
            "dea_number",
            "location_type",
            "phone_number",
            "fax_number",
            "email",
            "address",
            "address_id",
            "is_24_hour",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_ncpdp_id(self, value):
        try:
            normalized = normalize_ncpdp(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc

        existing = PharmacyLocation.objects.filter(ncpdp_id=normalized)
        if self.instance:
            existing = existing.exclude(pk=self.instance.pk)
        conflict = existing.first()
        if conflict:
            raise Conflict(
                "A live pharmacy location with this NCPDP ID already exists.",
                details={
                    "field": "ncpdp_id",
                    "ncpdp_id": normalized,
                    "pharmacy_location_id": str(conflict.pk),
                },
            )
        return normalized

    def validate_npi(self, value):
        if not value:
            return value
        validate_npi(value)
        existing = PharmacyLocation.objects.filter(npi=value)
        if self.instance:
            existing = existing.exclude(pk=self.instance.pk)
        conflict = existing.first()
        if conflict:
            raise Conflict(
                "A live pharmacy location with this NPI already exists.",
                details={"field": "npi", "npi": value, "pharmacy_location_id": str(conflict.pk)},
            )
        return value

    def validate(self, attrs):
        # Store numbers are unique per pharmacy, not globally: two chains both
        # having a store 41 is normal. Blanks never collide.
        pharmacy = attrs.get("pharmacy") or getattr(self.instance, "pharmacy", None)
        store_number = attrs.get("store_number", getattr(self.instance, "store_number", ""))
        if pharmacy and store_number:
            existing = PharmacyLocation.objects.filter(pharmacy=pharmacy, store_number=store_number)
            if self.instance:
                existing = existing.exclude(pk=self.instance.pk)
            conflict = existing.first()
            if conflict:
                raise Conflict(
                    "This pharmacy already has a live location with that store number.",
                    details={
                        "field": "store_number",
                        "store_number": store_number,
                        "pharmacy_location_id": str(conflict.pk),
                    },
                )
        return attrs


class PharmacyLocationSummarySerializer(serializers.ModelSerializer):
    """Two levels deep on purpose: a store's identity is useless without the
    chain name."""

    pharmacy = PharmacySummarySerializer(read_only=True)
    address = AddressSummarySerializer(read_only=True)

    class Meta:
        model = PharmacyLocation
        fields = ["id", "store_number", "ncpdp_id", "phone_number", "pharmacy", "address"]

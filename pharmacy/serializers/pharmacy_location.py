from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from pharmacy.models import Address, PharmacyLocation, Staff
from pharmacy.models.pharmacy_location import pharmacist_in_charge_problem
from pharmacy.validators import normalize_ncpdp, validate_npi

from .address import AddressSummarySerializer
from .base import reject_live_duplicate
from .pharmacy import PharmacySummarySerializer
from .staff import StaffSummarySerializer


class PharmacyLocationSerializer(serializers.ModelSerializer):
    """The owning pharmacy comes from the URL, never the body: a location is a
    sub-resource of its pharmacy, so the viewset attaches it on create and a
    store cannot be moved to another chain by PATCH."""

    pharmacy = PharmacySummarySerializer(read_only=True)
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
    # The board-registered designation for the site, for defaulting and
    # validation. Not who is supervising at any moment.
    pharmacist_in_charge = StaffSummarySerializer(read_only=True)
    pharmacist_in_charge_id = serializers.PrimaryKeyRelatedField(
        source="pharmacist_in_charge",
        queryset=Staff.objects.all(),
        required=False,
        allow_null=True,
        write_only=True,
    )

    class Meta:
        model = PharmacyLocation
        fields = [
            "id",
            "pharmacy",
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
            "pharmacist_in_charge",
            "pharmacist_in_charge_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    @property
    def pharmacy_scope(self):
        """The pharmacy this write belongs to: the instance's on update, the
        one the nested route resolved on create."""
        if self.instance is not None:
            return self.instance.pharmacy
        return self.context.get("pharmacy")

    def validate_ncpdp_id(self, value):
        try:
            normalized = normalize_ncpdp(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc

        reject_live_duplicate(
            PharmacyLocation.objects.filter(ncpdp_id=normalized),
            self.instance,
            record="pharmacy location",
            field="ncpdp_id",
            label="NCPDP ID",
            value=normalized,
        )
        return normalized

    def validate_npi(self, value):
        if not value:
            return value
        validate_npi(value)
        reject_live_duplicate(
            PharmacyLocation.objects.filter(npi=value),
            self.instance,
            record="pharmacy location",
            field="npi",
            label="NPI",
            value=value,
        )
        return value

    def validate_pharmacist_in_charge_id(self, staff):
        if staff is None:
            return staff
        problem = pharmacist_in_charge_problem(staff, self.pharmacy_scope)
        if problem:
            raise serializers.ValidationError(problem)
        return staff

    def validate(self, attrs):
        # Store numbers are unique per pharmacy, not globally: two chains both
        # having a store 41 is normal. Blanks never collide.
        pharmacy = self.pharmacy_scope
        store_number = attrs.get("store_number", getattr(self.instance, "store_number", ""))
        if pharmacy and store_number:
            reject_live_duplicate(
                PharmacyLocation.objects.filter(pharmacy=pharmacy, store_number=store_number),
                self.instance,
                record="pharmacy location",
                field="store_number",
                label="store number",
                value=store_number,
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

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from pharmacy import repositories
from pharmacy.models import Staff
from pharmacy.models.staff import credential_problems
from pharmacy.validators import validate_npi, validate_us_state

from .base import reject_live_duplicate
from .pharmacy import PharmacySummarySerializer


class StaffSerializer(serializers.ModelSerializer):
    """The employing pharmacy comes from the URL, never the body: staff are a
    sub-resource of a pharmacy, so the viewset attaches it on create and a
    record cannot be moved to another employer by PATCH.

    A row with `external_user_id` came from an identity provider token and is that
    identity's record: read-only here except `is_active` (the service
    refuses the rest with 409). `email` and `roles` are the token's."""

    pharmacy = PharmacySummarySerializer(read_only=True)
    full_name = serializers.CharField(read_only=True)
    is_external = serializers.BooleanField(read_only=True)
    # Declared explicitly to drop the UniqueValidator DRF generates for the
    # conditional UniqueConstraint, which would report a duplicate as a bare
    # 400 before validate_npi can raise the 409 naming the existing record.
    npi = serializers.CharField(max_length=10, required=False, allow_blank=True)

    class Meta:
        model = Staff
        fields = [
            "id",
            "external_user_id",
            "email",
            "is_external",
            "first_name",
            "last_name",
            "middle_name",
            "suffix",
            "full_name",
            "role",
            "roles",
            "registration_state",
            "registration_number",
            "certification_body",
            "certification_number",
            "npi",
            "pharmacy",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "external_user_id", "email", "roles", "created_at", "updated_at"]

    def validate_registration_state(self, value):
        value = (value or "").strip().upper()
        if value:
            try:
                validate_us_state(value)
            except DjangoValidationError as exc:
                raise serializers.ValidationError(exc.messages) from exc
        return value

    def validate_registration_number(self, value):
        return (value or "").strip().upper()

    def validate_certification_number(self, value):
        return (value or "").strip()

    def validate_npi(self, value):
        if not value:
            return value
        validate_npi(value)
        reject_live_duplicate(
            repositories.staff.find(npi=value),
            self.instance,
            record="staff member",
            field="npi",
            label="NPI",
            value=value,
        )
        return value

    def validate(self, attrs):
        # A PATCH may carry one half of a pair; the other half is whatever the
        # record already holds.
        def current(field):
            return attrs.get(field, getattr(self.instance, field, ""))

        role = current("role")
        state, number = current("registration_state"), current("registration_number")
        problems = credential_problems(
            role, state, number, current("certification_body"), current("certification_number")
        )
        if problems:
            raise serializers.ValidationError(problems)

        if number:
            reject_live_duplicate(
                repositories.staff.find(registration_state=state, registration_number=number),
                self.instance,
                record="staff member",
                field="registration_number",
                label="state registration",
                value=number,
                extra={"registration_state": state},
            )
        return attrs


class StaffSummarySerializer(serializers.ModelSerializer):
    """What a board lookup needs, and what a record naming a person embeds."""

    class Meta:
        model = Staff
        fields = [
            "id",
            "first_name",
            "last_name",
            "email",
            "role",
            "registration_state",
            "registration_number",
        ]

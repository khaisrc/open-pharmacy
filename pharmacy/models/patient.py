from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from pharmacy.validators import validate_e164

from .base import TimeStampedModel


class Gender(models.TextChoices):
    """FHIR administrative gender. Clinical sex-at-birth is a separate concept
    and is not modelled here."""

    MALE = "male", "Male"
    FEMALE = "female", "Female"
    OTHER = "other", "Other"
    UNKNOWN = "unknown", "Unknown"


class Patient(TimeStampedModel):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)
    # A name component (Jr., III), never a credential.
    suffix = models.CharField(max_length=20, blank=True)
    dob = models.DateField()
    gender = models.CharField(max_length=16, choices=Gender.choices, default=Gender.UNKNOWN)
    phone_number = models.CharField(max_length=20, validators=[validate_e164])
    email = models.EmailField(blank=True)
    # Consent is something a patient grants, never something a default grants.
    sms_opt_in = models.BooleanField(default=False)
    address = models.ForeignKey(
        "pharmacy.Address",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="patients",
    )

    class Meta(TimeStampedModel.Meta):
        abstract = False
        indexes = [
            models.Index(fields=["last_name", "first_name"]),
            models.Index(fields=["dob"]),
            models.Index(fields=["email"]),
        ]

    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name, self.suffix]
        return " ".join(part for part in parts if part)

    def clean(self):
        # Not a CheckConstraint: Postgres does not allow non-deterministic
        # functions such as CURRENT_DATE inside CHECK.
        super().clean()
        if self.dob and self.dob > timezone.localdate():
            raise ValidationError({"dob": "Date of birth cannot be in the future."})

    def __str__(self):
        return self.full_name

from django.db import models
from django.db.models import Q

from pharmacy.validators import validate_npi

from .base import TimeStampedModel


class PractitionerType(models.TextChoices):
    MD = "MD", "Doctor of Medicine"
    DO = "DO", "Doctor of Osteopathic Medicine"
    NP = "NP", "Nurse Practitioner"
    PA = "PA", "Physician Assistant"
    DDS = "DDS", "Doctor of Dental Surgery"
    DMD = "DMD", "Doctor of Dental Medicine"
    DPM = "DPM", "Doctor of Podiatric Medicine"
    OD = "OD", "Doctor of Optometry"
    DVM = "DVM", "Doctor of Veterinary Medicine"
    PHARMD = "PHARMD", "Doctor of Pharmacy"
    RPH = "RPH", "Registered Pharmacist"
    CNM = "CNM", "Certified Nurse-Midwife"
    CRNA = "CRNA", "Certified Registered Nurse Anesthetist"
    OTHER = "OTHER", "Other"


class Prescriber(TimeStampedModel):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)
    # suffix is a name component (Jr., III); practitioner_type is the
    # professional credential. Mixing them made "Jane Smith, MD, Jr."
    # unrepresentable and name sorting unreliable.
    suffix = models.CharField(max_length=20, blank=True)
    practitioner_type = models.CharField(
        max_length=16, choices=PractitionerType.choices, default=PractitionerType.MD
    )
    npi = models.CharField(max_length=10, validators=[validate_npi])
    address = models.ForeignKey(
        "pharmacy.Address",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="prescribers",
    )

    class Meta(TimeStampedModel.Meta):
        abstract = False
        constraints = [
            # Partial: a soft-deleted prescriber must not hold their NPI against
            # a live one.
            models.UniqueConstraint(
                fields=["npi"], condition=Q(deleted__isnull=True), name="uniq_live_npi"
            )
        ]
        indexes = [
            models.Index(fields=["last_name", "first_name"]),
            models.Index(fields=["npi"]),
        ]

    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name, self.suffix]
        return " ".join(part for part in parts if part)

    def __str__(self):
        return f"{self.full_name}, {self.practitioner_type}"

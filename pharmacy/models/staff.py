"""A credentialed person working at a pharmacy.

This is the domain record a state board can look up -- the person whose name
a transfer record names -- and it is deliberately separate from auth.User,
which is an operator credential: a way to sign in, not a person. Nothing
here links the two.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from pharmacy.validators import validate_npi, validate_us_state

from .base import TimeStampedModel


class StaffRole(models.TextChoices):
    PHARMACIST = "pharmacist", "Pharmacist"
    CERTIFIED_TECHNICIAN = "certified_technician", "Certified pharmacy technician"
    TECHNICIAN = "technician", "Pharmacy technician"
    INTERN = "intern", "Pharmacy intern"


class CertificationBody(models.TextChoices):
    """National technician certification. A board verifies the state
    registration; these are a different credential from a different body, so
    the two are recorded separately and neither stands in for the other."""

    PTCB = "PTCB", "Pharmacy Technician Certification Board"
    NHA = "NHA", "National Healthcareer Association"


# Every state licenses pharmacists and a certified technician is registered by
# definition; plain technicians and interns are registered in most states but
# not all, so for them the registration is optional.
ROLES_REQUIRING_REGISTRATION = frozenset({StaffRole.PHARMACIST, StaffRole.CERTIFIED_TECHNICIAN})
# "Certified" means holding a PTCB or NHA certification; without one the role
# is simply technician.
ROLES_REQUIRING_CERTIFICATION = frozenset({StaffRole.CERTIFIED_TECHNICIAN})


class Staff(TimeStampedModel):
    """A pharmacist, technician or intern employed by a pharmacy.

    `is_active` and soft delete mean different things. An inactive record is
    a person who has left or whose registration lapsed: historical records
    will still name them, so the row stays resolvable. DELETE is for a record
    that should never have existed.
    """

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)
    # A name component (Jr., III), never a credential; role carries that.
    suffix = models.CharField(max_length=20, blank=True)
    role = models.CharField(max_length=24, choices=StaffRole.choices)
    # The state board registration: both halves or neither, since a number
    # without its issuer is not something a board can look up.
    registration_state = models.CharField(max_length=2, blank=True, validators=[validate_us_state])
    registration_number = models.CharField(max_length=32, blank=True)
    certification_body = models.CharField(
        max_length=8, choices=CertificationBody.choices, blank=True
    )
    certification_number = models.CharField(max_length=32, blank=True)
    npi = models.CharField(max_length=10, blank=True, validators=[validate_npi])
    # The employer is the organization, not a site: a chain's technician
    # floats between its stores.
    pharmacy = models.ForeignKey(
        "pharmacy.Pharmacy", on_delete=models.PROTECT, related_name="staff"
    )
    is_active = models.BooleanField(default=True)

    class Meta(TimeStampedModel.Meta):
        abstract = False
        verbose_name_plural = "staff"
        constraints = [
            # Partial on both counts: a soft-deleted record must not hold a
            # registration against a live one, and the unregistered must never
            # collide with each other.
            models.UniqueConstraint(
                fields=["registration_state", "registration_number"],
                condition=Q(deleted__isnull=True) & ~Q(registration_number=""),
                name="uniq_live_staff_registration",
            ),
            models.UniqueConstraint(
                fields=["npi"],
                condition=Q(deleted__isnull=True) & ~Q(npi=""),
                name="uniq_live_staff_npi",
            ),
        ]
        indexes = [models.Index(fields=["last_name", "first_name"])]

    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name, self.suffix]
        return " ".join(part for part in parts if part)

    def clean(self):
        super().clean()
        problems = credential_problems(
            self.role,
            self.registration_state,
            self.registration_number,
            self.certification_body,
            self.certification_number,
        )
        if problems:
            raise ValidationError(problems)

    def save(self, *args, **kwargs):
        # Boards print registration numbers in upper case; normalizing keeps
        # the live-unique constraint from treating "rph.12345" as a second
        # registration.
        self.registration_state = (self.registration_state or "").strip().upper()
        self.registration_number = (self.registration_number or "").strip().upper()
        self.certification_number = (self.certification_number or "").strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.full_name} ({self.get_role_display()})"


def credential_problems(role, registration_state, registration_number, body, certification):
    """Field errors for a role's credentials, keyed like a serializer's.

    Shared by Staff.clean() and the serializer so the two cannot disagree. A
    half-filled pair is reported before a missing one: it is the more
    specific mistake.
    """
    errors = {}
    if bool(registration_state) != bool(registration_number):
        errors["registration_number"] = (
            "A state registration needs both the issuing state and the number."
        )
    elif not registration_number and role in ROLES_REQUIRING_REGISTRATION:
        errors["registration_number"] = (
            f"A {StaffRole(role).label.lower()} must hold a state registration."
        )
    if bool(body) != bool(certification):
        errors["certification_number"] = (
            "A certification needs both the certifying body and the number."
        )
    elif not certification and role in ROLES_REQUIRING_CERTIFICATION:
        errors["certification_number"] = (
            f"A {StaffRole(role).label.lower()} must hold a PTCB or NHA certification."
        )
    return errors

"""A credentialed person working at a pharmacy, and the identity a token names.

This is the domain record a state board can look up -- the person whose name
a transfer record names. It is also the identity record for the API: a
Logto access token's first appearance inserts a row carrying
`external_user_id` (the token's subject) and what the token says about the
person, and that row is never updated from a token again. Identity per
subject is immutable by policy -- a change of email, name, role or
registration is a new Logto user -- and `services.staff` refuses a token
whose claims no longer match its row. A row without `external_user_id` is a
directory entry typed in by hand; it can be named, but it cannot act.

Deliberately separate from auth.User, which is a way to sign into the Django
admin, not a person. Nothing links the two.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from pharmacy.validators import validate_npi, validate_us_state

from .base import TimeStampedModel


class StaffRole(models.TextChoices):
    ADMIN = "admin", "Administrator"
    PHARMACIST = "pharmacist", "Pharmacist"
    CERTIFIED_TECHNICIAN = "certified_technician", "Certified pharmacy technician"
    TECHNICIAN = "technician", "Pharmacy technician"
    INTERN = "intern", "Pharmacy intern"


# A person may hold several roles in Logto; the first of these they hold is
# the one `Staff.role` records and the rules key on. An admin is a superuser
# employed by no particular pharmacy.
ROLE_PRECEDENCE = [
    StaffRole.ADMIN,
    StaffRole.PHARMACIST,
    StaffRole.CERTIFIED_TECHNICIAN,
    StaffRole.TECHNICIAN,
    StaffRole.INTERN,
]
# Roles that may supervise an intern.
SUPERVISING_ROLES = frozenset(
    {StaffRole.PHARMACIST, StaffRole.CERTIFIED_TECHNICIAN, StaffRole.TECHNICIAN}
)


def highest_role(roles):
    """The role `Staff.role` records for a set of roles, or None if none is
    a StaffRole."""
    return next((role for role in ROLE_PRECEDENCE if role in roles), None)


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

    # The Logto subject this row was created from, and the email it carried.
    # Blank on a row typed in by hand.
    external_user_id = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)
    # A name component (Jr., III), never a credential; role carries that.
    suffix = models.CharField(max_length=20, blank=True)
    # `role` is the highest by ROLE_PRECEDENCE of `roles`, the full list the
    # token carried (a hand-typed row has a one-element list). The column is
    # kept because filters and the rules below key on it.
    role = models.CharField(max_length=24, choices=StaffRole.choices)
    roles = models.JSONField(default=list, blank=True)
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
    # floats between its stores. Null only for an admin, who works at none
    # in particular; clean() holds everyone else to it.
    pharmacy = models.ForeignKey(
        "pharmacy.Pharmacy",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="staff",
    )
    is_active = models.BooleanField(default=True)

    class Meta(TimeStampedModel.Meta):
        abstract = False
        verbose_name_plural = "staff"
        constraints = [
            # Partial: over live, active rows, and never over blanks. Active
            # rather than live so that a successor identity -- the new Logto
            # user created when a person's details change -- can hold the
            # registration once the old row is deactivated.
            models.UniqueConstraint(
                fields=["registration_state", "registration_number"],
                condition=Q(deleted__isnull=True, is_active=True) & ~Q(registration_number=""),
                name="uniq_live_staff_registration",
            ),
            models.UniqueConstraint(
                fields=["npi"],
                condition=Q(deleted__isnull=True, is_active=True) & ~Q(npi=""),
                name="uniq_live_staff_npi",
            ),
            models.UniqueConstraint(
                fields=["external_user_id"],
                condition=Q(deleted__isnull=True) & ~Q(external_user_id=""),
                name="uniq_live_staff_external_user_id",
            ),
            models.UniqueConstraint(
                fields=["email"],
                condition=Q(deleted__isnull=True) & ~Q(email=""),
                name="uniq_live_staff_email",
            ),
        ]
        indexes = [models.Index(fields=["last_name", "first_name"])]

    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name, self.suffix]
        return " ".join(part for part in parts if part)

    @property
    def is_external(self):
        """Created from a token, so owned by Logto: read-only here."""
        return bool(self.external_user_id)

    @property
    def all_roles(self):
        return set(self.roles or [self.role])

    def clean(self):
        super().clean()
        problems = credential_problems(
            self.role,
            self.registration_state,
            self.registration_number,
            self.certification_body,
            self.certification_number,
            pharmacy_id=self.pharmacy_id,
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


# "Not passed": the serializer takes the employer from the URL and does not
# check it here; the model and the token registration do.
UNCHECKED = object()


def credential_problems(
    role, registration_state, registration_number, body, certification, *, pharmacy_id=UNCHECKED
):
    """Field errors for a role's credentials, keyed like a serializer's.

    Shared by Staff.clean(), the serializer and the token registration so the
    three cannot disagree. A half-filled pair is reported before a missing
    one: it is the more specific mistake.
    """
    errors = {}
    if pharmacy_id is not UNCHECKED and pharmacy_id is None and role != StaffRole.ADMIN:
        errors["pharmacy"] = f"A {StaffRole(role).label.lower()} must be employed by a pharmacy."
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

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from pharmacy.validators import normalize_ncpdp, validate_e164, validate_npi

from .base import TimeStampedModel
from .staff import StaffRole


class LocationType(models.TextChoices):
    RETAIL = "retail", "Retail"
    MAIL_ORDER = "mail_order", "Mail order"
    SPECIALTY = "specialty", "Specialty"
    COMPOUNDING = "compounding", "Compounding"
    HOSPITAL_OUTPATIENT = "hospital_outpatient", "Hospital outpatient"
    LONG_TERM_CARE = "long_term_care", "Long-term care"
    OTHER = "other", "Other"


class PharmacyLocation(TimeStampedModel):
    """A physical dispensing site.

    The per-store identifiers live here rather than on Pharmacy because they are
    what a claim, a routing decision or a transfer actually names.
    """

    pharmacy = models.ForeignKey(
        "pharmacy.Pharmacy", on_delete=models.PROTECT, related_name="locations"
    )
    store_number = models.CharField(max_length=20, blank=True)
    ncpdp_id = models.CharField(max_length=7)
    npi = models.CharField(max_length=10, blank=True, validators=[validate_npi])
    dea_number = models.CharField(max_length=9, blank=True)
    location_type = models.CharField(
        max_length=24, choices=LocationType.choices, default=LocationType.RETAIL
    )
    phone_number = models.CharField(max_length=20, validators=[validate_e164])
    fax_number = models.CharField(max_length=20, blank=True, validators=[validate_e164])
    email = models.EmailField(blank=True)
    # Required: a location without a place is meaningless.
    address = models.ForeignKey(
        "pharmacy.Address", on_delete=models.PROTECT, related_name="pharmacy_locations"
    )
    is_24_hour = models.BooleanField(default=False)
    # The pharmacist-in-charge: the board-registered designation for this
    # site, held for defaulting and validation. It says nothing about who was
    # supervising at any given moment, and nothing reads it that way.
    pharmacist_in_charge = models.ForeignKey(
        "pharmacy.Staff",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="pic_of_locations",
    )

    class Meta(TimeStampedModel.Meta):
        abstract = False
        constraints = [
            models.UniqueConstraint(
                fields=["ncpdp_id"], condition=Q(deleted__isnull=True), name="uniq_live_ncpdp"
            ),
            models.UniqueConstraint(
                fields=["npi"],
                condition=Q(deleted__isnull=True) & ~Q(npi=""),
                name="uniq_live_location_npi",
            ),
            # Per pharmacy, not global: two chains both having a store 41 is
            # normal. Blank store numbers are excluded so they never collide.
            models.UniqueConstraint(
                fields=["pharmacy", "store_number"],
                condition=Q(deleted__isnull=True) & ~Q(store_number=""),
                name="uniq_live_store_number_per_pharmacy",
            ),
        ]
        # The FK already carries its own index. ncpdp_id needs one here because
        # the unique constraint above is partial and covers live rows only.
        indexes = [models.Index(fields=["ncpdp_id"])]

    def clean(self):
        super().clean()
        if self.pharmacist_in_charge_id:
            # full_clean() calls this even when the pharmacy failed clean_fields.
            pharmacy = self.pharmacy if self.pharmacy_id else None
            problem = pharmacist_in_charge_problem(self.pharmacist_in_charge, pharmacy)
            if problem:
                raise ValidationError({"pharmacist_in_charge": problem})

    def save(self, *args, **kwargs):
        self.ncpdp_id = normalize_ncpdp(self.ncpdp_id)
        super().save(*args, **kwargs)

    def __str__(self):
        label = self.store_number or self.ncpdp_id
        return f"{self.pharmacy.name} #{label}"


def pharmacist_in_charge_problem(staff, pharmacy):
    """Why `staff` cannot be designated PIC of a location of `pharmacy`, or None.

    Checked at write time only: a PIC who later leaves or becomes inactive
    keeps the designation until someone changes it. Shared by the model and
    the serializer so the two cannot disagree.
    """
    # The roles held, not the highest: an admin who is also a pharmacist
    # qualifies, an admin who is not does not.
    if StaffRole.PHARMACIST not in staff.all_roles:
        return "The pharmacist-in-charge must be a pharmacist."
    if not staff.is_active:
        return "The pharmacist-in-charge must be an active staff member."
    if pharmacy is not None and staff.pharmacy_id != pharmacy.pk:
        return "The pharmacist-in-charge must be employed by this pharmacy."
    return None

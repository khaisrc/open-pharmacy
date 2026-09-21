from django.db import models
from django.db.models import Q

from pharmacy.validators import normalize_ncpdp, validate_e164, validate_npi

from .base import TimeStampedModel


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
        indexes = [
            models.Index(fields=["pharmacy"]),
            models.Index(fields=["ncpdp_id"]),
        ]

    def save(self, *args, **kwargs):
        self.ncpdp_id = normalize_ncpdp(self.ncpdp_id)
        super().save(*args, **kwargs)

    def __str__(self):
        label = self.store_number or self.ncpdp_id
        return f"{self.pharmacy.name} #{label}"

"""One row per dispensing, and the workflow that gets it to `completed`.

This is the record 21 CFR 1306.25 asks a transfer to reproduce: the date,
the location and the people for every previous fill. It exists because a
counter on the prescription cannot answer any of that, and cannot hold 30 of
90 either -- the quantity a fill actually dispensed is what the prescription's
totals are derived from.
"""

from django.db import models
from django.db.models import Q

from .base import TimeStampedModel


class FillStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    VERIFIED = "verified", "Verified"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


# A fill in flight holds its quantity against the prescription and blocks a
# second one from starting; only a terminal fill releases it.
FILL_IN_FLIGHT_STATUSES = frozenset({FillStatus.PENDING, FillStatus.VERIFIED})
FILL_TERMINAL_STATUSES = frozenset({FillStatus.COMPLETED, FillStatus.CANCELLED})

# The complete set of transitions, keyed by the action that performs each.
# `verified -> pending` is how a verified fill is sent back for rework; it is
# the only way to edit one after verification.
FILL_TRANSITIONS = {
    "verify": (FillStatus.PENDING, FillStatus.VERIFIED),
    "unverify": (FillStatus.VERIFIED, FillStatus.PENDING),
    "complete": (FillStatus.VERIFIED, FillStatus.COMPLETED),
    "cancel": (FILL_IN_FLIGHT_STATUSES, FillStatus.CANCELLED),
}


class Fill(TimeStampedModel):
    prescription = models.ForeignKey(
        "pharmacy.Prescription", on_delete=models.PROTECT, related_name="fills"
    )
    # 1 for the prescription's first fill. A cancelled fill keeps its number:
    # a ledger does not renumber.
    sequence = models.PositiveIntegerField()
    # A snapshot of the prescription's location when the fill was created, so
    # a later re-route does not rewrite where earlier fills happened. This is
    # the per-dispensing pharmacy identity a transfer record names.
    pharmacy_location = models.ForeignKey(
        "pharmacy.PharmacyLocation", on_delete=models.PROTECT, related_name="fills"
    )
    status = models.CharField(max_length=16, choices=FillStatus.choices, default=FillStatus.PENDING)
    # The staff member who created the fill, from the X-Staff-Id header.
    operator = models.ForeignKey(
        "pharmacy.Staff", on_delete=models.PROTECT, related_name="fills_operated"
    )
    # The pharmacist who verified it; set by verify/, cleared by unverify/.
    verified_by = models.ForeignKey(
        "pharmacy.Staff",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="fills_verified",
    )
    quantity_dispensed = models.PositiveIntegerField()
    days_supply = models.PositiveIntegerField(null=True, blank=True)
    # Server-set when the fill completes; the dispensing timestamp of record.
    dispensed_at = models.DateTimeField(null=True, blank=True)

    class Meta(TimeStampedModel.Meta):
        abstract = False
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["prescription", "sequence"], name="uniq_fill_sequence_per_prescription"
            ),
            # One in flight at a time. Partial over live rows like every
            # other identifier constraint here; the service refuses first
            # with a clearer message, this is the backstop for the race.
            models.UniqueConstraint(
                fields=["prescription"],
                condition=Q(status__in=sorted(FILL_IN_FLIGHT_STATUSES)) & Q(deleted__isnull=True),
                name="uniq_live_fill_in_flight",
            ),
            models.CheckConstraint(
                condition=Q(quantity_dispensed__gte=1), name="fill_quantity_dispensed_positive"
            ),
            models.CheckConstraint(
                condition=Q(days_supply__isnull=True) | Q(days_supply__gte=1),
                name="fill_days_supply_positive",
            ),
            # dispensed_at exists exactly when the fill is completed.
            models.CheckConstraint(
                condition=Q(status=FillStatus.COMPLETED, dispensed_at__isnull=False)
                | (~Q(status=FillStatus.COMPLETED) & Q(dispensed_at__isnull=True)),
                name="fill_dispensed_at_iff_completed",
            ),
            # verified_by exists exactly when the fill has been verified.
            models.CheckConstraint(
                condition=Q(
                    status__in=[FillStatus.VERIFIED, FillStatus.COMPLETED],
                    verified_by__isnull=False,
                )
                | (
                    Q(status__in=[FillStatus.PENDING, FillStatus.CANCELLED])
                    & Q(verified_by__isnull=True)
                ),
                name="fill_verified_by_iff_verified",
            ),
        ]

    @property
    def is_in_flight(self):
        return self.status in FILL_IN_FLIGHT_STATUSES

    @property
    def is_terminal(self):
        return self.status in FILL_TERMINAL_STATUSES

    @property
    def is_partial(self):
        """Less than one written fill's worth. Derived, so a quantity edit on a
        pending fill cannot leave a stale flag behind."""
        return self.quantity_dispensed < self.prescription.quantity

    def __str__(self):
        return f"Fill {self.sequence} of {self.prescription}"

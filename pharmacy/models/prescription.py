from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from .base import TimeStampedModel
from .fill import IN_FLIGHT_STATUSES, FillStatus

# The largest Rx number a label carries: 12 digits.
RX_NUMBER_MAX = 999_999_999_999
# fills_used and fills_remaining are fractions once a partial fill exists;
# eight places is the precision they are reported to.
FILL_FRACTION_PLACES = Decimal("0.00000001")


class IntakeSource(models.TextChoices):
    """How the order arrived: NCPDP Telecommunication Standard D.0, field
    419-DJ (Prescription Origin Code), under the name the domain uses. Stored as a word,
    not the one-digit wire code (0-5, in this order), so a row reads as what
    it means; a claim maps it back at the edge. `pharmacy` is what NCPDP
    assigns when a new Rx number is created from an existing prescription --
    a transfer in -- so this one field says both how the order arrived and
    whether it was written here."""

    NOT_SPECIFIED = "not_specified", "Not specified"
    PROVIDER = "provider", "Written"
    TELEPHONE = "telephone", "Telephone"
    ELECTRONIC = "electronic", "Electronic"
    FAX = "fax", "Facsimile"
    PHARMACY = "pharmacy", "Pharmacy"


class PrescriptionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ON_HOLD = "on_hold", "On hold"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    EXPIRED = "expired", "Expired"


TERMINAL_STATUSES = frozenset(
    {PrescriptionStatus.COMPLETED, PrescriptionStatus.CANCELLED, PrescriptionStatus.EXPIRED}
)

# Only these targets are reachable through a PATCH/PUT. cancelled, completed and
# expired are never writable directly: cancel has its own action, completed is
# set when a fill dispenses the last of the authorized quantity, and expired
# is recorded when a write path finds expires_at in the past.
ALLOWED_TRANSITIONS = {
    PrescriptionStatus.ACTIVE: {PrescriptionStatus.ON_HOLD},
    PrescriptionStatus.ON_HOLD: {PrescriptionStatus.ACTIVE},
    PrescriptionStatus.COMPLETED: set(),
    PrescriptionStatus.CANCELLED: set(),
    PrescriptionStatus.EXPIRED: set(),
}


class Prescription(TimeStampedModel):
    patient = models.ForeignKey(
        "pharmacy.Patient", on_delete=models.PROTECT, related_name="prescriptions"
    )
    prescriber = models.ForeignKey(
        "pharmacy.Prescriber", on_delete=models.PROTECT, related_name="prescriptions"
    )
    medication = models.ForeignKey(
        "pharmacy.Medication", on_delete=models.PROTECT, related_name="prescriptions"
    )
    # Required: this service backs one pharmacy, so every prescription it holds
    # is dispensed somewhere. The API fills in the home location when a client
    # names none, so a caller still never has to supply it.
    pharmacy_location = models.ForeignKey(
        "pharmacy.PharmacyLocation",
        on_delete=models.PROTECT,
        related_name="prescriptions",
    )
    # Issued by services.rx_number from the pharmacy's counter when the
    # prescription is created; printed on the label. Unique within the
    # pharmacy by construction of the counter, not by a constraint: that
    # would need the pharmacy denormalized onto this row.
    rx_number = models.PositiveBigIntegerField()
    intake_source = models.CharField(max_length=16, choices=IntakeSource.choices)
    dose_amount = models.CharField(max_length=100)
    # PositiveIntegerField is Django's name for non-negative, so both of these
    # need the validator to be the positive integers the model intends: a
    # prescription is written for at least one unit and authorizes at least the
    # original fill. What has been dispensed is not stored here at all -- it
    # is the sum over the fills, so there is nothing to drift.
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    fills_authorized = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    instructions = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=PrescriptionStatus.choices, default=PrescriptionStatus.ACTIVE
    )
    written_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta(TimeStampedModel.Meta):
        abstract = False
        constraints = [
            models.CheckConstraint(
                condition=models.Q(expires_at__isnull=True)
                | models.Q(expires_at__gt=models.F("written_at")),
                name="expires_after_written",
            ),
        ]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["written_at"]),
            models.Index(fields=["rx_number"]),
        ]

    @property
    def is_terminal(self):
        return self.status in TERMINAL_STATUSES

    @property
    def quantity_authorized(self):
        return self.quantity * self.fills_authorized

    def fill_totals(self):
        """(dispensed, in progress): the completed fills' quantity, and the
        in-flight fill's reservation. One pass over the fills -- the prefetch
        on a read, a single query under the lock in a write path -- so the
        callers that need both numbers pay for one traversal."""
        dispensed = in_progress = 0
        for fill in self.fills.all():
            if fill.status == FillStatus.COMPLETED:
                dispensed += fill.quantity_dispensed
            elif fill.status in IN_FLIGHT_STATUSES:
                in_progress += fill.quantity_dispensed
        return dispensed, in_progress

    @property
    def quantity_dispensed(self):
        return self.fill_totals()[0]

    @property
    def quantity_in_progress(self):
        return self.fill_totals()[1]

    @property
    def quantity_remaining(self):
        dispensed, in_progress = self.fill_totals()
        return self.quantity_authorized - dispensed - in_progress

    @property
    def fills_used(self):
        """Fills' worth dispensed, as a fraction of the written quantity.
        Derived for display; the integers above are the record."""
        return self._fill_fraction(self.quantity_dispensed)

    @property
    def fills_remaining(self):
        return self._fill_fraction(self.quantity_remaining)

    def _fill_fraction(self, units):
        return (Decimal(units) / Decimal(self.quantity)).quantize(FILL_FRACTION_PLACES)

    @property
    def is_expired(self):
        # Whether the expiry has lapsed, not whether it has been recorded:
        # there is no background sweep, so `services.prescriptions` records
        # `expired` when a write path finds this true.
        return self.expires_at is not None and self.expires_at <= timezone.now()

    def clean(self):
        # Mirrored in the serializer; here too so an admin form, which calls
        # full_clean(), refuses these the same way instead of tripping the
        # CHECK constraint or over-committing the fills.
        super().clean()
        if self.written_at and self.expires_at and self.expires_at <= self.written_at:
            raise ValidationError(
                {"expires_at": "Expiry must be after the prescription was written."}
            )
        if self.pk and self.quantity and self.fills_authorized:
            dispensed, in_progress = self.fill_totals()
            held = dispensed + in_progress
            if self.quantity_authorized < held:
                raise ValidationError(
                    {
                        "fills_authorized": (
                            f"Cannot authorize {self.quantity_authorized} units when the "
                            f"fills already hold {held}."
                        )
                    }
                )

    def __str__(self):
        return f"{self.medication} for {self.patient}"

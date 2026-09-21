from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from .base import TimeStampedModel


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
# expired are never writable directly: cancel has its own action, and the other
# two are set by the fill path.
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
    dose_amount = models.CharField(max_length=100)
    # PositiveIntegerField is Django's name for non-negative, so both of these
    # need the validator to be the positive integers the model intends: a
    # prescription is written for at least one unit and authorizes at least the
    # original fill. fills_used is genuinely non-negative and keeps the default.
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    fills_authorized = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    fills_used = models.PositiveIntegerField(default=0)
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
                condition=models.Q(fills_used__lte=models.F("fills_authorized")),
                name="fills_used_lte_authorized",
            ),
            models.CheckConstraint(
                condition=models.Q(expires_at__isnull=True)
                | models.Q(expires_at__gt=models.F("written_at")),
                name="expires_after_written",
            ),
        ]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["written_at"]),
        ]

    @property
    def is_terminal(self):
        return self.status in TERMINAL_STATUSES

    @property
    def fills_remaining(self):
        return self.fills_authorized - self.fills_used

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at <= timezone.now()

    def expire_if_due(self):
        """Record a lapsed expiry; returns True when the status changed.

        There is no background sweep, so every write path that cares whether a
        prescription is still active calls this first. Only an active
        prescription expires: a lapsed hold stays on hold, so the record still
        says it was paused rather than that it ran out.
        """
        if self.status != PrescriptionStatus.ACTIVE or not self.is_expired:
            return False
        self.status = PrescriptionStatus.EXPIRED
        self.save(update_fields=["status", "updated_at"])
        return True

    def __str__(self):
        return f"{self.medication} for {self.patient}"

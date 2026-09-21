from django.db import models
from django.utils import timezone

from .base import TimeStampedModel


class PrescriptionStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
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
    PrescriptionStatus.DRAFT: {PrescriptionStatus.ACTIVE},
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
    # Nullable because a draft need not be routed yet, but required to fill.
    pharmacy_location = models.ForeignKey(
        "pharmacy.PharmacyLocation",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="prescriptions",
    )
    dose_amount = models.CharField(max_length=100)
    quantity = models.PositiveIntegerField()
    fills_authorized = models.PositiveIntegerField(default=1)
    fills_used = models.PositiveIntegerField(default=0)
    instructions = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=PrescriptionStatus.choices, default=PrescriptionStatus.DRAFT
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

    def __str__(self):
        return f"{self.medication} for {self.patient}"

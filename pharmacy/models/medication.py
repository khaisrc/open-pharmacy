from django.db import models
from django.db.models import Q

from pharmacy.validators import normalize_ndc

from .base import TimeStampedModel


class MedicationForm(models.TextChoices):
    TABLET = "tablet", "Tablet"
    CAPSULE = "capsule", "Capsule"
    LIQUID = "liquid", "Liquid"
    INJECTION = "injection", "Injection"
    TOPICAL = "topical", "Topical"
    OTHER = "other", "Other"


class Medication(TimeStampedModel):
    name = models.CharField(max_length=255, db_index=True)
    ndc = models.CharField(max_length=11)
    strength = models.CharField(max_length=100, blank=True)
    form = models.CharField(
        max_length=32, choices=MedicationForm.choices, default=MedicationForm.TABLET
    )

    class Meta(TimeStampedModel.Meta):
        abstract = False
        constraints = [
            models.UniqueConstraint(
                fields=["ndc"], condition=Q(deleted__isnull=True), name="uniq_live_ndc"
            )
        ]
        indexes = [models.Index(fields=["ndc"])]

    def save(self, *args, **kwargs):
        self.ndc = normalize_ndc(self.ndc)
        super().save(*args, **kwargs)

    @property
    def formatted_ndc(self):
        """Display form, 5-4-2, derived from the stored 11 digits."""
        return f"{self.ndc[:5]}-{self.ndc[5:9]}-{self.ndc[9:]}" if len(self.ndc) == 11 else self.ndc

    def __str__(self):
        return f"{self.name} {self.strength}".strip()

from django.db import models

from pharmacy.validators import validate_postal_code, validate_us_state

from .base import TimeStampedModel


class Address(TimeStampedModel):
    line1 = models.CharField(max_length=255)
    line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2, validators=[validate_us_state])
    postal_code = models.CharField(max_length=10, validators=[validate_postal_code])
    country = models.CharField(max_length=2, default="US")

    class Meta(TimeStampedModel.Meta):
        abstract = False
        verbose_name_plural = "addresses"
        indexes = [
            models.Index(fields=["state", "city"]),
            models.Index(fields=["postal_code"]),
        ]

    def save(self, *args, **kwargs):
        self.state = (self.state or "").upper()
        self.country = (self.country or "US").upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.line1}, {self.city}, {self.state} {self.postal_code}"

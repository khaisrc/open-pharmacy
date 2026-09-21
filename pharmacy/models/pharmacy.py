from django.db import models

from pharmacy.validators import validate_e164

from .base import TimeStampedModel


class Pharmacy(TimeStampedModel):
    """The organization. A mom-and-pop shop is one Pharmacy with a single
    location; a chain is one Pharmacy with many. Nothing special-cases the
    difference, so an independent that opens a second store becomes correct by
    inserting a row rather than changing shape.

    No address: an organization does not dispense, a store does.
    """

    name = models.CharField(max_length=255, db_index=True)
    legal_name = models.CharField(max_length=255, blank=True)
    phone_number = models.CharField(max_length=20, blank=True, validators=[validate_e164])
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)

    class Meta(TimeStampedModel.Meta):
        abstract = False
        verbose_name_plural = "pharmacies"

    def __str__(self):
        return self.name

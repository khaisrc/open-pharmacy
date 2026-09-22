"""The source of Rx numbers, one counter per pharmacy organization.

An Rx number is the counter value alone, unique within the pharmacy because
the counter only goes up and shared by every store of the chain. It is not
unique across pharmacies -- a label and a transfer name the store by its NCPDP
ID separately, which is how Rx numbers work between real pharmacies. A plain
model rather than TimeStampedModel: a counter is never listed or deleted.

`last_issued` starts null. Seeding it is part of onboarding a pharmacy --
`seed_local` does it for the local ones, deployment does it elsewhere; until
then `services.rx_number` refuses to issue, so a pharmacy cannot silently
start numbering from 1 on top of numbers it already holds elsewhere.
"""

from django.db import models


class RxCounter(models.Model):
    pharmacy = models.OneToOneField(
        "pharmacy.Pharmacy", on_delete=models.PROTECT, related_name="rx_counter"
    )
    last_issued = models.PositiveBigIntegerField(null=True, blank=True)
    step = models.PositiveSmallIntegerField(default=1)

    def __str__(self):
        return f"Rx counter for {self.pharmacy}: {self.last_issued}"

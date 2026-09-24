from pharmacy.models import Prescriber

from .base import Repository


class PrescriberRepository(Repository):
    model = Prescriber

    def all(self):
        return Prescriber.objects.select_related("address")

from pharmacy.models import Medication

from .base import Repository


class MedicationRepository(Repository):
    model = Medication

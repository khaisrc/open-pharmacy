from pharmacy.models import Patient

from .base import Repository


class PatientRepository(Repository):
    model = Patient

    def all(self):
        return Patient.objects.select_related("address")

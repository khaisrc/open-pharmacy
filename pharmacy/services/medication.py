from pharmacy import repositories

from .base import CrudService


class MedicationService(CrudService):
    repository = repositories.medications
    protected_relations = (("prescriptions", "prescriptions"),)

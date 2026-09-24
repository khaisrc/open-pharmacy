from pharmacy import repositories

from .base import CrudService


class PatientService(CrudService):
    repository = repositories.patients
    protected_relations = (("prescriptions", "prescriptions"),)

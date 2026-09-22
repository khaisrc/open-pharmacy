from pharmacy import repositories

from .base import CrudService


class PharmacyLocationService(CrudService):
    repository = repositories.pharmacy_locations
    protected_relations = (("prescriptions", "prescriptions"),)

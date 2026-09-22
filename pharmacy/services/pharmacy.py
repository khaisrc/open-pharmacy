from pharmacy import repositories

from .base import CrudService


class PharmacyService(CrudService):
    repository = repositories.pharmacies
    protected_relations = (("locations", "pharmacy locations"), ("staff", "staff"))

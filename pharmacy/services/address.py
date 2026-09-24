from pharmacy import repositories

from .base import CrudService


class AddressService(CrudService):
    repository = repositories.addresses
    protected_relations = (
        ("patients", "patients"),
        ("prescribers", "prescribers"),
        ("pharmacy_locations", "pharmacy locations"),
    )

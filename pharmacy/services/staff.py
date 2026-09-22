from pharmacy import repositories

from .base import CrudService


class StaffService(CrudService):
    repository = repositories.staff
    # A location's pharmacist-in-charge must stay resolvable; revoke the
    # designation first.
    protected_relations = (("pic_of_locations", "pharmacy locations"),)

from pharmacy import repositories

from .base import CrudService


class PrescriberService(CrudService):
    repository = repositories.prescribers
    protected_relations = (("prescriptions", "prescriptions"),)

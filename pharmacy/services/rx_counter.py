from pharmacy import repositories

from .base import CrudService


class RxCounterService(CrudService):
    """Onboarding: creating and adjusting a pharmacy's counter. Issuing a
    number from it is `services.rx_number`, under the lock."""

    repository = repositories.rx_counters

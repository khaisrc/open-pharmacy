from pharmacy.models import RxCounter

from .base import Repository


class RxCounterRepository(Repository):
    model = RxCounter

    def get_locked_for(self, pharmacy):
        """The pharmacy's counter row under a lock, or None when the pharmacy
        has not been onboarded. No `of=`: a plain model with no joins."""
        return RxCounter.objects.select_for_update().filter(pharmacy=pharmacy).first()

from django.db import transaction
from rest_framework.exceptions import ValidationError

from pharmacy import repositories
from pharmacy.auth.scopes import Scope
from pharmacy.exceptions import InsufficientScope

from . import history
from .base import CrudService


class PharmacyService(CrudService):
    """The organization, and its onboarding: a pharmacy can issue Rx numbers
    once it has a counter, which a create or update seeds through
    `rx_number_seed` (the value issuing starts after) and `step`."""

    repository = repositories.pharmacies
    protected_relations = (("locations", "pharmacy locations"), ("staff", "staff"))

    def create(self, ctx, rx_number_seed=None, step=None, **fields):
        self._require_onboard_scope(ctx, rx_number_seed, step)
        with transaction.atomic():
            pharmacy = super().create(ctx, **fields)
            if rx_number_seed is not None or step is not None:
                self._seed_counter(ctx, pharmacy, rx_number_seed, step)
        return pharmacy

    def update(self, ctx, instance, rx_number_seed=None, step=None, **fields):
        self._require_onboard_scope(ctx, rx_number_seed, step)
        with transaction.atomic():
            instance = super().update(ctx, instance, **fields)
            if rx_number_seed is not None or step is not None:
                self._seed_counter(ctx, instance, rx_number_seed, step)
        return self.repository.get(instance.pk)

    @staticmethod
    def _require_onboard_scope(ctx, seed, step):
        # Issuing Rx numbers is a separate grant from editing the pharmacy.
        if (seed is not None or step is not None) and not ctx.principal.has(
            Scope.PHARMACIES_ONBOARD
        ):
            raise InsufficientScope(
                "Seeding or moving the Rx counter takes pharmacies:onboard.",
                details={"required": [str(Scope.PHARMACIES_ONBOARD)]},
            )

    def _seed_counter(self, ctx, pharmacy, seed, step):
        """Create the counter, or move it. Under the same lock issuing
        takes, so a seed cannot land between a read of last_issued and its
        increment. A seed never moves a counter backwards: the numbers
        between have been printed on labels."""
        counter = repositories.rx_counters.get_locked_for(pharmacy)
        if counter is None:
            if seed is None:
                raise ValidationError(
                    {"step": "A pharmacy with no Rx counter needs rx_number_seed as well."}
                )
            counter = repositories.rx_counters.create(
                pharmacy=pharmacy, last_issued=seed, step=step or 1
            )
            history.record(ctx, counter)
            return counter

        before = history.snapshot(counter)
        if seed is not None:
            if counter.last_issued is not None and seed < counter.last_issued:
                raise ValidationError(
                    {
                        "rx_number_seed": (
                            f"Cannot move the counter back from {counter.last_issued} to "
                            f"{seed}; numbers in between have been issued."
                        )
                    }
                )
            counter.last_issued = seed
        if step is not None:
            counter.step = step
        repositories.rx_counters.save(counter, update_fields=["last_issued", "step"])
        history.record(ctx, counter, before)
        return counter

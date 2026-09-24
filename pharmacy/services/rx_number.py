"""Issuing Rx numbers.

The only thing that computes one. Called inside the transaction that inserts
the prescription, so a create that fails for any other reason hands the
number back by rollback rather than burning it.
"""

from pharmacy import repositories
from pharmacy.exceptions import Conflict
from pharmacy.models import RX_NUMBER_MAX


def next_rx_number(pharmacy):
    """The next number for `pharmacy`, advancing its counter under a lock.

    A row lock per pharmacy is the cheapest collision-free primitive the ORM
    can express; two creates in the same chain serialize on it, creates in
    different chains do not touch. Refuses rather than guesses when the
    pharmacy has no seeded counter: onboarding sets `last_issued` at or above
    any number the pharmacy already holds, and starting from zero on its own
    would collide with those.
    """
    counter = repositories.rx_counters.get_locked_for(pharmacy)
    if counter is None or counter.last_issued is None:
        raise Conflict(
            "This pharmacy has no Rx number counter seeded; onboarding has not been completed.",
            code="rx_counter_not_seeded",
            details={"pharmacy_id": str(pharmacy.pk)},
        )
    issued = counter.last_issued + counter.step
    if issued > RX_NUMBER_MAX:
        raise Conflict(
            "This pharmacy's Rx numbers are exhausted.",
            code="rx_number_exhausted",
            details={"pharmacy_id": str(pharmacy.pk), "last_issued": counter.last_issued},
        )
    counter.last_issued = issued
    repositories.rx_counters.save(counter, update_fields=["last_issued"])
    return issued

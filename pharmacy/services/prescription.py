from django.db import transaction

from pharmacy import repositories
from pharmacy.auth.scopes import Scope
from pharmacy.exceptions import Conflict, InsufficientScope
from pharmacy.models import FILL_IN_FLIGHT_STATUSES, FillStatus, PrescriptionStatus

from . import history, rx_number
from .base import CrudService


class PrescriptionService(CrudService):
    """The prescription lifecycle: numbering, cancels, lazy expiry, and the
    completion a fill triggers.

    Every transition re-reads the row under a lock inside its own transaction
    (`repositories.prescriptions.get_locked`), so a fill and a cancel cannot
    both succeed. A refusal is raised only after the block commits: raising
    inside it would roll back the expiry that `_expire_if_due` just recorded.
    """

    repository = repositories.prescriptions

    def create(self, ctx, **fields):
        # The number comes from the pharmacy's counter inside the same
        # transaction, so a create that fails after this line hands the
        # number back by rollback rather than burning it.
        with transaction.atomic():
            pharmacy = fields["pharmacy_location"].pharmacy
            fields["rx_number"] = rx_number.next_rx_number(pharmacy)
            return super().create(ctx, **fields)

    def update(self, ctx, instance, **fields):
        with transaction.atomic():
            self._refuse_if_update_needs_more_scope(ctx, self.repository.get_locked(instance.pk))
            return super().update(ctx, instance, **fields)

    def delete(self, ctx, instance):
        with transaction.atomic():
            self._refuse_if_update_needs_more_scope(ctx, self.repository.get_locked(instance.pk))
            super().delete(ctx, instance)

    def expire_if_due(self, ctx, pk):
        """Record a lapsed expiry before an update is validated, so a lapsed
        prescription is refused as terminal rather than moved to on_hold.
        Returns True when the status changed."""
        with transaction.atomic():
            return self._expire_if_due(ctx, self.repository.get_locked(pk))

    def cancel(self, ctx, pk):
        # Locked, so a fill that completes the prescription concurrently is seen
        # here as 'completed' rather than overwritten with 'cancelled'.
        with transaction.atomic():
            prescription = self.repository.get_locked(pk)
            self._refuse_if_update_needs_more_scope(ctx, prescription)
            self._expire_if_due(ctx, prescription)
            in_flight = repositories.fills.in_flight(prescription)
            if prescription.is_terminal:
                refusal = Conflict(
                    f"A '{prescription.status}' prescription cannot be cancelled.",
                    code="already_terminal",
                    details={"status": prescription.status},
                )
            elif in_flight is not None:
                # Cancelling under a fill that is being prepared would strand
                # it: cancel the fill first, then the prescription.
                refusal = Conflict(
                    "This prescription has a fill in progress; cancel the fill first.",
                    code="fill_in_progress",
                    details={"status": prescription.status, "fill_id": str(in_flight.pk)},
                )
            else:
                refusal = None
                self._set_status(ctx, prescription, PrescriptionStatus.CANCELLED)
        if refusal:
            raise refusal
        return self.repository.get(pk)

    def complete_if_dispensed(self, ctx, prescription):
        """Called by the fill service, under the lock it already holds, after
        a fill completes: the prescription is done when nothing is left to
        dispense. The quantity decides, not a count of fills."""
        if prescription.quantity_remaining == 0 and prescription.quantity_dispensed > 0:
            self._set_status(ctx, prescription, PrescriptionStatus.COMPLETED)
            return True
        return False

    @staticmethod
    def _refuse_if_update_needs_more_scope(ctx, prescription):
        """The one state-dependent permission in the system. A principal
        holding prescriptions:update_unfilled but not prescriptions:update
        may change a prescription only while no fill has been started -- a
        cancelled fill does not count, nothing was dispensed or reserved.
        Keyed on the scopes held, never on a role. Under the lock, like every
        rule that reads the fills."""
        scopes = getattr(ctx.principal, "scopes", frozenset())
        if (
            Scope.PRESCRIPTIONS_UPDATE in scopes
            or Scope.PRESCRIPTIONS_UPDATE_UNFILLED not in scopes
        ):
            return
        started = [
            fill
            for fill in prescription.fills.all()
            if fill.status in FILL_IN_FLIGHT_STATUSES or fill.status == FillStatus.COMPLETED
        ]
        if started:
            raise InsufficientScope(
                "A fill has been started on this prescription; changing it now takes "
                "prescriptions:update.",
                details={
                    "required": [str(Scope.PRESCRIPTIONS_UPDATE)],
                    "reason": "a fill has been started",
                },
            )

    def _expire_if_due(self, ctx, prescription):
        """Record a lapsed expiry; returns True when the status changed.

        There is no background sweep, so every write path that cares whether
        a prescription is still active calls this first, under the lock. Only
        an active prescription expires: a lapsed hold stays on hold, so the
        record still says it was paused rather than that it ran out.
        """
        if prescription.status != PrescriptionStatus.ACTIVE or not prescription.is_expired:
            return False
        self._set_status(ctx, prescription, PrescriptionStatus.EXPIRED)
        return True

    def _set_status(self, ctx, prescription, status):
        before = history.snapshot(prescription)
        prescription.status = status
        self.repository.save(prescription, update_fields=["status", "updated_at"])
        history.record(ctx, prescription, before)

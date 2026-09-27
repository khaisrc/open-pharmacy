"""The fill workflow: create, edit while pending, and the four transitions.

Every write here runs under the prescription's row lock, because every one
of them changes a total the next write reads -- the in-flight reservation,
the dispensed quantity, the sequence. That lock is what makes the
authorized-quantity rule hold under concurrency; there is no constraint over
the totals in the database, on purpose, because the totals are derived and
a stored copy would be something to drift.
"""

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from pharmacy import repositories
from pharmacy.exceptions import Conflict, InsufficientScope
from pharmacy.models import FILL_TRANSITIONS, FillStatus, PrescriptionStatus, StaffRole

from . import history
from .base import CrudService


class FillService(CrudService):
    repository = repositories.fills

    def __init__(self, prescriptions):
        self.prescriptions = prescriptions

    def create(self, ctx, prescription, **fields):
        """Start a fill: status pending, quantity reserved, sequence assigned.

        409 when the prescription is not active, has expired, has nothing
        left to dispense, or already has a fill in flight; 400 when the
        header names no staff member or the quantity exceeds the remainder.
        """
        operator = self._require_staff(ctx)
        with transaction.atomic():
            prescription = self.prescriptions.repository.get_locked(prescription.pk)
            refusal = self._refusal_to_start(ctx, prescription)
            if refusal is None:
                remaining = prescription.quantity_remaining
                quantity = fields.get("quantity_dispensed")
                if quantity is None:
                    quantity = min(prescription.quantity, remaining)
                self._check_quantity(prescription, quantity, remaining)
                fill = super().create(
                    ctx,
                    prescription=prescription,
                    sequence=self.repository.next_sequence(prescription),
                    pharmacy_location=prescription.pharmacy_location,
                    operator=operator,
                    quantity_dispensed=quantity,
                    days_supply=fields.get("days_supply"),
                )
        if refusal:
            raise refusal
        return self.repository.get(fill.pk)

    def update(self, ctx, fill, **fields):
        """Edit the quantity or days' supply of a pending fill."""
        with transaction.atomic():
            prescription = self.prescriptions.repository.get_locked(fill.prescription_id)
            fill = self.repository.get(fill.pk)
            if fill.status != FillStatus.PENDING:
                raise Conflict(
                    f"Only a pending fill can be edited; this one is '{fill.status}'.",
                    code="not_pending",
                    details={"status": fill.status},
                )
            if "quantity_dispensed" in fields:
                # The remainder excluding this fill's own reservation.
                available = prescription.quantity_remaining + fill.quantity_dispensed
                self._check_quantity(prescription, fields["quantity_dispensed"], available)
            return super().update(ctx, fill, **fields)

    def verify(self, ctx, fill):
        # The fills:verify scope is checked before this runs. The verifier
        # must also be a pharmacist: an admin holds every scope but is not
        # one unless the pharmacist role is among its roles.
        staff = self._require_staff(ctx)
        if StaffRole.PHARMACIST not in staff.all_roles:
            raise InsufficientScope(
                "Only a pharmacist can verify a fill.",
                code="pharmacist_required",
                details={"staff_id": str(staff.pk), "roles": sorted(staff.all_roles)},
            )
        return self._transition(ctx, fill, "verify", verified_by=staff)

    def unverify(self, ctx, fill):
        return self._transition(ctx, fill, "unverify", verified_by=None)

    def complete(self, ctx, fill):
        """Dispense: stamp dispensed_at, and complete the prescription when
        this fill accounts for the last of its authorized quantity. The
        prescription must still be active -- a lapsed expiry is recorded here
        and refuses, the way a fill on an expired prescription always has."""
        return self._transition(ctx, fill, "complete", dispensed_at=timezone.now())

    def cancel(self, ctx, fill):
        """Release the reservation. Allowed whatever the prescription's own
        status, so a fill left pending on one that has since expired or been
        completed can still be closed out."""
        return self._transition(ctx, fill, "cancel", verified_by=None)

    def _transition(self, ctx, fill, action, **changes):
        source, target = FILL_TRANSITIONS[action]
        allowed = source if isinstance(source, frozenset) else {source}
        with transaction.atomic():
            prescription = self.prescriptions.repository.get_locked(fill.prescription_id)
            fill = self.repository.get(fill.pk)
            refusal = None
            if fill.status not in allowed:
                verb = "cancelled" if action == "cancel" else f"{action}d"
                refusal = Conflict(
                    f"A '{fill.status}' fill cannot be {verb}.",
                    code="invalid_transition",
                    details={"status": fill.status, "action": action},
                )
            elif action == "complete":
                refusal = self._refusal_if_not_active(ctx, prescription)
            if refusal is None:
                changes["status"] = target
                before = history.snapshot(fill)
                for name, value in changes.items():
                    setattr(fill, name, value)
                self.repository.save(fill)
                history.record(ctx, fill, before)
                if action == "complete":
                    self.prescriptions.complete_if_dispensed(ctx, prescription)
        if refusal:
            raise refusal
        return self.repository.get(fill.pk)

    def _refusal_if_not_active(self, ctx, prescription):
        """Why nothing can be dispensed against `prescription`, or None. The
        expiry is recorded here, under the lock, the way a fill on a lapsed
        prescription has always recorded it."""
        if self.prescriptions._expire_if_due(ctx, prescription):
            return Conflict(
                "This prescription expired and cannot be filled.",
                code="expired",
                details={
                    "status": prescription.status,
                    "expires_at": prescription.expires_at.isoformat(),
                },
            )
        if prescription.status != PrescriptionStatus.ACTIVE:
            return Conflict(
                f"Only an active prescription can be filled; this one is '{prescription.status}'.",
                code="not_active",
                details={"status": prescription.status},
            )
        return None

    def _refusal_to_start(self, ctx, prescription):
        """Why a new fill cannot be started: not active, one already in
        flight, or nothing left to dispense."""
        refusal = self._refusal_if_not_active(ctx, prescription)
        if refusal is not None:
            return refusal
        in_flight = repositories.fills.in_flight(prescription)
        if in_flight is not None:
            return self._in_progress(prescription, in_flight)
        dispensed, in_progress = prescription.fill_totals()
        remaining = prescription.quantity_authorized - dispensed - in_progress
        if remaining <= 0:
            return Conflict(
                "Every authorized fill has already been used.",
                code="no_fills_remaining",
                details=self._quantities(prescription, dispensed, in_progress, remaining),
            )
        return None

    def _in_progress(self, prescription, fill):
        return Conflict(
            "This prescription already has a fill in progress.",
            code="fill_in_progress",
            details={"status": prescription.status, "fill_id": str(fill.pk)},
        )

    def _check_quantity(self, prescription, quantity, available):
        if quantity < 1 or quantity > available:
            dispensed, in_progress = prescription.fill_totals()
            raise ValidationError(
                {
                    "quantity_dispensed": (
                        f"Must be between 1 and {available}: {prescription.quantity_authorized} "
                        f"authorized, {dispensed} dispensed, {in_progress} in progress."
                    )
                }
            )

    @staticmethod
    def _quantities(prescription, dispensed, in_progress, remaining):
        return {
            "status": prescription.status,
            "quantity_authorized": prescription.quantity_authorized,
            "quantity_dispensed": dispensed,
            "quantity_in_progress": in_progress,
            "quantity_remaining": remaining,
        }

    @staticmethod
    def _require_staff(ctx):
        """A fill names the person who started or verified it, so these two
        writes need a token that maps to a staff member; a Django session or
        Basic credential is a login, not a person."""
        if ctx.staff is None:
            raise InsufficientScope(
                "Starting or verifying a fill must be done as a staff member: "
                "authenticate with a bearer token.",
                code="staff_identity_required",
            )
        return ctx.staff

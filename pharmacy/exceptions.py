"""One error shape for the whole API, and the 409 that blocked writes raise."""

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.db.models import ProtectedError
from django.db.utils import IntegrityError
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import APIException, NotFound
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
from rest_framework.views import exception_handler as drf_exception_handler

# Maps a violated partial-unique constraint to (record, field, label): what the
# row is called, the field a client should look at, and what a client calls it.
LIVE_UNIQUE_CONSTRAINTS = {
    "uniq_live_ndc": ("medication", "ndc", "NDC"),
    "uniq_live_npi": ("prescriber", "npi", "NPI"),
    "uniq_live_ncpdp": ("pharmacy location", "ncpdp_id", "NCPDP ID"),
    "uniq_live_location_npi": ("pharmacy location", "npi", "NPI"),
    "uniq_live_store_number_per_pharmacy": ("pharmacy location", "store_number", "store number"),
    "uniq_live_staff_registration": ("staff member", "registration_number", "state registration"),
    "uniq_live_staff_npi": ("staff member", "npi", "NPI"),
}


class Conflict(APIException):
    """409. Raised for blocked deletes, duplicates, refused fills and cancels."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "The request conflicts with the current state of the resource."
    default_code = "conflict"

    def __init__(self, detail=None, code=None, details=None):
        super().__init__(detail, code)
        self.details = details or {}


def duplicate(record, field, label, value=None, existing=None, extra=None):
    """The 409 for an identifier a live record already holds.

    Serializers raise it with the offending value and the existing row; the
    exception handler raises it for the IntegrityError a concurrent create
    slips past them, when neither is known. Same code and message either way,
    so a client keys on one thing.
    """
    details = {"field": field}
    if value is not None:
        details[field] = value
    if extra:
        details.update(extra)
    if existing is not None:
        details["existing_id"] = str(existing.pk)
    return Conflict(
        f"A live {record} with this {label} already exists.",
        code=f"duplicate_{field}",
        details=details,
    )


def _envelope(code, message, details):
    return {"error": {"code": code, "message": message, "details": details or {}}}


def _code(exc):
    """The code this raise site passed, falling back to the class default.

    APIException stores the `code=` argument on `exc.detail`, not on the
    instance, so reading `default_code` alone would flatten every Conflict to
    "conflict" no matter what the raise site asked for. A ValidationError's
    detail is a dict or list and carries no code, so it falls through.
    """
    return getattr(exc.detail, "code", None) or getattr(exc, "default_code", "error")


def exception_handler(exc, context):
    """Flatten every error into {"error": {code, message, details}}.

    ProtectedError and a unique-constraint IntegrityError become the same 409 a
    serializer would have raised, so a concurrent create is a conflict rather
    than a 500.
    """
    # DRF converts these two itself, but only to a local name inside its own
    # handler, so the `exc` we still hold below would be the Django exception --
    # which carries no default_code, and every 404 would report "error". Convert
    # them here instead, the way ProtectedError and IntegrityError are.
    if isinstance(exc, Http404):
        exc = NotFound(*exc.args)
    elif isinstance(exc, DjangoPermissionDenied):
        exc = DRFPermissionDenied(*exc.args)

    if isinstance(exc, ProtectedError):
        referencing = sorted({obj._meta.verbose_name for obj in exc.protected_objects})
        exc = Conflict(
            "Cannot delete a record that other records still reference.",
            code="referenced",
            details={"referenced_by": referencing},
        )

    if isinstance(exc, IntegrityError):
        text = str(exc)
        for name, (record, field, label) in LIVE_UNIQUE_CONSTRAINTS.items():
            if name in text:
                exc = duplicate(record, field, label)
                break

    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    if isinstance(exc, Conflict):
        response.data = _envelope(_code(exc), str(exc.detail), getattr(exc, "details", {}))
        return response

    detail = response.data
    code = _code(exc)
    if isinstance(detail, dict) and "detail" in detail:
        response.data = _envelope(code, str(detail["detail"]), {})
    elif isinstance(detail, dict):
        response.data = _envelope("validation_error", "Validation failed.", detail)
    else:
        response.data = _envelope(code, "Request failed.", {"errors": detail})
    return response

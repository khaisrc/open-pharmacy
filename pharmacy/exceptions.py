"""One error shape for the whole API, and the 409 that blocked writes raise."""

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.db.models import ProtectedError
from django.db.utils import IntegrityError
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import APIException, NotFound
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
from rest_framework.views import exception_handler as drf_exception_handler

# Maps a violated constraint name to the field a client should look at.
CONSTRAINT_FIELDS = {
    "uniq_live_ndc": "ndc",
    "uniq_live_npi": "npi",
    "uniq_live_ncpdp": "ncpdp_id",
    "uniq_live_location_npi": "npi",
    "uniq_live_store_number_per_pharmacy": "store_number",
}


class Conflict(APIException):
    """409. Raised for blocked deletes, duplicates, refused fills and cancels."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "The request conflicts with the current state of the resource."
    default_code = "conflict"

    def __init__(self, detail=None, code=None, details=None):
        super().__init__(detail, code)
        self.details = details or {}


def _envelope(code, message, details):
    return {"error": {"code": code, "message": message, "details": details or {}}}


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
            details={"referenced_by": referencing},
        )

    if isinstance(exc, IntegrityError):
        text = str(exc)
        for name, field in CONSTRAINT_FIELDS.items():
            if name in text:
                exc = Conflict(
                    f"A live record with this {field} already exists.",
                    details={"field": field, "constraint": name},
                )
                break

    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    if isinstance(exc, Conflict):
        response.data = _envelope("conflict", str(exc.detail), getattr(exc, "details", {}))
        return response

    detail = response.data
    code = getattr(exc, "default_code", "error")
    if isinstance(detail, dict) and "detail" in detail:
        response.data = _envelope(code, str(detail["detail"]), {})
    elif isinstance(detail, dict):
        response.data = _envelope("validation_error", "Validation failed.", detail)
    else:
        response.data = _envelope(code, "Request failed.", {"errors": detail})
    return response

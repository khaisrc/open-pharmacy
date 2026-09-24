"""One ModelAdmin per model, registered on import. Django discovers this
package by name; the per-model modules mirror the other packages."""

from . import (  # noqa: F401
    address,
    field_change,
    fill,
    medication,
    patient,
    pharmacy,
    pharmacy_location,
    prescriber,
    prescription,
    rx_counter,
    staff,
)
from .base import ServiceModelAdmin

__all__ = ["ServiceModelAdmin"]

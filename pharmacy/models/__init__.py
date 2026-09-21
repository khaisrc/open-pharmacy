"""Model package.

Django resolves app_label from the package a model is imported into, so every
model is re-exported here. With that, this package behaves exactly like a
models.py for makemigrations and no Meta.app_label is needed.
"""

from .address import Address
from .base import TimeStampedModel
from .medication import Medication, MedicationForm
from .patient import Gender, Patient
from .pharmacy import Pharmacy
from .pharmacy_location import LocationType, PharmacyLocation
from .prescriber import PractitionerType, Prescriber
from .prescription import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    Prescription,
    PrescriptionStatus,
)
from .staff import (
    ROLES_REQUIRING_CERTIFICATION,
    ROLES_REQUIRING_REGISTRATION,
    CertificationBody,
    Staff,
    StaffRole,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "ROLES_REQUIRING_CERTIFICATION",
    "ROLES_REQUIRING_REGISTRATION",
    "TERMINAL_STATUSES",
    "Address",
    "CertificationBody",
    "Gender",
    "LocationType",
    "Medication",
    "MedicationForm",
    "Patient",
    "Pharmacy",
    "PharmacyLocation",
    "PractitionerType",
    "Prescriber",
    "Prescription",
    "PrescriptionStatus",
    "Staff",
    "StaffRole",
    "TimeStampedModel",
]

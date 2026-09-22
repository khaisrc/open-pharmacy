"""Model package.

Django resolves app_label from the package a model is imported into, so every
model is re-exported here. With that, this package behaves exactly like a
models.py for makemigrations and no Meta.app_label is needed.
"""

from .address import Address
from .base import TimeStampedModel
from .field_change import FieldChange
from .fill import (
    FILL_TRANSITIONS,
    IN_FLIGHT_STATUSES,
    TERMINAL_FILL_STATUSES,
    Fill,
    FillStatus,
)
from .medication import Medication, MedicationForm
from .patient import Gender, Patient
from .pharmacy import Pharmacy
from .pharmacy_location import LocationType, PharmacyLocation
from .prescriber import PractitionerType, Prescriber
from .prescription import (
    ALLOWED_TRANSITIONS,
    RX_NUMBER_MAX,
    TERMINAL_STATUSES,
    Prescription,
    PrescriptionOriginCode,
    PrescriptionStatus,
)
from .rx_counter import RxCounter
from .staff import (
    ROLES_REQUIRING_CERTIFICATION,
    ROLES_REQUIRING_REGISTRATION,
    CertificationBody,
    Staff,
    StaffRole,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "FILL_TRANSITIONS",
    "IN_FLIGHT_STATUSES",
    "RX_NUMBER_MAX",
    "ROLES_REQUIRING_CERTIFICATION",
    "ROLES_REQUIRING_REGISTRATION",
    "TERMINAL_FILL_STATUSES",
    "TERMINAL_STATUSES",
    "Address",
    "CertificationBody",
    "FieldChange",
    "Fill",
    "FillStatus",
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
    "PrescriptionOriginCode",
    "PrescriptionStatus",
    "RxCounter",
    "Staff",
    "StaffRole",
    "TimeStampedModel",
]

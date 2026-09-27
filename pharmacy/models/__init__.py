"""Model package.

Django resolves app_label from the package a model is imported into, so every
model is re-exported here. With that, this package behaves exactly like a
models.py for makemigrations and no Meta.app_label is needed.
"""

from .address import Address
from .base import TimeStampedModel
from .field_change import FieldChange
from .fill import (
    FILL_IN_FLIGHT_STATUSES,
    FILL_TERMINAL_STATUSES,
    FILL_TRANSITIONS,
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
    IntakeSource,
    Prescription,
    PrescriptionStatus,
)
from .rx_counter import RxCounter
from .staff import (
    ROLE_PRECEDENCE,
    ROLES_REQUIRING_CERTIFICATION,
    ROLES_REQUIRING_REGISTRATION,
    SUPERVISING_ROLES,
    CertificationBody,
    Staff,
    StaffRole,
    highest_role,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "FILL_IN_FLIGHT_STATUSES",
    "FILL_TERMINAL_STATUSES",
    "FILL_TRANSITIONS",
    "ROLE_PRECEDENCE",
    "ROLES_REQUIRING_CERTIFICATION",
    "ROLES_REQUIRING_REGISTRATION",
    "RX_NUMBER_MAX",
    "SUPERVISING_ROLES",
    "TERMINAL_STATUSES",
    "Address",
    "CertificationBody",
    "FieldChange",
    "Fill",
    "FillStatus",
    "Gender",
    "IntakeSource",
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
    "RxCounter",
    "Staff",
    "StaffRole",
    "TimeStampedModel",
    "highest_role",
]

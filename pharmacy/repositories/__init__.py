"""One repository instance per model.

The instances are plural (`prescriptions`) and the modules singular
(`prescription.py`) so that importing an instance never shadows its module.
Call sites read `repositories.prescriptions.live()`.
"""

from .address import AddressRepository
from .base import Repository
from .field_change import FieldChangeRepository
from .fill import FillRepository
from .medication import MedicationRepository
from .patient import PatientRepository
from .pharmacy import PharmacyRepository
from .pharmacy_location import PharmacyLocationRepository
from .prescriber import PrescriberRepository
from .prescription import PrescriptionRepository
from .rx_counter import RxCounterRepository
from .staff import StaffRepository

addresses = AddressRepository()
medications = MedicationRepository()
patients = PatientRepository()
prescribers = PrescriberRepository()
pharmacies = PharmacyRepository()
pharmacy_locations = PharmacyLocationRepository()
staff = StaffRepository()
prescriptions = PrescriptionRepository()
fills = FillRepository()
rx_counters = RxCounterRepository()
field_changes = FieldChangeRepository()

__all__ = [
    "Repository",
    "addresses",
    "field_changes",
    "fills",
    "medications",
    "patients",
    "pharmacies",
    "pharmacy_locations",
    "prescribers",
    "prescriptions",
    "rx_counters",
    "staff",
]

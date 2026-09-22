"""One service instance per resource, plural like the repositories:
`services.prescriptions.fill(ctx, pk)`."""

from .address import AddressService
from .base import CrudService
from .context import WriteContext
from .fill import FillService
from .medication import MedicationService
from .patient import PatientService
from .pharmacy import PharmacyService
from .pharmacy_location import PharmacyLocationService
from .prescriber import PrescriberService
from .prescription import PrescriptionService
from .rx_counter import RxCounterService
from .staff import StaffService

addresses = AddressService()
medications = MedicationService()
patients = PatientService()
prescribers = PrescriberService()
pharmacies = PharmacyService()
pharmacy_locations = PharmacyLocationService()
staff = StaffService()
prescriptions = PrescriptionService()
rx_counters = RxCounterService()
# Fills are only ever written through a prescription, and completing one can
# complete the prescription, so the fill service holds the prescription one.
fills = FillService(prescriptions)

__all__ = [
    "CrudService",
    "WriteContext",
    "addresses",
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

from .address import AddressViewSet
from .health import health
from .medication import MedicationViewSet
from .patient import PatientViewSet
from .pharmacy import PharmacyViewSet
from .pharmacy_location import PharmacyLocationViewSet
from .prescriber import PrescriberViewSet
from .prescription import PrescriptionViewSet
from .staff import StaffViewSet

__all__ = [
    "AddressViewSet",
    "MedicationViewSet",
    "PatientViewSet",
    "PharmacyLocationViewSet",
    "PharmacyViewSet",
    "PrescriberViewSet",
    "PrescriptionViewSet",
    "StaffViewSet",
    "health",
]

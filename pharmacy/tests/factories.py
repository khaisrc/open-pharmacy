import factory
from django.utils import timezone

from pharmacy.models import (
    Address,
    Medication,
    Patient,
    Pharmacy,
    PharmacyLocation,
    Prescriber,
    Prescription,
    PrescriptionStatus,
)
from pharmacy.validators import luhn_check_digit


def make_npi(seed):
    """A structurally valid NPI: 9 digits plus the Luhn check digit."""
    body = f"{100000000 + seed:09d}"
    return body + str(luhn_check_digit("80840" + body))


class AddressFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Address

    line1 = factory.Sequence(lambda n: f"{n + 1} Test Street")
    city = "Ithaca"
    state = "NY"
    postal_code = "14850"


class PatientFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Patient

    first_name = factory.Sequence(lambda n: f"Pat{n}")
    last_name = "Tester"
    dob = "1990-01-01"
    gender = "other"
    phone_number = "+15551234567"
    address = factory.SubFactory(AddressFactory)


class PrescriberFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Prescriber

    first_name = factory.Sequence(lambda n: f"Doc{n}")
    last_name = "Writer"
    practitioner_type = "MD"
    npi = factory.Sequence(make_npi)
    address = factory.SubFactory(AddressFactory)


class MedicationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Medication

    name = factory.Sequence(lambda n: f"Testazole {n}")
    ndc = factory.Sequence(lambda n: f"{n:05d}12345"[:11].ljust(11, "0"))
    strength = "500 mg"
    form = "tablet"


class PharmacyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Pharmacy

    name = factory.Sequence(lambda n: f"Test Pharmacy {n}")


class PharmacyLocationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PharmacyLocation

    pharmacy = factory.SubFactory(PharmacyFactory)
    ncpdp_id = factory.Sequence(lambda n: f"{1000000 + n}")
    phone_number = "+15559876543"
    address = factory.SubFactory(AddressFactory)


class PrescriptionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Prescription

    patient = factory.SubFactory(PatientFactory)
    prescriber = factory.SubFactory(PrescriberFactory)
    medication = factory.SubFactory(MedicationFactory)
    pharmacy_location = factory.SubFactory(PharmacyLocationFactory)
    dose_amount = "1 tablet"
    quantity = 30
    fills_authorized = 3
    fills_used = 0
    status = PrescriptionStatus.DRAFT
    written_at = factory.LazyFunction(timezone.now)

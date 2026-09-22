import factory
from django.utils import timezone

from pharmacy.models import (
    Address,
    Fill,
    FillStatus,
    Medication,
    Patient,
    Pharmacy,
    PharmacyLocation,
    Prescriber,
    Prescription,
    PrescriptionOriginCode,
    PrescriptionStatus,
    RxCounter,
    Staff,
    StaffRole,
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
    """Onboarded: the pharmacy comes with a seeded Rx counter, so a
    prescription can be created against any of its stores through the API.
    Pass rx_counter=None for one that has not been."""

    class Meta:
        model = Pharmacy
        skip_postgeneration_save = True

    name = factory.Sequence(lambda n: f"Test Pharmacy {n}")
    rx_counter = factory.RelatedFactory(
        "pharmacy.tests.factories.RxCounterFactory", factory_related_name="pharmacy"
    )


class RxCounterFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = RxCounter

    pharmacy = factory.SubFactory(PharmacyFactory, rx_counter=None)
    last_issued = 0
    step = 1


class PharmacyLocationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PharmacyLocation

    pharmacy = factory.SubFactory(PharmacyFactory)
    ncpdp_id = factory.Sequence(lambda n: f"{1000000 + n}")
    phone_number = "+15559876543"
    address = factory.SubFactory(AddressFactory)


class StaffFactory(factory.django.DjangoModelFactory):
    """A registered pharmacist by default, the role every rule allows; pass
    role= and blank the registration for the other shapes."""

    class Meta:
        model = Staff

    first_name = factory.Sequence(lambda n: f"Staff{n}")
    last_name = "Member"
    role = StaffRole.PHARMACIST
    registration_state = "OR"
    registration_number = factory.Sequence(lambda n: f"RPH.{n:06d}")
    pharmacy = factory.SubFactory(PharmacyFactory)


class PrescriptionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Prescription

    patient = factory.SubFactory(PatientFactory)
    prescriber = factory.SubFactory(PrescriberFactory)
    medication = factory.SubFactory(MedicationFactory)
    pharmacy_location = factory.SubFactory(PharmacyLocationFactory)
    # Through the ORM, not the service, so the number is the factory's own.
    rx_number = factory.Sequence(lambda n: 100_000 + n)
    prescription_origin_code = PrescriptionOriginCode.WRITTEN
    dose_amount = "1 tablet"
    quantity = 30
    fills_authorized = 3
    status = PrescriptionStatus.ACTIVE
    written_at = factory.LazyFunction(timezone.now)


class FillFactory(factory.django.DjangoModelFactory):
    """A pending fill of one written quantity at the prescription's location.
    `completed=True` and `verified=True` are the other two shapes the
    constraints allow."""

    class Meta:
        model = Fill

    class Params:
        verified = factory.Trait(
            status=FillStatus.VERIFIED, verified_by=factory.SubFactory(StaffFactory)
        )
        completed = factory.Trait(
            status=FillStatus.COMPLETED,
            verified_by=factory.SubFactory(StaffFactory),
            dispensed_at=factory.LazyFunction(timezone.now),
        )

    prescription = factory.SubFactory(PrescriptionFactory)
    sequence = factory.LazyAttribute(
        lambda o: Fill.all_objects.filter(prescription=o.prescription).count() + 1
    )
    pharmacy_location = factory.LazyAttribute(lambda o: o.prescription.pharmacy_location)
    operator = factory.SubFactory(StaffFactory)
    status = FillStatus.PENDING
    quantity_dispensed = factory.LazyAttribute(lambda o: o.prescription.quantity)

"""Data migrations, run on the executor against the real schema."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE = [("pharmacy", "0008_fills_rx_numbers_change_history")]
AFTER = [("pharmacy", "0009_assign_rx_numbers")]


@pytest.fixture
def migrate():
    executor = MigrationExecutor(connection)

    def to(targets):
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    yield to
    # Leave the schema where the rest of the suite expects it.
    to(executor.loader.graph.leaf_nodes())


def test_0009_numbers_existing_prescriptions_per_pharmacy(migrate):
    apps = migrate(BEFORE)
    Address = apps.get_model("pharmacy", "Address")
    Pharmacy = apps.get_model("pharmacy", "Pharmacy")
    Location = apps.get_model("pharmacy", "PharmacyLocation")
    Patient = apps.get_model("pharmacy", "Patient")
    Prescriber = apps.get_model("pharmacy", "Prescriber")
    Medication = apps.get_model("pharmacy", "Medication")
    Prescription = apps.get_model("pharmacy", "Prescription")

    address = Address.objects.create(line1="1", city="X", state="NY", postal_code="14850")
    patient = Patient.objects.create(
        first_name="A", last_name="B", dob="1990-01-01", phone_number="+15551234567"
    )
    prescriber = Prescriber.objects.create(first_name="A", last_name="B", npi="1234567893")
    medication = Medication.objects.create(name="M", ndc="00002821501")
    chain, corner = Pharmacy.objects.create(name="Chain"), Pharmacy.objects.create(name="Corner")
    stores = [
        Location.objects.create(
            pharmacy=pharmacy, ncpdp_id=ncpdp, phone_number="+15551234567", address=address
        )
        for pharmacy, ncpdp in ((chain, "1000001"), (chain, "1000002"), (corner, "1000003"))
    ]
    now = timezone.now()
    written = [
        (stores[0], now - timezone.timedelta(days=3)),
        (stores[2], now - timezone.timedelta(days=2)),
        (stores[1], now - timezone.timedelta(days=1)),
    ]
    ids = [
        Prescription.objects.create(
            patient=patient,
            prescriber=prescriber,
            medication=medication,
            pharmacy_location=store,
            intake_source="not_specified",
            dose_amount="1",
            quantity=30,
            written_at=when,
        ).pk
        for store, when in written
    ]
    assert Prescription.objects.filter(rx_number__isnull=False).count() == 0

    Prescription = migrate(AFTER).get_model("pharmacy", "Prescription")
    numbers = [Prescription.objects.get(pk=pk).rx_number for pk in ids]
    # Chain's two stores share one sequence in written order; Corner has its own.
    assert numbers == [1, 1, 2]
    # No counter was seeded: onboarding does that, at deployment time.
    assert not apps.get_model("pharmacy", "RxCounter").objects.exists()

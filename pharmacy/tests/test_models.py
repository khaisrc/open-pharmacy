import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from pharmacy.models import Medication, Patient, Prescriber, Prescription, Staff

from .factories import (
    MedicationFactory,
    PatientFactory,
    PharmacyLocationFactory,
    PrescriberFactory,
    PrescriptionFactory,
    StaffFactory,
    make_npi,
)

pytestmark = pytest.mark.django_db


class TestCheckConstraints:
    def test_fills_used_cannot_exceed_authorized(self):
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                PrescriptionFactory(fills_authorized=2, fills_used=3)

    def test_expiry_must_follow_written_at(self):
        now = timezone.now()
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                PrescriptionFactory(written_at=now, expires_at=now - timezone.timedelta(days=1))


class TestPatientValidation:
    def test_future_dob_rejected_by_clean(self):
        # Deliberately not a CheckConstraint: Postgres does not allow
        # non-deterministic functions such as CURRENT_DATE inside CHECK.
        patient = PatientFactory.build(dob=timezone.localdate() + timezone.timedelta(days=1))
        with pytest.raises(ValidationError):
            patient.clean()


class TestPartialUniqueness:
    def test_live_ndc_is_unique(self):
        MedicationFactory(ndc="00093505601")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                MedicationFactory(ndc="00093505601")

    def test_deleted_ndc_can_be_reused(self):
        original = MedicationFactory(ndc="00093505601")
        original.delete()
        replacement = MedicationFactory(ndc="00093505601")
        assert replacement.pk != original.pk
        assert Medication.all_objects.filter(ndc="00093505601").count() == 2

    def test_live_npi_is_unique(self):
        npi = make_npi(4242)
        PrescriberFactory(npi=npi)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                PrescriberFactory(npi=npi)

    def test_deleted_ncpdp_can_be_reused(self):
        closed = PharmacyLocationFactory(ncpdp_id="1417231")
        closed.delete()
        PharmacyLocationFactory(ncpdp_id="1417231")

    def test_store_number_unique_within_a_pharmacy(self, pharmacy):
        PharmacyLocationFactory(pharmacy=pharmacy, store_number="41")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                PharmacyLocationFactory(pharmacy=pharmacy, store_number="41")

    def test_store_number_may_repeat_across_pharmacies(self):
        first = PharmacyLocationFactory(store_number="41")
        second = PharmacyLocationFactory(store_number="41")
        assert first.pharmacy_id != second.pharmacy_id

    def test_blank_store_numbers_never_collide(self, pharmacy):
        PharmacyLocationFactory(pharmacy=pharmacy, store_number="")
        PharmacyLocationFactory(pharmacy=pharmacy, store_number="")

    def test_live_staff_registration_is_unique_per_state(self):
        StaffFactory(registration_state="NY", registration_number="RPH.1")
        StaffFactory(registration_state="CA", registration_number="RPH.1")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                StaffFactory(registration_state="NY", registration_number="RPH.1")

    def test_deleted_staff_registration_can_be_reused(self):
        gone = StaffFactory(registration_state="NY", registration_number="RPH.1")
        gone.delete()
        StaffFactory(registration_state="NY", registration_number="RPH.1")
        assert Staff.all_objects.filter(registration_number="RPH.1").count() == 2

    def test_unregistered_staff_never_collide(self):
        for _ in range(2):
            StaffFactory(role="technician", registration_state="", registration_number="")

    def test_live_staff_npi_is_unique(self):
        npi = make_npi(4343)
        StaffFactory(npi=npi)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                StaffFactory(npi=npi)


class TestSoftDelete:
    def test_delete_hides_but_keeps_the_row(self):
        patient = PatientFactory()
        patient.delete()
        assert not Patient.objects.filter(pk=patient.pk).exists()
        assert Patient.all_objects.filter(pk=patient.pk).exists()

    def test_undelete_restores(self):
        prescriber = PrescriberFactory()
        prescriber.delete()
        Prescriber.all_objects.get(pk=prescriber.pk).undelete()
        assert Prescriber.objects.filter(pk=prescriber.pk).exists()

    def test_soft_delete_does_not_cascade(self):
        prescription = PrescriptionFactory()
        prescription.prescriber.delete()
        # The clinical record survives its prescriber leaving.
        assert Prescription.objects.filter(pk=prescription.pk).exists()


class TestNormalizationOnSave:
    def test_ndc_normalized_on_save(self):
        assert MedicationFactory(ndc="0002-8215-01").ndc == "00002821501"

    def test_ncpdp_normalized_on_save(self):
        assert PharmacyLocationFactory(ncpdp_id="141-7231").ncpdp_id == "1417231"

    def test_state_uppercased_on_save(self, address):
        address.state = "ny"
        address.save()
        address.refresh_from_db()
        assert address.state == "NY"

    def test_staff_registration_uppercased_on_save(self):
        staff = StaffFactory(registration_state="ny", registration_number=" rph.12 ")
        assert (staff.registration_state, staff.registration_number) == ("NY", "RPH.12")

"""The admin is one more adapter over the services: every write it makes is
audited, numbered and refused by the same code the API uses."""

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from pharmacy.models import FieldChange, FillStatus, Prescription, PrescriptionStatus, RxCounter

from .factories import (
    FillFactory,
    PharmacyFactory,
    PharmacyLocationFactory,
    PrescriptionFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def superuser(db):
    return get_user_model().objects.create_superuser("root", "root@example.local", "secret")


@pytest.fixture
def ui(superuser):
    client = Client()
    client.force_login(superuser)
    return client


def url(model, page, *args):
    return reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_{page}", args=args)


INLINE = {
    "fills-TOTAL_FORMS": "0",
    "fills-INITIAL_FORMS": "0",
    "fills-MIN_NUM_FORMS": "0",
    "fills-MAX_NUM_FORMS": "0",
}


def prescription_form(rx=None, **overrides):
    base = rx or PrescriptionFactory.build()
    body = {
        "intake_source": "provider",
        "status": PrescriptionStatus.ACTIVE,
        "dose_amount": base.dose_amount,
        "quantity": base.quantity,
        "fills_authorized": base.fills_authorized,
        "instructions": "",
        "written_at_0": "2026-01-02",
        "written_at_1": "09:00:00",
        "expires_at_0": "",
        "expires_at_1": "",
        "notes": "",
        **INLINE,
    }
    if rx is not None:
        body.update(
            {
                "patient": str(rx.patient.pk),
                "prescriber": str(rx.prescriber.pk),
                "medication": str(rx.medication.pk),
                "pharmacy_location": str(rx.pharmacy_location.pk),
                "intake_source": rx.intake_source,
                "status": rx.status,
            }
        )
    body.update(overrides)
    return body


class TestEveryModelIsManaged:
    @pytest.mark.parametrize("model", list(admin.site._registry), ids=lambda m: m._meta.model_name)
    def test_changelist_renders(self, ui, model):
        assert ui.get(url(model, "changelist")).status_code == 200

    def test_every_pharmacy_model_is_registered(self):
        from pharmacy import models

        registered = {m._meta.model_name for m in admin.site._registry}
        expected = {
            m._meta.model_name
            for m in vars(models).values()
            if isinstance(m, type) and getattr(m, "_meta", None) and not m._meta.abstract
        }
        assert expected <= registered, expected - registered


class TestWritesGoThroughTheServices:
    def test_adding_a_prescription_issues_an_rx_number_and_history(
        self, ui, superuser, patient, prescriber, medication, location
    ):
        response = ui.post(
            url(Prescription, "add"),
            prescription_form(
                patient=str(patient.pk),
                prescriber=str(prescriber.pk),
                medication=str(medication.pk),
                pharmacy_location=str(location.pk),
            ),
        )
        assert response.status_code == 302, response.content.decode()[:2000]
        rx = Prescription.objects.get(patient=patient)
        assert rx.rx_number == 1
        assert RxCounter.objects.get(pharmacy=location.pharmacy).last_issued == 1
        rows = FieldChange.objects.filter(model="prescription", record_id=rx.pk)
        assert rows.filter(field="rx_number", new_value=1).exists()
        row = rows.first()
        assert row.changed_by_user == superuser
        assert row.changed_by_staff is None
        assert row.request_path == url(Prescription, "add")

    def test_changing_a_prescription_records_old_and_new(self, ui):
        rx = PrescriptionFactory(notes="before")
        response = ui.post(url(Prescription, "change", rx.pk), prescription_form(rx, notes="after"))
        assert response.status_code == 302
        rx.refresh_from_db()
        assert rx.notes == "after"
        (row,) = FieldChange.objects.filter(record_id=rx.pk, field="notes")
        assert (row.old_value, row.new_value) == ("before", "after")

    def test_unseeded_pharmacy_is_refused_with_a_message(self, ui, patient, prescriber, medication):
        location = PharmacyLocationFactory(pharmacy__rx_counter=None)
        response = ui.post(
            url(Prescription, "add"),
            prescription_form(
                patient=str(patient.pk),
                prescriber=str(prescriber.pk),
                medication=str(medication.pk),
                pharmacy_location=str(location.pk),
            ),
            follow=True,
        )
        assert response.redirect_chain[-1][0] == url(Prescription, "add")
        assert "no Rx number counter" in response.content.decode()
        assert not Prescription.objects.exists()

    def test_referenced_delete_is_refused(self, ui, prescription):
        """Django's collector sees the PROTECT foreign key on the
        confirmation page and refuses before the service's guard does; the
        guard is still behind it for a post that skips the page."""
        patient = prescription.patient
        response = ui.post(url(type(patient), "delete", patient.pk), {"post": "yes"}, follow=True)
        assert "Cannot delete patient" in response.content.decode()
        patient.refresh_from_db()
        assert patient.deleted is None

    def test_delete_is_soft_and_recorded(self, ui, address):
        response = ui.post(url(type(address), "delete", address.pk), {"post": "yes"})
        assert response.status_code == 302
        address.refresh_from_db()
        assert address.deleted is not None
        assert FieldChange.objects.filter(record_id=address.pk, field="deleted").exists()


class TestPrescriptionRules:
    def test_status_offers_only_the_reachable_transitions(self, ui):
        rx = PrescriptionFactory(status=PrescriptionStatus.ACTIVE)
        form = ui.get(url(Prescription, "change", rx.pk)).context["adminform"].form
        assert [v for v, _ in form.fields["status"].choices] == ["active", "on_hold"]

    def test_intake_source_is_read_only_after_create(self, ui):
        rx = PrescriptionFactory(intake_source="provider")
        ui.post(url(Prescription, "change", rx.pk), prescription_form(rx, intake_source="fax"))
        rx.refresh_from_db()
        assert rx.intake_source == "provider"

    def test_a_terminal_prescription_is_read_only(self, ui):
        rx = PrescriptionFactory(status=PrescriptionStatus.COMPLETED)
        form = ui.get(url(Prescription, "change", rx.pk)).context["adminform"].form
        assert form.fields == {}

    def test_expiry_before_written_is_a_form_error(self, ui):
        rx = PrescriptionFactory()
        response = ui.post(
            url(Prescription, "change", rx.pk),
            prescription_form(rx, expires_at_0="2020-01-01", expires_at_1="00:00:00"),
        )
        assert response.status_code == 200
        assert "expires_at" in response.context["adminform"].form.errors

    def test_cannot_authorize_less_than_held(self, ui):
        rx = PrescriptionFactory(fills_authorized=2)
        FillFactory(prescription=rx, completed=True)
        FillFactory(prescription=rx, completed=True)
        response = ui.post(
            url(Prescription, "change", rx.pk), prescription_form(rx, fills_authorized=1)
        )
        assert response.status_code == 200
        assert "fills_authorized" in response.context["adminform"].form.errors


class TestFills:
    def test_no_add_page(self, ui, fill):
        assert ui.get(url(type(fill), "add")).status_code == 403

    def test_pending_fill_edits_go_through_the_service(self, ui, fill):
        response = ui.post(
            url(type(fill), "change", fill.pk), {"quantity_dispensed": "5", "days_supply": "5"}
        )
        assert response.status_code == 302
        fill.refresh_from_db()
        assert (fill.quantity_dispensed, fill.days_supply) == (5, 5)
        assert FieldChange.objects.filter(record_id=fill.pk, field="quantity_dispensed").exists()

    def test_a_completed_fill_is_refused(self, ui, prescription):
        fill = FillFactory(prescription=prescription, completed=True)
        response = ui.post(
            url(type(fill), "change", fill.pk), {"quantity_dispensed": "5"}, follow=True
        )
        assert "Only a pending fill can be edited" in response.content.decode()
        fill.refresh_from_db()
        assert fill.quantity_dispensed == 30
        assert fill.status == FillStatus.COMPLETED

    def test_inline_on_the_prescription_lists_its_fills(self, ui, prescription):
        FillFactory(prescription=prescription, completed=True)
        FillFactory()
        response = ui.get(url(Prescription, "change", prescription.pk))
        inline = response.context["inline_admin_formsets"][0]
        assert len(inline.formset.forms) == 1


class TestOnboardingAndHistory:
    def test_rx_counter_is_created_through_the_admin(self, ui):
        pharmacy = PharmacyFactory(rx_counter=None)
        response = ui.post(
            url(RxCounter, "add"),
            {"pharmacy": str(pharmacy.pk), "last_issued": "1000000000", "step": "1"},
        )
        assert response.status_code == 302
        assert RxCounter.objects.get(pharmacy=pharmacy).last_issued == 1_000_000_000
        assert FieldChange.objects.filter(model="rxcounter").exists()

    def test_history_is_read_only(self, ui, address):
        assert ui.get(url(FieldChange, "add")).status_code == 403
        row = FieldChange.objects.first()
        assert row is None or ui.get(url(FieldChange, "change", row.pk)).status_code == 200

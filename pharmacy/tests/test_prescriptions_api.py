from decimal import Decimal

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from pharmacy import repositories
from pharmacy.models import FieldChange, Prescription, PrescriptionStatus, RxCounter

from .factories import (
    FillFactory,
    PharmacyFactory,
    PharmacyLocationFactory,
    PrescriptionFactory,
    RxCounterFactory,
)
from .helpers import PRESCRIPTIONS as URL
from .helpers import dispense, prescription_body, start_fill

pytestmark = pytest.mark.django_db


class TestStatusLifecycle:
    @pytest.mark.parametrize(
        "start,target",
        [
            (PrescriptionStatus.ACTIVE, PrescriptionStatus.ON_HOLD),
            (PrescriptionStatus.ON_HOLD, PrescriptionStatus.ACTIVE),
        ],
    )
    def test_allowed_transitions_succeed(self, api, start, target):
        rx = PrescriptionFactory(status=start)
        response = api.patch(f"{URL}{rx.pk}/", {"status": target}, format="json")
        assert response.status_code == 200
        assert response.data["status"] == target

    @pytest.mark.parametrize(
        "start,target",
        [
            (PrescriptionStatus.ACTIVE, PrescriptionStatus.CANCELLED),
            (PrescriptionStatus.ACTIVE, PrescriptionStatus.EXPIRED),
            (PrescriptionStatus.COMPLETED, PrescriptionStatus.ACTIVE),
            (PrescriptionStatus.CANCELLED, PrescriptionStatus.ACTIVE),
            (PrescriptionStatus.EXPIRED, PrescriptionStatus.ACTIVE),
        ],
    )
    def test_disallowed_transitions_are_400(self, api, start, target):
        rx = PrescriptionFactory(status=start)
        response = api.patch(f"{URL}{rx.pk}/", {"status": target}, format="json")
        assert response.status_code == 400
        # Same key every other refusal uses for the current status.
        assert response.data["error"]["details"]["status"]["status"] == start

    def test_terminal_statuses_are_never_writable(self, api):
        rx = PrescriptionFactory(status=PrescriptionStatus.ACTIVE)
        for target in ("completed", "cancelled", "expired"):
            assert api.patch(f"{URL}{rx.pk}/", {"status": target}, format="json").status_code == 400


class TestTerminalIsClosed:
    """A completed, cancelled or expired prescription is a closed record: no
    field of it changes through PATCH or PUT, not only status."""

    def test_completed_cannot_gain_fills(self, api, as_pharmacist):
        rx = PrescriptionFactory(status=PrescriptionStatus.ACTIVE, fills_authorized=1)
        dispense(as_pharmacist, rx)
        assert api.get(f"{URL}{rx.pk}/").data["status"] == PrescriptionStatus.COMPLETED

        response = api.patch(f"{URL}{rx.pk}/", {"fills_authorized": 3}, format="json")
        assert response.status_code == 409
        assert response.data["error"]["code"] == "already_terminal"
        assert response.data["error"]["details"]["fields"] == ["fills_authorized"]
        rx.refresh_from_db()
        assert rx.fills_authorized == 1

    @pytest.mark.parametrize(
        "status",
        [PrescriptionStatus.COMPLETED, PrescriptionStatus.CANCELLED, PrescriptionStatus.EXPIRED],
    )
    def test_terminal_cannot_be_rerouted(self, api, status, location, medication):
        rx = PrescriptionFactory(status=status)
        for field, value in [
            ("pharmacy_location_id", str(location.pk)),
            ("medication_id", str(medication.pk)),
            ("quantity", 999),
        ]:
            response = api.patch(f"{URL}{rx.pk}/", {field: value}, format="json")
            assert response.status_code == 409, field
        rx.refresh_from_db()
        assert rx.quantity == 30

    def test_put_of_the_current_body_is_still_a_200(self, api):
        rx = PrescriptionFactory(status=PrescriptionStatus.COMPLETED)
        body = api.get(f"{URL}{rx.pk}/").data
        body.update(
            {
                "patient_id": body["patient"]["id"],
                "prescriber_id": body["prescriber"]["id"],
                "medication_id": body["medication"]["id"],
                "pharmacy_location_id": body["pharmacy_location"]["id"],
            }
        )
        assert api.put(f"{URL}{rx.pk}/", body, format="json").status_code == 200


class TestLazyExpiry:
    """No sweep sets `expired`; every write path records it when it finds it."""

    @staticmethod
    def lapsed():
        now = timezone.now()
        return PrescriptionFactory(
            status=PrescriptionStatus.ACTIVE,
            written_at=now - timezone.timedelta(days=30),
            expires_at=now - timezone.timedelta(days=1),
        )

    def test_cancel_records_expired_rather_than_cancelled(self, api):
        rx = self.lapsed()
        response = api.post(f"{URL}{rx.pk}/cancel/")
        assert response.status_code == 409
        assert response.data["error"]["code"] == "already_terminal"
        assert response.data["error"]["details"]["status"] == PrescriptionStatus.EXPIRED
        rx.refresh_from_db()
        assert rx.status == PrescriptionStatus.EXPIRED

    def test_patch_records_expired_rather_than_on_hold(self, api):
        rx = self.lapsed()
        response = api.patch(f"{URL}{rx.pk}/", {"status": "on_hold"}, format="json")
        assert response.status_code == 400
        rx.refresh_from_db()
        assert rx.status == PrescriptionStatus.EXPIRED

    def test_locked_reread_keeps_the_joins_of_the_read_queryset(self, prescription):
        """The locked re-read in a write path must carry the same joins the
        read path does, or a write pays a query per nested object that a
        read does not. Both now come from one repository definition; this
        pins that the lock does not drop them."""
        with CaptureQueriesContext(connection) as queries:
            with transaction.atomic():
                locked = repositories.prescriptions.get_locked(prescription.pk)
        selects = [q["sql"] for q in queries if q["sql"].startswith("SELECT")]
        assert len(selects) == 1  # one statement: the joins ride along, no prefetch
        assert "FOR UPDATE OF" in selects[0]
        for related in ("patient", "prescriber", "medication", "pharmacy_location"):
            assert related in locked._state.fields_cache, related
        assert "pharmacy" in locked.pharmacy_location._state.fields_cache


class TestFill:
    """The prescription as the fill workflow sees it. The workflow itself is
    in test_fills_api.py; this is what it does to the prescription."""

    def test_dispensing_counts_and_completes_on_the_last_quantity(self, api, as_pharmacist):
        rx = PrescriptionFactory(status=PrescriptionStatus.ACTIVE, fills_authorized=2)

        dispense(as_pharmacist, rx)
        first = api.get(f"{URL}{rx.pk}/").data
        assert first["fills_used"] == 1
        assert first["quantity_dispensed"] == 30
        assert first["status"] == PrescriptionStatus.ACTIVE

        dispense(as_pharmacist, rx)
        second = api.get(f"{URL}{rx.pk}/").data
        assert second["fills_used"] == 2
        assert second["quantity_remaining"] == 0
        assert second["status"] == PrescriptionStatus.COMPLETED

    def test_a_pending_fill_is_not_yet_dispensed(self, api, as_pharmacist):
        """POST starts a fill; nothing is dispensed until complete/."""
        rx = PrescriptionFactory(status=PrescriptionStatus.ACTIVE, fills_authorized=1)
        assert start_fill(as_pharmacist, rx).status_code == 201
        body = api.get(f"{URL}{rx.pk}/").data
        assert body["quantity_dispensed"] == 0
        assert body["quantity_in_progress"] == 30
        assert body["quantity_remaining"] == 0
        assert body["status"] == PrescriptionStatus.ACTIVE

    @pytest.mark.parametrize(
        "status",
        [
            PrescriptionStatus.ON_HOLD,
            PrescriptionStatus.COMPLETED,
            PrescriptionStatus.CANCELLED,
            PrescriptionStatus.EXPIRED,
        ],
    )
    def test_fill_only_works_on_active(self, as_pharmacist, status):
        rx = PrescriptionFactory(status=status)
        response = start_fill(as_pharmacist, rx)
        assert response.status_code == 409
        assert response.data["error"]["code"] == "not_active"

    def test_fill_after_expiry_sets_expired_and_409s(self, as_pharmacist):
        now = timezone.now()
        rx = PrescriptionFactory(
            status=PrescriptionStatus.ACTIVE,
            written_at=now - timezone.timedelta(days=30),
            expires_at=now - timezone.timedelta(days=1),
        )
        response = start_fill(as_pharmacist, rx)
        assert response.status_code == 409
        assert response.data["error"]["code"] == "expired"
        rx.refresh_from_db()
        assert rx.status == PrescriptionStatus.EXPIRED

    def test_fill_exhausted_is_409(self, as_pharmacist):
        rx = PrescriptionFactory(status=PrescriptionStatus.ACTIVE, fills_authorized=1)
        FillFactory(prescription=rx, completed=True)
        response = start_fill(as_pharmacist, rx)
        assert response.status_code == 409
        assert response.data["error"]["code"] == "no_fills_remaining"
        assert response.data["error"]["details"]["quantity_remaining"] == 0

    def test_fractions_are_reported_to_eight_places(self, api, as_pharmacist):
        rx = PrescriptionFactory(status=PrescriptionStatus.ACTIVE, quantity=90, fills_authorized=1)
        dispense(as_pharmacist, rx, quantity_dispensed=30)
        body = api.get(f"{URL}{rx.pk}/").data
        assert body["fills_used"] == Decimal("0.33333333")
        assert body["fills_remaining"] == Decimal("0.66666667")
        assert body["quantity_remaining"] == 60


class TestAuthorizedQuantityEdits:
    """quantity × fills_authorized cannot drop below what the fills hold."""

    def test_cannot_authorize_less_than_dispensed(self, api):
        rx = PrescriptionFactory(status=PrescriptionStatus.ACTIVE, fills_authorized=3)
        FillFactory(prescription=rx, completed=True)
        FillFactory(prescription=rx, completed=True)
        response = api.patch(f"{URL}{rx.pk}/", {"fills_authorized": 1}, format="json")
        assert response.status_code == 400
        assert "fills_authorized" in response.data["error"]["details"]

    def test_a_fill_in_progress_counts_as_held(self, api):
        rx = PrescriptionFactory(status=PrescriptionStatus.ACTIVE, quantity=30, fills_authorized=1)
        FillFactory(prescription=rx, quantity_dispensed=30)
        response = api.patch(f"{URL}{rx.pk}/", {"quantity": 20}, format="json")
        assert response.status_code == 400
        assert "quantity" in response.data["error"]["details"]

    def test_raising_the_authorization_is_fine(self, api):
        rx = PrescriptionFactory(status=PrescriptionStatus.ACTIVE, fills_authorized=1)
        FillFactory(prescription=rx, completed=True)
        response = api.patch(f"{URL}{rx.pk}/", {"fills_authorized": 2}, format="json")
        assert response.status_code == 200
        assert response.data["fills_remaining"] == 1


class TestCancel:
    def test_cancel_sets_status_and_keeps_the_record_visible(self, api):
        rx = PrescriptionFactory(status=PrescriptionStatus.ACTIVE)
        response = api.post(f"{URL}{rx.pk}/cancel/")
        assert response.status_code == 200
        assert response.data["status"] == PrescriptionStatus.CANCELLED
        assert api.get(f"{URL}{rx.pk}/").status_code == 200

    @pytest.mark.parametrize(
        "status",
        [PrescriptionStatus.COMPLETED, PrescriptionStatus.CANCELLED, PrescriptionStatus.EXPIRED],
    )
    def test_cancel_on_terminal_is_409(self, api, status):
        rx = PrescriptionFactory(status=status)
        response = api.post(f"{URL}{rx.pk}/cancel/")
        assert response.status_code == 409
        assert response.data["error"]["code"] == "already_terminal"

    def test_cancel_with_a_fill_in_progress_is_409(self, api):
        """The fill is cancelled first, then the prescription; cancelling
        underneath a fill being prepared would strand it."""
        rx = PrescriptionFactory(status=PrescriptionStatus.ACTIVE)
        fill = FillFactory(prescription=rx, verified=True)
        response = api.post(f"{URL}{rx.pk}/cancel/")
        assert response.status_code == 409
        assert response.data["error"]["code"] == "fill_in_progress"
        assert response.data["error"]["details"]["fill_id"] == str(fill.pk)
        rx.refresh_from_db()
        assert rx.status == PrescriptionStatus.ACTIVE


class TestDeleteVersusCancel:
    def test_delete_removes_from_the_api_but_keeps_the_row(self, api):
        rx = PrescriptionFactory(status=PrescriptionStatus.ACTIVE)
        assert api.delete(f"{URL}{rx.pk}/").status_code == 204
        assert api.get(f"{URL}{rx.pk}/").status_code == 404
        assert Prescription.all_objects.filter(pk=rx.pk).exists()

    def test_delete_and_cancel_produce_different_states(self, api):
        cancelled = PrescriptionFactory(status=PrescriptionStatus.ACTIVE)
        deleted = PrescriptionFactory(status=PrescriptionStatus.ACTIVE)

        api.post(f"{URL}{cancelled.pk}/cancel/")
        api.delete(f"{URL}{deleted.pk}/")

        cancelled.refresh_from_db()
        assert cancelled.status == PrescriptionStatus.CANCELLED
        assert cancelled.deleted is None

        row = Prescription.all_objects.get(pk=deleted.pk)
        assert row.deleted is not None
        assert row.status == PrescriptionStatus.ACTIVE


class TestWriteAndRead:
    def test_writes_flat_and_reads_nested(self, api, patient, prescriber, medication, location):
        response = api.post(
            URL, prescription_body(patient, prescriber, medication, location), format="json"
        )
        assert response.status_code == 201
        body = response.data
        assert body["patient"]["id"] == str(patient.pk)
        assert body["medication"]["ndc"] == medication.ndc
        # Two levels deep: a store is useless without its chain name.
        assert body["pharmacy_location"]["pharmacy"]["name"] == location.pharmacy.name

    def test_list_query_count_is_flat(self, api):
        """The select_related regression guard: query count must not grow with
        the page, or nested read serializers turn one request into 3N queries."""
        PrescriptionFactory.create_batch(3)
        with CaptureQueriesContext(connection) as small_page:
            api.get(f"{URL}?page_size=3")

        PrescriptionFactory.create_batch(7)
        with CaptureQueriesContext(connection) as large_page:
            api.get(f"{URL}?page_size=10")

        assert len(large_page) == len(small_page)


class TestExpiryIsCheckedAgainstTheDefaultWrittenAt:
    def test_past_expires_at_without_written_at_is_400(
        self, api, patient, prescriber, medication, location
    ):
        """written_at defaults to now, so the serializer must check expires_at
        against that default rather than skipping the check and letting the
        database constraint answer with a 500."""
        response = api.post(
            URL,
            prescription_body(
                patient, prescriber, medication, location, expires_at="2020-01-01T00:00:00Z"
            ),
            format="json",
        )
        assert response.status_code == 400
        assert "expires_at" in response.data["error"]["details"]


class TestQuantityAndFillsArePositive:
    """PositiveIntegerField permits zero, so both fields carry MinValueValidator(1)."""

    @pytest.mark.parametrize("field", ["quantity", "fills_authorized"])
    def test_zero_is_rejected(self, api, patient, prescriber, medication, location, field):
        payload = prescription_body(patient, prescriber, medication, location)
        payload[field] = 0
        response = api.post(URL, payload, format="json")
        assert response.status_code == 400
        assert field in response.data["error"]["details"]

    def test_one_is_accepted(self, api, patient, prescriber, medication, location):
        response = api.post(
            URL,
            prescription_body(
                patient, prescriber, medication, location, quantity=1, fills_authorized=1
            ),
            format="json",
        )
        assert response.status_code == 201


class TestRxNumber:
    """The counter value alone, per pharmacy, issued on create."""

    def create(self, api, patient, prescriber, medication, location):
        return api.post(
            URL, prescription_body(patient, prescriber, medication, location), format="json"
        )

    def test_consecutive_within_a_pharmacy_across_its_stores(
        self, api, patient, prescriber, medication
    ):
        pharmacy = PharmacyFactory()
        store_a, store_b = PharmacyLocationFactory.create_batch(2, pharmacy=pharmacy)
        numbers = [
            self.create(api, patient, prescriber, medication, store).data["rx_number"]
            for store in (store_a, store_b, store_a)
        ]
        assert numbers == [1, 2, 3]
        assert RxCounter.objects.get(pharmacy=pharmacy).last_issued == 3

    def test_independent_between_pharmacies(self, api, patient, prescriber, medication):
        first = self.create(api, patient, prescriber, medication, PharmacyLocationFactory())
        second = self.create(api, patient, prescriber, medication, PharmacyLocationFactory())
        # Two pharmacies may hold the same number; the store on the label
        # tells them apart.
        assert first.data["rx_number"] == second.data["rx_number"] == 1

    def test_starts_after_last_issued_and_honours_step(self, api, patient, prescriber, medication):
        location = PharmacyLocationFactory(pharmacy__rx_counter=None)
        RxCounterFactory(pharmacy=location.pharmacy, last_issued=4_000, step=5)
        assert self.create(api, patient, prescriber, medication, location).data["rx_number"] == 4005
        assert self.create(api, patient, prescriber, medication, location).data["rx_number"] == 4010

    @pytest.mark.parametrize("seeded", [False, None], ids=["no counter row", "null last_issued"])
    def test_unseeded_pharmacy_cannot_issue(self, api, patient, prescriber, medication, seeded):
        location = PharmacyLocationFactory(pharmacy__rx_counter=None)
        if seeded is None:
            RxCounterFactory(pharmacy=location.pharmacy, last_issued=None)
        response = self.create(api, patient, prescriber, medication, location)
        assert response.status_code == 409
        assert response.data["error"]["code"] == "rx_counter_not_seeded"
        assert response.data["error"]["details"]["pharmacy_id"] == str(location.pharmacy.pk)
        # Nothing was written: the refusal is inside the create's transaction.
        assert not Prescription.objects.filter(pharmacy_location=location).exists()
        assert not FieldChange.objects.filter(model="prescription").exists()

    def test_twelve_digits_is_the_ceiling(self, api, patient, prescriber, medication):
        location = PharmacyLocationFactory(pharmacy__rx_counter=None)
        RxCounterFactory(pharmacy=location.pharmacy, last_issued=999_999_999_999)
        response = self.create(api, patient, prescriber, medication, location)
        assert response.status_code == 409
        assert response.data["error"]["code"] == "rx_number_exhausted"

    def test_home_location_uses_the_home_pharmacy_counter(
        self, api, patient, prescriber, medication, home_rx_counter
    ):
        response = api.post(URL, prescription_body(patient, prescriber, medication), format="json")
        assert response.status_code == 201
        assert response.data["rx_number"] == 1
        home_rx_counter.refresh_from_db()
        assert home_rx_counter.last_issued == 1

    def test_read_only_on_the_wire(self, api, patient, prescriber, medication, location):
        response = api.post(
            URL,
            prescription_body(patient, prescriber, medication, location, rx_number=777),
            format="json",
        )
        assert response.status_code == 201
        assert response.data["rx_number"] == 1


class TestPrescriptionOriginCode:
    """NCPDP D.0 419-DJ: required on entry, immutable after."""

    def test_required_on_create(self, api, patient, prescriber, medication, location):
        body = prescription_body(patient, prescriber, medication, location)
        del body["prescription_origin_code"]
        response = api.post(URL, body, format="json")
        assert response.status_code == 400
        assert "prescription_origin_code" in response.data["error"]["details"]

    @pytest.mark.parametrize(
        "code", ["not_specified", "provider", "telephone", "electronic", "fax", "pharmacy"]
    )
    def test_every_code_round_trips(self, api, patient, prescriber, medication, location, code):
        body = prescription_body(
            patient, prescriber, medication, location, prescription_origin_code=code
        )
        response = api.post(URL, body, format="json")
        assert response.status_code == 201
        assert response.data["prescription_origin_code"] == code

    def test_unknown_code_is_400(self, api, patient, prescriber, medication, location):
        body = prescription_body(
            patient, prescriber, medication, location, prescription_origin_code="written"
        )
        assert api.post(URL, body, format="json").status_code == 400

    @pytest.mark.parametrize("method", ["patch", "put"])
    def test_immutable_after_create(self, api, method):
        rx = PrescriptionFactory(prescription_origin_code="provider")
        body = api.get(f"{URL}{rx.pk}/").data
        body.update(
            {
                "patient_id": body["patient"]["id"],
                "prescriber_id": body["prescriber"]["id"],
                "medication_id": body["medication"]["id"],
                "pharmacy_location_id": body["pharmacy_location"]["id"],
                "prescription_origin_code": "electronic",
            }
        )
        payload = body if method == "put" else {"prescription_origin_code": "electronic"}
        response = getattr(api, method)(f"{URL}{rx.pk}/", payload, format="json")
        assert response.status_code == 400
        assert "prescription_origin_code" in response.data["error"]["details"]
        rx.refresh_from_db()
        assert rx.prescription_origin_code == "provider"

    def test_resending_the_same_code_is_fine(self, api):
        rx = PrescriptionFactory(prescription_origin_code="provider")
        body = {"prescription_origin_code": "provider"}
        response = api.patch(f"{URL}{rx.pk}/", body, format="json")
        assert response.status_code == 200

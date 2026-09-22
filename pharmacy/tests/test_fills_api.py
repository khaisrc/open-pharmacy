"""The fill workflow: /prescriptions/{id}/fills/ and its four transitions."""

import threading

import pytest
from django.db import connection
from rest_framework.test import APIClient

from pharmacy.models import Fill, FillStatus, PrescriptionStatus, RxCounter

from .conftest import with_staff
from .factories import (
    FillFactory,
    PharmacyLocationFactory,
    PrescriptionFactory,
    StaffFactory,
)
from .helpers import FILLS, PRESCRIPTIONS, dispense, fills_url, prescription_body, start_fill

pytestmark = pytest.mark.django_db
MISSING = "00000000-0000-4000-8000-000000000000"


def transition(client, prescription, fill, action):
    return client.post(fills_url(prescription, fill, action))


class TestStaffHeader:
    """X-Staff-Id names the person acting. Optional on most writes, ignored
    on reads, required where a fill needs a person."""

    def test_create_without_a_header_is_400(self, api, prescription):
        response = start_fill(api, prescription)
        assert response.status_code == 400
        assert response.data["error"]["code"] == "staff_required"
        assert not Fill.objects.exists()

    @pytest.mark.parametrize(
        "value",
        ["not-a-uuid", MISSING, "deleted", "inactive"],
        ids=["malformed", "unknown", "deleted", "inactive"],
    )
    def test_a_header_that_names_nobody_is_400(self, api, prescription, value):
        if value == "deleted":
            staff = StaffFactory()
            staff.delete()
            value = str(staff.pk)
        elif value == "inactive":
            value = str(StaffFactory(is_active=False).pk)
        api.credentials(HTTP_X_STAFF_ID=value)
        response = start_fill(api, prescription)
        assert response.status_code == 400
        assert response.data["error"]["code"] == "invalid_staff"
        assert response.data["error"]["details"]["value"] == value

    def test_a_bad_header_is_ignored_on_a_read(self, api, fill):
        api.credentials(HTTP_X_STAFF_ID="not-a-uuid")
        assert api.get(fills_url(fill.prescription)).status_code == 200
        assert api.get(fills_url(fill.prescription, fill)).status_code == 200

    def test_verify_needs_a_pharmacist(self, as_technician, fill):
        response = transition(as_technician, fill.prescription, fill, "verify")
        assert response.status_code == 400
        assert response.data["error"]["code"] == "pharmacist_required"
        fill.refresh_from_db()
        assert fill.status == FillStatus.PENDING

    def test_verify_without_a_header_is_400(self, api, fill):
        response = transition(api, fill.prescription, fill, "verify")
        assert response.status_code == 400
        assert response.data["error"]["code"] == "staff_required"

    def test_operator_is_whoever_the_header_names(self, as_technician, technician, prescription):
        response = start_fill(as_technician, prescription)
        assert response.status_code == 201
        assert response.data["operator"]["id"] == str(technician.pk)


class TestCreate:
    def test_starts_pending_with_the_defaults(self, as_pharmacist, prescription):
        response = start_fill(as_pharmacist, prescription)
        assert response.status_code == 201
        body = response.data
        assert body["status"] == FillStatus.PENDING
        assert body["sequence"] == 1
        assert body["quantity_dispensed"] == prescription.quantity
        assert body["is_partial"] is False
        assert body["days_supply"] is None
        assert body["dispensed_at"] is None
        assert body["verified_by"] is None
        assert body["rx_number"] == prescription.rx_number
        assert body["pharmacy_location"]["id"] == str(prescription.pharmacy_location.pk)

    def test_location_is_a_snapshot(self, api, as_pharmacist, prescription):
        """A later re-route must not rewrite where earlier fills happened."""
        fill = start_fill(as_pharmacist, prescription).data
        elsewhere = PharmacyLocationFactory()
        api.patch(
            f"{PRESCRIPTIONS}{prescription.pk}/",
            {"pharmacy_location_id": str(elsewhere.pk)},
            format="json",
        )
        assert (
            api.get(fills_url(prescription, fill["id"])).data["pharmacy_location"]["id"]
            == fill["pharmacy_location"]["id"]
        )

    def test_sequences_increment_and_cancelled_fills_keep_theirs(self, as_pharmacist, prescription):
        first = start_fill(as_pharmacist, prescription).data
        transition(as_pharmacist, prescription, first["id"], "cancel")
        second = start_fill(as_pharmacist, prescription).data
        assert (first["sequence"], second["sequence"]) == (1, 2)

    def test_a_second_fill_in_flight_is_409(self, as_pharmacist, prescription):
        first = start_fill(as_pharmacist, prescription).data
        response = start_fill(as_pharmacist, prescription)
        assert response.status_code == 409
        assert response.data["error"]["code"] == "fill_in_progress"
        assert response.data["error"]["details"]["fill_id"] == first["id"]

    def test_unknown_prescription_is_404(self, as_pharmacist):
        assert as_pharmacist.post(f"{PRESCRIPTIONS}{MISSING}/fills/").status_code == 404

    def test_default_after_a_partial_is_the_balance(self, as_pharmacist):
        rx = PrescriptionFactory(quantity=90, fills_authorized=1)
        dispense(as_pharmacist, rx, quantity_dispensed=30)
        response = start_fill(as_pharmacist, rx)
        assert response.status_code == 201
        assert response.data["quantity_dispensed"] == 60


class TestQuantityRule:
    """1 <= quantity <= what remains; no per-fill cap; a 400 when broken."""

    def test_over_the_remainder_is_400(self, as_pharmacist):
        rx = PrescriptionFactory(quantity=30, fills_authorized=2)
        response = start_fill(as_pharmacist, rx, quantity_dispensed=61)
        assert response.status_code == 400
        assert "quantity_dispensed" in response.data["error"]["details"]
        assert response.data["error"]["code"] == "validation_error"

    def test_zero_is_400(self, as_pharmacist, prescription):
        response = start_fill(as_pharmacist, prescription, quantity_dispensed=0)
        assert response.status_code == 400
        assert "quantity_dispensed" in response.data["error"]["details"]

    def test_more_than_one_fills_worth_at_once_is_allowed(self, api, as_pharmacist):
        rx = PrescriptionFactory(quantity=30, fills_authorized=3)
        _, _, completed = dispense(as_pharmacist, rx, quantity_dispensed=90)
        assert completed.status_code == 200
        body = api.get(f"{PRESCRIPTIONS}{rx.pk}/").data
        assert body["status"] == PrescriptionStatus.COMPLETED
        assert body["fills_used"] == 3

    def test_a_partial_and_its_balance_consume_one_authorized_fill(self, api, as_pharmacist):
        rx = PrescriptionFactory(quantity=90, fills_authorized=2)
        first, _, _ = dispense(as_pharmacist, rx, quantity_dispensed=30)
        assert first.data["is_partial"] is True
        second, _, _ = dispense(as_pharmacist, rx, quantity_dispensed=60)
        assert second.data["is_partial"] is True
        body = api.get(f"{PRESCRIPTIONS}{rx.pk}/").data
        assert body["fills_used"] == 1
        assert body["fills_remaining"] == 1
        assert body["quantity_remaining"] == 90

    def test_patch_is_checked_against_the_remainder_excluding_itself(self, as_pharmacist):
        rx = PrescriptionFactory(quantity=30, fills_authorized=2)
        fill = start_fill(as_pharmacist, rx, quantity_dispensed=30).data
        url = fills_url(rx, fill["id"])
        raised = as_pharmacist.patch(url, {"quantity_dispensed": 60}, format="json")
        assert raised.status_code == 200
        response = as_pharmacist.patch(url, {"quantity_dispensed": 61}, format="json")
        assert response.status_code == 400
        assert "quantity_dispensed" in response.data["error"]["details"]


class TestWorkflow:
    def test_verify_records_the_pharmacist(self, as_pharmacist, pharmacist, fill):
        response = transition(as_pharmacist, fill.prescription, fill, "verify")
        assert response.status_code == 200
        assert response.data["status"] == FillStatus.VERIFIED
        assert response.data["verified_by"]["id"] == str(pharmacist.pk)

    def test_unverify_sends_it_back(self, as_pharmacist, prescription):
        fill = FillFactory(prescription=prescription, verified=True)
        response = transition(as_pharmacist, prescription, fill, "unverify")
        assert response.status_code == 200
        assert response.data["status"] == FillStatus.PENDING
        assert response.data["verified_by"] is None

    def test_complete_stamps_dispensed_at(self, as_pharmacist, prescription):
        fill = FillFactory(prescription=prescription, verified=True)
        response = transition(as_pharmacist, prescription, fill, "complete")
        assert response.status_code == 200
        assert response.data["status"] == FillStatus.COMPLETED
        assert response.data["dispensed_at"] is not None

    def test_complete_does_not_need_a_header(self, api, prescription):
        fill = FillFactory(prescription=prescription, verified=True)
        assert transition(api, prescription, fill, "complete").status_code == 200

    def test_the_last_quantity_completes_the_prescription(self, api, as_pharmacist):
        rx = PrescriptionFactory(fills_authorized=1)
        _, _, completed = dispense(as_pharmacist, rx)
        assert completed.status_code == 200
        assert api.get(f"{PRESCRIPTIONS}{rx.pk}/").data["status"] == PrescriptionStatus.COMPLETED

    def test_completing_on_a_lapsed_prescription_records_expiry(self, api, as_pharmacist):
        from django.utils import timezone

        now = timezone.now()
        rx = PrescriptionFactory(
            written_at=now - timezone.timedelta(days=30),
            expires_at=now + timezone.timedelta(seconds=1),
        )
        fill = FillFactory(prescription=rx, verified=True)
        rx.expires_at = now - timezone.timedelta(seconds=1)
        rx.save(update_fields=["expires_at"])
        response = transition(api, rx, fill, "complete")
        assert response.status_code == 409
        assert response.data["error"]["code"] == "expired"
        rx.refresh_from_db()
        assert rx.status == PrescriptionStatus.EXPIRED
        fill.refresh_from_db()
        assert fill.status == FillStatus.VERIFIED

    def test_cancel_releases_the_reservation(self, api, as_pharmacist, prescription):
        fill = start_fill(as_pharmacist, prescription).data
        assert api.get(f"{PRESCRIPTIONS}{prescription.pk}/").data["quantity_in_progress"] == 30
        response = transition(api, prescription, fill["id"], "cancel")
        assert response.status_code == 200
        assert response.data["status"] == FillStatus.CANCELLED
        assert api.get(f"{PRESCRIPTIONS}{prescription.pk}/").data["quantity_in_progress"] == 0

    def test_cancel_is_allowed_on_a_prescription_that_is_no_longer_active(self, api):
        rx = PrescriptionFactory(status=PrescriptionStatus.CANCELLED)
        fill = FillFactory(prescription=rx)
        assert transition(api, rx, fill, "cancel").status_code == 200

    @pytest.mark.parametrize(
        "status,action",
        [
            (FillStatus.PENDING, "unverify"),
            (FillStatus.PENDING, "complete"),
            (FillStatus.VERIFIED, "verify"),
            (FillStatus.COMPLETED, "verify"),
            (FillStatus.COMPLETED, "unverify"),
            (FillStatus.COMPLETED, "complete"),
            (FillStatus.COMPLETED, "cancel"),
            (FillStatus.CANCELLED, "verify"),
            (FillStatus.CANCELLED, "complete"),
            (FillStatus.CANCELLED, "cancel"),
        ],
    )
    def test_every_other_transition_is_409(self, as_pharmacist, prescription, status, action):
        shapes = {
            FillStatus.PENDING: {},
            FillStatus.VERIFIED: {"verified": True},
            FillStatus.COMPLETED: {"completed": True},
            FillStatus.CANCELLED: {"status": FillStatus.CANCELLED},
        }
        fill = FillFactory(prescription=prescription, **shapes[status])
        response = transition(as_pharmacist, prescription, fill, action)
        assert response.status_code == 409
        assert response.data["error"]["code"] == "invalid_transition"
        # Same key every other refusal uses for the current status.
        assert response.data["error"]["details"]["status"] == status
        fill.refresh_from_db()
        assert fill.status == status


class TestPatch:
    def test_edits_a_pending_fill(self, as_pharmacist, fill):
        response = as_pharmacist.patch(
            fills_url(fill.prescription, fill),
            {"quantity_dispensed": 10, "days_supply": 10},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["quantity_dispensed"] == 10
        assert response.data["days_supply"] == 10
        assert response.data["is_partial"] is True

    @pytest.mark.parametrize("shape", [{"verified": True}, {"completed": True}])
    def test_only_a_pending_fill_can_be_edited(self, api, prescription, shape):
        fill = FillFactory(prescription=prescription, **shape)
        response = api.patch(
            fills_url(prescription, fill), {"quantity_dispensed": 10}, format="json"
        )
        assert response.status_code == 409
        assert response.data["error"]["code"] == "not_pending"

    def test_status_and_people_are_not_writable(self, api, fill):
        response = api.patch(
            fills_url(fill.prescription, fill),
            {"status": "completed", "sequence": 9, "dispensed_at": "2020-01-01T00:00:00Z"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["status"] == FillStatus.PENDING
        assert response.data["sequence"] == 1
        assert response.data["dispensed_at"] is None

    def test_no_put(self, api, fill):
        assert api.put(fills_url(fill.prescription, fill)).status_code == 405


class TestReads:
    def test_list_is_scoped_and_ordered_by_sequence(self, api, prescription):
        FillFactory(prescription=prescription, completed=True)
        FillFactory(prescription=prescription, status=FillStatus.CANCELLED)
        FillFactory(prescription=prescription)
        FillFactory()  # someone else's
        body = api.get(fills_url(prescription)).data
        assert body["count"] == 3
        assert [f["sequence"] for f in body["results"]] == [1, 2, 3]

    def test_filter_by_status(self, api, prescription):
        FillFactory(prescription=prescription, completed=True)
        FillFactory(prescription=prescription)
        body = api.get(f"{fills_url(prescription)}?status=completed").data
        assert body["count"] == 1

    def test_a_fill_has_a_top_level_id(self, api, fill):
        """Listed and started through its prescription; read, edited and
        moved at /fills/{id}/ -- not nested."""
        assert api.get(f"{FILLS}{fill.pk}/").status_code == 200
        nested = f"{PRESCRIPTIONS}{fill.prescription.pk}/fills/{fill.pk}/"
        assert api.get(nested).status_code == 404
        assert api.get(f"{FILLS}{MISSING}/").status_code == 404

    def test_there_is_no_top_level_collection(self, api, fill):
        assert api.get(FILLS).status_code == 404
        assert api.post(FILLS, {}, format="json").status_code == 404

    def test_unknown_prescription_is_404(self, api):
        assert api.get(f"{PRESCRIPTIONS}{MISSING}/fills/").status_code == 404


@pytest.mark.django_db(transaction=True)
class TestConcurrency:
    """The row lock is load-bearing. Two threads, two connections, one row
    lock: exactly one of each pair wins. transaction=True because the lock
    is invisible inside a single wrapping test transaction."""

    @pytest.fixture(autouse=True)
    def postgres_only(self):
        if connection.vendor != "postgresql":
            pytest.skip("select_for_update is only exercised on PostgreSQL")

    @staticmethod
    def race(workers):
        """Run each callable in its own thread, started together, returning
        their results in order. Each thread closes its own connection so the
        test database can be torn down."""
        barrier = threading.Barrier(len(workers))
        results = [None] * len(workers)

        def run(index, work):
            try:
                barrier.wait()
                results[index] = work()
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(i, w)) for i, w in enumerate(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return results

    def test_two_fills_against_the_last_quantity(self, user, pharmacist):
        rx = PrescriptionFactory(fills_authorized=1)

        def start():
            client = with_staff(APIClient(), pharmacist)
            client.force_authenticate(user=user)
            return start_fill(client, rx)

        responses = self.race([start, start])
        assert sorted(r.status_code for r in responses) == [201, 409]
        refused = next(r for r in responses if r.status_code == 409)
        assert refused.data["error"]["code"] == "fill_in_progress"
        assert Fill.objects.filter(prescription=rx).count() == 1

    def test_two_prescriptions_at_one_pharmacy_get_distinct_numbers(
        self, user, patient, prescriber, medication, location
    ):
        def create():
            client = APIClient()
            client.force_authenticate(user=user)
            return client.post(
                PRESCRIPTIONS,
                prescription_body(patient, prescriber, medication, location),
                format="json",
            )

        responses = self.race([create, create])
        assert [r.status_code for r in responses] == [201, 201]
        assert sorted(r.data["rx_number"] for r in responses) == [1, 2]
        assert RxCounter.objects.get(pharmacy=location.pharmacy).last_issued == 2

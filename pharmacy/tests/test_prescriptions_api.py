import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from pharmacy.models import Prescription, PrescriptionStatus

from .factories import PrescriptionFactory

pytestmark = pytest.mark.django_db
URL = "/api/v1/prescriptions/"


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

    def test_completed_cannot_gain_fills(self, api):
        rx = PrescriptionFactory(status=PrescriptionStatus.ACTIVE, fills_authorized=1)
        assert api.post(f"{URL}{rx.pk}/fill/").data["status"] == PrescriptionStatus.COMPLETED

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
        rx = PrescriptionFactory(status=PrescriptionStatus.COMPLETED, fills_used=3)
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

    def test_fill_and_cancel_cost_the_same_queries(self, api):
        """The locked re-read must keep the joins the response serializer
        needs, or fill pays a query per nested object that cancel does not."""
        rx = PrescriptionFactory(status=PrescriptionStatus.ACTIVE, fills_authorized=3)
        with CaptureQueriesContext(connection) as fill:
            assert api.post(f"{URL}{rx.pk}/fill/").status_code == 200
        with CaptureQueriesContext(connection) as cancel:
            assert api.post(f"{URL}{rx.pk}/cancel/").status_code == 200
        assert len(fill) == len(cancel)


class TestFill:
    def test_fill_increments_and_completes_on_the_last_one(self, api):
        rx = PrescriptionFactory(status=PrescriptionStatus.ACTIVE, fills_authorized=2)

        first = api.post(f"{URL}{rx.pk}/fill/")
        assert first.status_code == 200
        assert first.data["fills_used"] == 1
        assert first.data["status"] == PrescriptionStatus.ACTIVE

        second = api.post(f"{URL}{rx.pk}/fill/")
        assert second.data["fills_used"] == 2
        assert second.data["status"] == PrescriptionStatus.COMPLETED

    @pytest.mark.parametrize(
        "status",
        [
            PrescriptionStatus.ON_HOLD,
            PrescriptionStatus.COMPLETED,
            PrescriptionStatus.CANCELLED,
            PrescriptionStatus.EXPIRED,
        ],
    )
    def test_fill_only_works_on_active(self, api, status):
        rx = PrescriptionFactory(status=status)
        response = api.post(f"{URL}{rx.pk}/fill/")
        assert response.status_code == 409
        assert response.data["error"]["code"] == "not_active"

    def test_fill_after_expiry_sets_expired_and_409s(self, api):
        now = timezone.now()
        rx = PrescriptionFactory(
            status=PrescriptionStatus.ACTIVE,
            written_at=now - timezone.timedelta(days=30),
            expires_at=now - timezone.timedelta(days=1),
        )
        response = api.post(f"{URL}{rx.pk}/fill/")
        assert response.status_code == 409
        assert response.data["error"]["code"] == "expired"
        rx.refresh_from_db()
        assert rx.status == PrescriptionStatus.EXPIRED

    def test_fill_exhausted_is_409(self, api):
        rx = PrescriptionFactory(status=PrescriptionStatus.ACTIVE, fills_authorized=1, fills_used=1)
        response = api.post(f"{URL}{rx.pk}/fill/")
        assert response.status_code == 409
        assert response.data["error"]["code"] == "no_fills_remaining"


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
            URL,
            {
                "patient_id": str(patient.pk),
                "prescriber_id": str(prescriber.pk),
                "medication_id": str(medication.pk),
                "pharmacy_location_id": str(location.pk),
                "dose_amount": "1 tablet",
                "quantity": 30,
            },
            format="json",
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
            {
                "patient_id": str(patient.pk),
                "prescriber_id": str(prescriber.pk),
                "medication_id": str(medication.pk),
                "pharmacy_location_id": str(location.pk),
                "dose_amount": "1 tablet",
                "quantity": 30,
                "expires_at": "2020-01-01T00:00:00Z",
            },
            format="json",
        )
        assert response.status_code == 400
        assert "expires_at" in response.data["error"]["details"]


class TestQuantityAndFillsArePositive:
    """PositiveIntegerField permits zero, so both fields carry MinValueValidator(1)."""

    @pytest.mark.parametrize("field", ["quantity", "fills_authorized"])
    def test_zero_is_rejected(self, api, patient, prescriber, medication, location, field):
        payload = {
            "patient_id": str(patient.pk),
            "prescriber_id": str(prescriber.pk),
            "medication_id": str(medication.pk),
            "pharmacy_location_id": str(location.pk),
            "dose_amount": "1 tablet",
            "quantity": 30,
        }
        payload[field] = 0
        response = api.post(URL, payload, format="json")
        assert response.status_code == 400
        assert field in response.data["error"]["details"]

    def test_one_is_accepted(self, api, patient, prescriber, medication, location):
        response = api.post(
            URL,
            {
                "patient_id": str(patient.pk),
                "prescriber_id": str(prescriber.pk),
                "medication_id": str(medication.pk),
                "pharmacy_location_id": str(location.pk),
                "dose_amount": "1 tablet",
                "quantity": 1,
                "fills_authorized": 1,
            },
            format="json",
        )
        assert response.status_code == 201

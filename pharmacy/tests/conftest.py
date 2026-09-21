import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from .factories import (
    AddressFactory,
    MedicationFactory,
    PatientFactory,
    PharmacyFactory,
    PharmacyLocationFactory,
    PrescriberFactory,
    PrescriptionFactory,
)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="secret")


@pytest.fixture
def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def anon():
    return APIClient()


@pytest.fixture
def address(db):
    return AddressFactory()


@pytest.fixture
def patient(db):
    return PatientFactory()


@pytest.fixture
def prescriber(db):
    return PrescriberFactory()


@pytest.fixture
def medication(db):
    return MedicationFactory()


@pytest.fixture
def pharmacy(db):
    return PharmacyFactory()


@pytest.fixture
def location(db):
    return PharmacyLocationFactory()


@pytest.fixture
def prescription(db):
    return PrescriptionFactory()

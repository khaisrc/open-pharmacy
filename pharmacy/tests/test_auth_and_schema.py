import base64
import collections

import pytest
from django.contrib.auth import get_user_model
from drf_spectacular.generators import SchemaGenerator

pytestmark = pytest.mark.django_db

PROTECTED = [
    "/api/v1/prescriptions/",
    "/api/v1/patients/",
    "/api/v1/prescribers/",
    "/api/v1/medications/",
    "/api/v1/pharmacies/",
    "/api/v1/pharmacy-locations/",
    "/api/v1/addresses/",
]
OPEN = ["/api/v1/health/", "/api/v1/schema/", "/api/v1/docs/", "/api/v1/redoc/"]


class TestAuthentication:
    @pytest.mark.parametrize("url", PROTECTED)
    def test_protected_endpoints_401_without_credentials(self, anon, url):
        assert anon.get(url).status_code == 401

    @pytest.mark.parametrize("url", PROTECTED)
    def test_protected_endpoints_401_with_wrong_credentials(self, anon, url):
        token = base64.b64encode(b"admin:wrong").decode()
        assert anon.get(url, HTTP_AUTHORIZATION=f"Basic {token}").status_code == 401

    @pytest.mark.parametrize("url", OPEN)
    def test_open_endpoints_need_no_credentials(self, anon, url):
        assert anon.get(url).status_code == 200

    def test_basic_auth_is_accepted(self, anon):
        get_user_model().objects.create_user("basic", password="pw12345")
        token = base64.b64encode(b"basic:pw12345").decode()
        response = anon.get("/api/v1/patients/", HTTP_AUTHORIZATION=f"Basic {token}")
        assert response.status_code == 200


class TestSchema:
    @pytest.fixture
    def schema(self):
        return SchemaGenerator().get_schema(request=None, public=True)

    def test_every_operation_has_exactly_one_declared_tag(self, schema):
        declared = {tag["name"] for tag in schema["tags"]}
        for path, operations in schema["paths"].items():
            for method, operation in operations.items():
                tags = operation.get("tags", [])
                assert len(tags) == 1, f"{method.upper()} {path} has tags {tags}"
                assert tags[0] in declared, f"{method.upper()} {path} has stray tag {tags[0]}"

    def test_no_declared_tag_is_empty(self, schema):
        used = collections.Counter(
            tag
            for operations in schema["paths"].values()
            for operation in operations.values()
            for tag in operation.get("tags", [])
        )
        for tag in schema["tags"]:
            assert used[tag["name"]] > 0, f"declared tag {tag['name']} has no operations"

    def test_group_order_is_pinned(self, schema):
        assert [tag["name"] for tag in schema["tags"]] == [
            "Prescriptions",
            "Patients",
            "Prescribers",
            "Medications",
            "Pharmacies",
            "Pharmacy Locations",
            "Addresses",
            "System",
        ]

    def test_every_resource_is_present(self, schema):
        for path in PROTECTED + ["/api/v1/health/"]:
            assert path in schema["paths"], f"{path} missing from the schema"

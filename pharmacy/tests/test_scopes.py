"""The scopes a set of roles grants: the one union every token minter uses."""

from pharmacy.auth.scopes import ALL_SCOPES, TECHNICIAN_SCOPES, scopes_for_roles


def test_the_union_of_each_roles_grants():
    assert scopes_for_roles(["technician", "pharmacist"]) == ALL_SCOPES


def test_a_technician_gets_the_technician_scopes():
    assert scopes_for_roles(["technician"]) == TECHNICIAN_SCOPES


def test_an_unknown_role_or_none_grants_nothing():
    assert scopes_for_roles(["janitor"]) == frozenset()
    assert scopes_for_roles([]) == frozenset()

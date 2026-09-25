"""Every scope the API knows, named once.

The same names are created on the API resource in Logto and granted to its
roles; a token's `scope` claim is a space-separated subset. `read` covers
list, detail and the nested child lists; `write` covers create, update,
delete and a resource's actions. Prescriptions split their update scope in
two because who may change one depends on its state (see
`services.prescription`).
"""

from enum import StrEnum


class Scope(StrEnum):
    PRESCRIPTIONS_READ = "prescriptions:read"
    PRESCRIPTIONS_CREATE = "prescriptions:create"
    PRESCRIPTIONS_UPDATE = "prescriptions:update"
    # The same writes as PRESCRIPTIONS_UPDATE, honoured only while no fill
    # has been started.
    PRESCRIPTIONS_UPDATE_UNFILLED = "prescriptions:update_unfilled"
    FILLS_READ = "fills:read"
    FILLS_WRITE = "fills:write"
    FILLS_VERIFY = "fills:verify"
    PATIENTS_READ = "patients:read"
    PATIENTS_WRITE = "patients:write"
    PRESCRIBERS_READ = "prescribers:read"
    PRESCRIBERS_WRITE = "prescribers:write"
    MEDICATIONS_READ = "medications:read"
    MEDICATIONS_WRITE = "medications:write"
    PHARMACIES_READ = "pharmacies:read"
    PHARMACIES_WRITE = "pharmacies:write"
    PHARMACIES_ONBOARD = "pharmacies:onboard"
    LOCATIONS_READ = "locations:read"
    LOCATIONS_WRITE = "locations:write"
    ADDRESSES_READ = "addresses:read"
    ADDRESSES_WRITE = "addresses:write"
    STAFF_READ = "staff:read"
    STAFF_WRITE = "staff:write"


ALL_SCOPES = frozenset(Scope)
READ_SCOPES = frozenset(s for s in Scope if s.endswith(":read"))

# What a technician or intern may do: every read, the writes of the work
# they do at the counter, and a prescription only until it is filled.
TECHNICIAN_SCOPES = READ_SCOPES | {
    Scope.PRESCRIPTIONS_CREATE,
    Scope.PRESCRIPTIONS_UPDATE_UNFILLED,
    Scope.FILLS_WRITE,
    Scope.PATIENTS_WRITE,
    Scope.ADDRESSES_WRITE,
}

# The scopes each Logto role grants, as documented for whoever configures
# Logto; the API itself only ever reads the token's `scope` claim.
ROLE_SCOPES = {
    "admin": ALL_SCOPES,
    "pharmacist": ALL_SCOPES,
    "certified_technician": TECHNICIAN_SCOPES,
    "technician": TECHNICIAN_SCOPES,
    "intern": TECHNICIAN_SCOPES,
}


def crud_scopes(read, write, *, create=None, update=None):
    """`required_scopes` for a viewset: the standard actions mapped to one
    read scope and one write scope (or a separate create and update)."""
    return {
        "list": {read},
        "retrieve": {read},
        "create": {create or write},
        "update": {update or write},
        "partial_update": {update or write},
        "destroy": {update or write},
    }

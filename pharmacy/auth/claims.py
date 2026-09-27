"""The identity claims contract: what a verified token must say.

This is the API's vocabulary for who is acting, independent of which
identity provider issued the token. A provider adapter
(`pharmacy.auth.providers`) turns its native claim layout into these names;
`Principal.from_claims` reads only these. Change a provider, and only the
adapter changes.

| Claim                   | Meaning                                              |
|-------------------------|------------------------------------------------------|
| sub                     | the external user id; immutable for the user's life  |
| email                   | required                                             |
| name, given_name,       | standard OIDC profile                                |
|   family_name           |                                                      |
| roles                   | list of StaffRole names                              |
| scope                   | space-separated scopes (RFC 9068)                    |
| pharmacy_id             | the employer, a Pharmacy UUID; absent for an admin   |
| registration_state,     | the board registration                               |
|   registration_number   |                                                      |
| certification_body,     | the national technician certification (PTCB / NHA)   |
|   certification_number  |                                                      |
| supervising_user_id     | an intern's supervisor, by external user id          |
"""

SUBJECT = "sub"
EMAIL = "email"
NAME = "name"
GIVEN_NAME = "given_name"
FAMILY_NAME = "family_name"
ROLES = "roles"
SCOPE = "scope"
PHARMACY_ID = "pharmacy_id"
REGISTRATION_STATE = "registration_state"
REGISTRATION_NUMBER = "registration_number"
CERTIFICATION_BODY = "certification_body"
CERTIFICATION_NUMBER = "certification_number"
SUPERVISING_USER_ID = "supervising_user_id"

# Every name the contract defines, for a provider adapter to check itself
# against and for the docs.
ALL = (
    SUBJECT,
    EMAIL,
    NAME,
    GIVEN_NAME,
    FAMILY_NAME,
    ROLES,
    SCOPE,
    PHARMACY_ID,
    REGISTRATION_STATE,
    REGISTRATION_NUMBER,
    CERTIFICATION_BODY,
    CERTIFICATION_NUMBER,
    SUPERVISING_USER_ID,
)

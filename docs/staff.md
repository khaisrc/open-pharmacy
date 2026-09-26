# Staff

`/pharmacies/{id}/staff/`

A staff record is the domain identity of a person -- the name a state board
can look up -- and the identity behind a bearer token. `role` is one of
`admin`, `pharmacist`, `certified_technician`, `technician` or `intern`;
`roles` is every role the person holds, and `role` the highest of them in
that order. Two credentials are recorded separately because a board verifies
one and a certifying body the other, and neither stands in for the other:

- `registration_state` + `registration_number`: the state board registration,
  both or neither, unique per state among live records. Required for a
  pharmacist and a certified technician; optional for a technician or intern,
  since not every state registers them.
- `certification_body` (`PTCB` or `NHA`) + `certification_number`: the
  national technician certification, both or neither. Required for a
  certified technician; any role may hold one.

`npi` is optional and validated like every other NPI. `is_active=false` is a
person who has left or whose registration lapsed: the row stays, because
records will still name them. `DELETE` is for a record that should never
have existed. `pharmacy` is required for every role but `admin`, who works
at no particular one.

## Identity records

A user's first request with an access token creates their staff record from
the token's claims: `external_user_id` is the token's subject (the identity
provider's user id), and `email`, the name, `roles`, `pharmacy` and the
board registration are what the token said. The record is **never updated from a
token again**. Identity per subject is immutable by policy: if any of those
facts changes, the person gets a new user at the provider, and a token for the old
subject whose claims no longer match its record is refused (401
`identity_changed`). A token that cannot make a valid record -- a pharmacist
with no registration, a technician with no `pharmacy_id`, no recognised role
-- is refused too (401 `identity_incomplete`), and nothing is written.

A record that came from a token (`is_external`) is read-only through this
API and the admin except `is_active` (409 `identity_managed_externally`). A
record typed in here has no `external_user_id`: it is a directory entry that
can be named but cannot act.

Interns: the token must carry `supervising_user_id`, the subject of a
supervising pharmacist or technician. The API checks it is there (401
`supervisor_required`) and records nothing -- who supervised whom, and when
that changed, is the identity provider's record, so a change of supervisor
is not an identity change.

### Changing a person's details

At the identity provider: deactivate the old user and create the new one
with the real email, profile, roles and the rest (the Logto steps are in
[identity-providers/logto.md](identity-providers/logto.md)). Nothing to do
here: the new user's first request creates the successor record and,
because the registration and NPI constraints hold over *active* records
only, deactivates the record that held the same registration or NPI -- with
a change-history row naming the successor as the actor. An admin may also
set `is_active` by hand. The person's history is split across the two
subjects, which is the correct audit answer.

## Pharmacist-in-charge

A location's `pharmacist_in_charge` is the board-registered designation for
the site, kept for defaulting and validation. Designating one requires an
active staff member whose `roles` include `pharmacist` (an admin who is also
a pharmacist qualifies), employed by the same pharmacy; nothing revokes it
on its own, and it says nothing about who was supervising at any particular
moment.

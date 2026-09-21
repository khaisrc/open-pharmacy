# Staff

`/pharmacies/{id}/staff/`

A staff record is the domain identity of a person -- the name a state board
can look up -- and is deliberately not a login. `role` is one of
`pharmacist`, `certified_technician`, `technician` or `intern`. Two
credentials are recorded separately because a board verifies one and a
certifying body the other, and neither stands in for the other:

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
have existed.

## Pharmacist-in-charge

A location's `pharmacist_in_charge` is the board-registered designation for
the site, kept for defaulting and validation. Designating one requires an
active pharmacist employed by the same pharmacy; nothing revokes it on its
own, and it says nothing about who was supervising at any particular moment.

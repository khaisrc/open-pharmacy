# Prescriptions

`/prescriptions/`

## Lifecycle

A prescription starts `active`. The table is the complete set of transitions:

| From | To | How |
|---|---|---|
| `active` | `on_hold` | `PATCH {"status": "on_hold"}` |
| `on_hold` | `active` | `PATCH {"status": "active"}` |
| `active` | `completed` | a fill's `complete/` dispenses the last of the authorized quantity |
| `active` | `cancelled` | `POST {id}/cancel/` |
| `active` | `expired` | recorded when a fill, cancel or update finds `expires_at` in the past |

`completed`, `cancelled` and `expired` are terminal. Nothing leaves them, and no
field of a terminal prescription can be changed (409 `already_terminal`).
Setting a terminal status through `PATCH` is a 400 that names the endpoint
which owns that transition. `cancel/` is also refused while a fill is in
progress (409 `fill_in_progress`): cancel the fill first.

## Quantities

`quantity` is what one fill dispenses and `fills_authorized` how many the
prescriber allowed; nothing else about dispensing is stored on the
prescription. Everything below is derived from the fill records on every
read, so there is no counter to drift:

| Field | |
|---|---|
| `quantity_authorized` | `quantity × fills_authorized` |
| `quantity_dispensed` | the sum over `completed` fills |
| `quantity_in_progress` | what the fill in flight holds, if any |
| `quantity_remaining` | authorized − dispensed − in progress: what a new fill may take |
| `fills_used`, `fills_remaining` | the two quantities above over `quantity`, to eight decimal places — `0.33333333` after 30 of 90 |

`PATCH` to `quantity` or `fills_authorized` is a 400 when the new authorized
quantity would fall below what the fills already hold.

## Fills

A fill is one dispensing. It is listed and started through its
prescription, and once it exists it has an id of its own:

| Method | Path | |
|---|---|---|
| `GET` | `/prescriptions/{id}/fills/` | by `sequence`; filters `status`, `operator`, `verified_by` |
| `POST` | `/prescriptions/{id}/fills/` | start one: `{"quantity_dispensed"?, "days_supply"?}` → 201, `pending` |
| `GET` | `/fills/{fill_id}/` | |
| `PATCH` | `/fills/{fill_id}/` | edit `quantity_dispensed` or `days_supply` while `pending` (else 409 `not_pending`) |
| `POST` | `/fills/{fill_id}/verify/` | `pending → verified`; the token's staff member becomes `verified_by` (`fills:verify`) |
| `POST` | `/fills/{fill_id}/unverify/` | `verified → pending`, clearing `verified_by` |
| `POST` | `/fills/{fill_id}/complete/` | `verified → completed`; stamps `dispensed_at` |
| `POST` | `/fills/{fill_id}/cancel/` | `pending` or `verified → cancelled` |

That table is the complete set of transitions; any other is a 409
`invalid_transition` with `details.status`. `completed` and `cancelled` are
terminal. There is no top-level `/fills/` collection and no `PUT`; a fill
that should not have happened is cancelled, which keeps its `sequence` and
its history.

Rules, all held under the prescription's row lock:

- A prescription has **at most one fill in flight** (`pending` or
  `verified`). Starting another is a 409 `fill_in_progress`.
- A fill **reserves its quantity** from the moment it is created:
  `quantity_remaining` counts it, so nothing else can be promised against it.
  `cancel/` releases it.
- `quantity_dispensed` must be between 1 and `quantity_remaining`. There is
  no per-fill cap — a pharmacy may dispense more than one written fill's
  worth at once — only the total. Breaking it is a 400 on the field. When
  omitted it defaults to one written fill's worth, or whatever is left.
- `is_partial` is derived: less than one written `quantity`. 30 of 90 is a
  partial; the 60 that follows is too; together they consume one authorized
  fill, because the arithmetic is on units.
- `complete/` requires the prescription to still be `active` (a lapsed
  expiry is recorded and refused, 409 `expired`) and, when the completed
  quantity reaches `quantity_authorized`, completes the prescription.
- Starting a fill is refused with `not_active`, `expired` or
  `no_fills_remaining` for the same reasons the old `fill/` action was.

Each fill records `sequence`, the `pharmacy_location` at the time (a
snapshot — re-routing the prescription later does not rewrite it), the
`operator` who started it, the pharmacist who `verified_by` it, and
`dispensed_at`. That is the per-dispensing record a transfer under 21 CFR
1306.25 reproduces.

## Who is acting

A fill names the person who started it (`operator`) and the pharmacist who
verified it (`verified_by`): the staff record the bearer token maps to (see
[staff.md](staff.md)). Those two writes therefore need a token; a Basic or
session login can do everything else and is refused there with 403
`staff_identity_required`. Which actions a token may perform is its scopes
([api.md](api.md#scopes)); whoever it names is recorded on every
change-history row the request produces.

## Rx number and origin

`rx_number` is issued when the prescription is created and printed on the
label. It is the next value of the pharmacy organization's counter, shared
by all of its stores and unique within the pharmacy; two pharmacies may hold
the same number, and a label or a transfer names the store by its NCPDP ID.
A pharmacy whose counter has not been seeded cannot create prescriptions
(409 `rx_counter_not_seeded`). It is seeded by `POST` or `PATCH
/pharmacies/` with `rx_number_seed` (the value issuing starts after; the
first number is seed + step) and `step`; the state reads back as
`rx_counter`, null until then. A seed never moves a counter backwards (400).
`seed_local` seeds the local ones; "Onboarding a pharmacy" in
[deployment.md](deployment.md) covers the rest.

`intake_source` is how the order arrived — NCPDP D.0 field 419-DJ,
Prescription Origin Code, as a word rather than the wire digit: `not_specified`, `provider` (written), `telephone`,
`electronic`, `fax`, `pharmacy` (a new Rx number created from an existing
prescription, such as a transfer in) — codes 0 to 5 in that order on a
claim. It is required when the prescription is entered and cannot be
changed afterwards.

## The home location

`pharmacy_location_id` is optional on create. When omitted, the prescription is
routed to the **home location**, the store this deployment serves, identified
by `HOME_PHARMACY_LOCATION_NCPDP_ID`. The default points at the Acme Pharmacy
row that migration 0003 seeds into every database.

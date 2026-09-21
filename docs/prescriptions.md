# Prescriptions

`/prescriptions/`

## Lifecycle

A prescription starts `active`. The table is the complete set of transitions:

| From | To | How |
|---|---|---|
| `active` | `on_hold` | `PATCH {"status": "on_hold"}` |
| `on_hold` | `active` | `PATCH {"status": "active"}` |
| `active` | `completed` | `POST {id}/fill/` uses the last authorized fill |
| `active` | `cancelled` | `POST {id}/cancel/` |
| `active` | `expired` | recorded when a fill, cancel or update finds `expires_at` in the past |

`completed`, `cancelled` and `expired` are terminal. Nothing leaves them, and no
field of a terminal prescription can be changed (409 `already_terminal`).
Setting a terminal status through `PATCH` is a 400 that names the endpoint
which owns that transition.

## Filling

`POST {id}/fill/` increments `fills_used`, sets `completed` when
`fills_remaining` hits zero, and is a 409 when the prescription is not active,
has no fills left, or has expired. Fills and cancels lock the row, so two
concurrent requests cannot both succeed.

## The home location

`pharmacy_location_id` is optional on create. When omitted, the prescription is
routed to the **home location**, the store this deployment serves, identified
by `HOME_PHARMACY_LOCATION_NCPDP_ID`. The default points at the Acme Pharmacy
row that migration 0003 seeds into every database.

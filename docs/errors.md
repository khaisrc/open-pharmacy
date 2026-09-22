# Errors

Every error has one shape:

```json
{
  "error": {
    "code": "duplicate_npi",
    "message": "A live prescriber with this NPI already exists.",
    "details": {"field": "npi", "npi": "1234567893", "existing_id": "…"}
  }
}
```

| Status | `code` | When |
|---|---|---|
| 400 | `validation_error` | `details` maps each field to its errors |
| 401 | `not_authenticated` | missing or bad credentials |
| 404 | `not_found` | |
| 409 | `duplicate_<field>` | a live record already holds that NPI, NDC, NCPDP ID, store number or state registration (`duplicate_registration_number`, with `details.registration_state`) |
| 409 | `referenced` | delete refused; `details.referenced_by` counts the live references, including a location whose pharmacist-in-charge is being deleted |
| 400 | `staff_required`, `invalid_staff`, `pharmacist_required` | the `X-Staff-Id` header is missing where a fill needs a person, names nobody live and active, or names someone who is not a pharmacist |
| 409 | `not_active`, `no_fills_remaining`, `expired` | starting or completing a fill refused; `details` carry the quantities |
| 409 | `fill_in_progress` | a second fill, or a prescription cancel, while a fill is pending or verified; `details.fill_id` |
| 409 | `invalid_transition` | a fill action from a status that does not allow it; `details.status` and `details.action` |
| 409 | `not_pending` | `PATCH` on a fill that is no longer pending |
| 409 | `rx_counter_not_seeded`, `rx_number_exhausted` | the pharmacy cannot issue an Rx number; `details.pharmacy_id` |
| 409 | `already_terminal` | cancel or update refused; `details.status` is the current status |
| 429 | `throttled` | 100/min anonymous, 1000/min authenticated by default |

Every response carries an `X-Request-ID` header, echoed from the request if
sent, and every log line for that request includes it.

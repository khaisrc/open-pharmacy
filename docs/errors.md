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
| 404 | `not_found` | |
| 409 | `duplicate_<field>` | a live record already holds that NPI, NDC, NCPDP ID, store number or state registration (`duplicate_registration_number`, with `details.registration_state`) |
| 409 | `referenced` | delete refused; `details.referenced_by` counts the live references, including a location whose pharmacist-in-charge is being deleted |
| 401 | `token_missing`, `token_invalid`, `token_expired` | no bearer token, one that fails verification (signature, issuer, audience, unknown key), or one past `exp` |
| 401 | `token_without_email`, `identity_incomplete`, `supervisor_required` | the token verifies but cannot make a valid staff record: no `email` claim, no recognised role, a pharmacist without a registration, a non-admin without a known `pharmacy_id`, an intern without `supervising_user_id` |
| 401 | `identity_changed` | the token's identity differs from the staff record first created for its subject; deactivate the user at the identity provider and create a new one |
| 403 | `insufficient_scope` | the token lacks every scope the action needs; `details.required` lists them, and `details.reason` says when it is the prescription's state (a fill started) rather than the token |
| 403 | `staff_identity_required` | starting or verifying a fill as a login rather than a token |
| 503 | `identity_unavailable` | the identity provider's keys could not be fetched, so the token was not judged; retry |
| 409 | `not_active`, `no_fills_remaining`, `expired` | starting or completing a fill refused; `details` carry the quantities |
| 409 | `fill_in_progress` | a second fill, or a prescription cancel, while a fill is pending or verified; `details.fill_id` |
| 409 | `invalid_transition` | a fill action from a status that does not allow it; `details.status` and `details.action` |
| 409 | `not_pending` | `PATCH` on a fill that is no longer pending |
| 409 | `rx_counter_not_seeded`, `rx_number_exhausted` | the pharmacy cannot issue an Rx number; `details.pharmacy_id` |
| 409 | `already_terminal` | cancel or update refused; `details.status` is the current status |
| 409 | `identity_managed_externally` | a change other than `is_active` to a staff record that came from a token |
| 429 | `throttled` | 100/min anonymous, 1000/min authenticated by default |

Every response carries an `X-Request-ID` header, echoed from the request if
sent, and every log line for that request includes it.

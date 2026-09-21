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
| 409 | `not_active`, `no_fills_remaining`, `expired` | fill refused |
| 409 | `already_terminal` | cancel or update refused; `details.status` is the current status |
| 429 | `throttled` | 100/min anonymous, 1000/min authenticated by default |

Every response carries an `X-Request-ID` header, echoed from the request if
sent, and every log line for that request includes it.

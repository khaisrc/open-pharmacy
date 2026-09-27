# Test users

The people a local stack can sign in as, created at the identity provider by
`manage.py seed_auth` (the `auth-seed` Compose service) and kept in step
with `seed_auth.TEST_USERS` by a test. Every one shares the password
**`Passw0rd!`**, is employed by the home pharmacy (the admins by none),
and has the email `<given>.<family>@open-pharmacy.local`. Sign in with the
username or the email.

| Username | Email | Name | Roles | Credentials | Supervisor |
|---|---|---|---|---|---|
| `elena_ortiz` | `elena.ortiz@open-pharmacy.local` | Elena Ortiz | pharmacist | registration NY RPH.140001 | -- |
| `brendan_walsh` | `brendan.walsh@open-pharmacy.local` | Brendan Walsh | pharmacist | registration NY RPH.140002 | -- |
| `soojin_kim` | `soojin.kim@open-pharmacy.local` | Soo-jin Kim | certified_technician | registration NY RPT.240001; PTCB 20240001 | -- |
| `marcus_bell` | `marcus.bell@open-pharmacy.local` | Marcus Bell | certified_technician | registration NY RPT.240002; PTCB 20240002 | -- |
| `priya_natarajan` | `priya.natarajan@open-pharmacy.local` | Priya Natarajan | certified_technician | registration NY RPT.240003; PTCB 20240003 | -- |
| `tomas_reyes` | `tomas.reyes@open-pharmacy.local` | Tomás Reyes | certified_technician | registration NY RPT.240004; PTCB 20240004 | -- |
| `hannah_oconnell` | `hannah.oconnell@open-pharmacy.local` | Hannah O'Connell | certified_technician | registration NY RPT.240005; PTCB 20240005 | -- |
| `marco_diaz` | `marco.diaz@open-pharmacy.local` | Marco Diaz | technician | -- | -- |
| `aisha_rahman` | `aisha.rahman@open-pharmacy.local` | Aisha Rahman | technician | -- | -- |
| `liam_chen` | `liam.chen@open-pharmacy.local` | Liam Chen | technician | -- | -- |
| `grace_okafor` | `grace.okafor@open-pharmacy.local` | Grace Okafor | technician | -- | -- |
| `noah_fischer` | `noah.fischer@open-pharmacy.local` | Noah Fischer | technician | -- | -- |
| `sam_whitaker` | `sam.whitaker@open-pharmacy.local` | Sam Whitaker | intern | -- | `elena_ortiz` |
| `jade_moreau` | `jade.moreau@open-pharmacy.local` | Jade Moreau | intern | -- | `brendan_walsh` |
| `ada_lovelace` | `ada.lovelace@open-pharmacy.local` | Ada Lovelace | admin | -- | -- |
| `grace_hopper` | `grace.hopper@open-pharmacy.local` | Grace Hopper | admin | -- | -- |

What each role may do is the scope table in [../api.md](../api.md#scopes):
admins and pharmacists everything; certified technicians and technicians
every read plus creating and (until a fill is started) changing
prescriptions, fills, patients and addresses -- not verifying a fill, not
onboarding a pharmacy; interns the same, supervised. The seed checks every
user's token against the API's own reading on each run, so a user listed
here can sign in and call the API.

## Getting a token

Through the UI: the application `open-pharmacy-ui` (single-page, PKCE,
redirect `http://localhost:3000/callback`), requesting this API's audience and scopes at
sign-in.

Without a UI, `manage.py auth_token` asks the provider for one by username
-- the same token the user would get by signing in, with their own subject
and the scopes their roles grant (`--scopes` asks for fewer). The stack
must be up with the `auth` profile (`make start`):

```bash
TOKEN=$(make -s token AS=elena_ortiz)
# or: TOKEN=$(docker compose exec -T web python manage.py auth_token elena_ortiz)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/prescriptions/
```

Under the hood it is what the seed's own check does: a subject token for the
user from the Management API, exchanged at the token endpoint for this API's
audience (`grant_type=urn:ietf:params:oauth:grant-type:token-exchange` with
`open-pharmacy-ui`'s client id). Tokens last 15 minutes; ask again when one
expires.

`manage.py mint_token` is the other way, without the provider at all, but it
is for identities of your own making: it invents a subject from the email
unless given `--sub`, so a minted token carrying one of these users' emails
is a second person claiming that email and is refused.

A token's `scope` is whatever the user's roles grant, capped at what was
requested; an action the token does not cover is a 403 `insufficient_scope`
naming the scope it takes.

# E-prescribing: the erx-gateway, and what pharmacy-api needs for it

Status: **ready for review. Nothing is implemented.**

Builds on `plans/auth/` (bearer tokens, scopes, `Staff` as the identity
record) and `plans/rx-tx-fills/` (the service layer, `WriteContext`, the
change history, the prescription lifecycle).

---

## 0. Decisions

The choices the design rests on, each with its reason and the section that
implements it. Everything below is settled; the body of the plan is the
design that follows from these.

| Decision | Why | Where |
|---|---|---|
| The NCPDP SCRIPT 2017071 XSD set (six files) is committed under `services/erx-gateway/standards/ncpdp/script/schema/2017071/`. | The gateway cannot validate without it and CI builds from the repository. Nothing else from the reference collection is committed. | §5.1 |
| The vendor is not Surescripts but behaves like it; there is no implementation guide and no sandbox. | The `vendor-sim` is the only counterparty the gateway is exercised against before production, so its behaviour is held strictly to the schema. | §8.3 |
| Inbound: plain HTTPS `POST`, the SCRIPT XML as the body, Basic credentials. | What the vendor does. TLS and IP restrictions belong to the proxy. | §5.2, §5.3 |
| A transient refusal is both the SCRIPT `Error 600` body and HTTP 503. | Whichever the vendor reads, it retries. | §5.3, §5.4 |
| Outbound: `POST` to the vendor with Basic credentials. The vendor imposes no response window; this service sets one, `ERX_RESPONSE_DEADLINE` (48 h), bounding how long a `CancelRxResponse` may stay unsent and how long a parked cancel waits for a pharmacist, who can also trigger the response earlier. | A response must not be owed forever, and a pharmacist must be able to answer now. | §4.3.5, §5.8 |
| `ReturnReceipt` and `TestMessage` are honoured but are not core: one unit test each, no e2e case. | Cheap; `TestMessage` keeps test traffic from becoming real prescriptions. | §5.3, §5.4 |
| A pharmacy is matched, never created. A message addressed to a pharmacy this deployment does not serve is acknowledged and parked for review, not refused. The intake call carries the NCPDP ID (always in the header) and the NPI when the message has it. | The `Pharmacy` block of a `NewRx` is optional in the schema; the header's `To` is always there. Routing mistakes are a person's call. | §4.3.3, §5.3 |
| Object storage: LocalStack in Compose and CI, moto in process for unit tests. The e2e suite verifies a received message is persisted as an XML object in the bucket. | Fidelity to S3 outranks footprint; the suite proves the integration, not the library. | §6.1, §8.3 |
| Task queue: `django.tasks` with `django-tasks-db`, not Celery. | Enqueue is an INSERT in the caller's transaction: a message row can never be committed without its task. | §6.2 |
| Redis, now, as the gateway's cache. | Shared throttle buckets and token cache across replicas; small. | §6.3 |
| Intake is synchronous and idempotent inside pharmacy-api; no queue there. | One retry engine (the gateway's task) is enough at this volume. | §4.3 |
| The gateway hands pharmacy-api a canonical JSON document; the raw XML on request. | SCRIPT parsing lives in exactly one service. | §5.6 |
| Idempotency is keyed on the SCRIPT `MessageID`, with natural-key matching underneath; no generic `Idempotency-Key` header. | The gateway never calls the public create endpoints. | §4.3.3 |
| Patient matching: exact normalized `(last_name, first_name, dob)` among live rows, `FormerName` as a second key; several matches go to review, never a guess. | Wrong-patient is the error a pharmacy must not make. | §4.3.3 |
| `Patient.phone_number`, `Medication.ndc` and `Prescription.dose_amount` become optional. | The schema makes a patient's phone optional, forbids an NDC on a compound, and has no dose element outside the structured sig. | §4.4 |
| A new prescription status, `needs_review`, is the parking state for every prescription intake creates but a pharmacist has not cleared (no NDC, controlled substance, `DoNotFill`, emergency cover, compound). `on_hold` keeps its current meaning. | Such a prescription must exist and must not be fillable, and "on hold" already means a pause of an active one. | §4.5 |
| `Prescription.quantity` and `Fill.quantity_dispensed` become decimals. | The schema's quantity is `[0-9]+(\.[0-9]+)?`; liquids and units are real. | §4.5 |
| A `CancelRx` against a prescription with a fill in flight is parked for a pharmacist with a deadline and auto-denied when it passes. | The pharmacist, not a rule, decides whether to stop a fill in progress. | §4.3.4, §4.3.5 |
| A controlled-substance `NewRx` is ingested like any other and parked for review. | This pharmacy does not dispense controlled substances; the record must still exist. | §4.3.5, §4.4 |
| A service actor is recognized by `sub == client_id`; its writes are attributed through `FieldChange.changed_by_service`. | RFC 9068 and what Logto emits; the audit trail must name the actor. | §4.2 |
| Locally, service-to-service tokens are dev-signed with the committed `AUTH_DEV_SIGNING_KEY`, in every local mode and in CI. The real client-credentials path is exercised by `seed_auth`, which creates the M2M applications and checks a real token from each. | Easiest for local development: nothing to restart, no secret to hand around (Logto generates M2M secrets and cannot be given one), and the real grant is still proven on every seed. | §4.2.3 |
| The provider-neutral auth code is a workspace package, `packages/pharmacy-auth`, used by both services. | The gateway must verify the token pharmacy-api presents; sharing beats duplicating. | §3.2, §4.2.0, §5.2 |
| One e2e suite, the bash one, with cases split per service; the bucket check is curl against LocalStack. | One style, as `AGENTS.md` asks; no second harness. | §8.3 |
| Review deadlines are driven by the gateway's sweep calling pharmacy-api's `expire` action. | pharmacy-api has no worker; the gateway is the party that owes the vendor a response and already polls. | §4.3.5, §5.8 |

---

## 1. Scope

In scope:

1. **Restructure** the repository into two services under `services/`, a
   uv workspace with one lock, one Dockerfile per service built from the
   root with only that service's dependencies and source, the Compose
   service renamed `web` -> `pharmacy-api`, a migrate step and environment
   files per service, CI per service, e2e across both.
2. **pharmacy-api**: a service (machine-to-machine) principal that is not a
   `Staff` row; an idempotent intake endpoint the gateway calls; the model
   additions a `NewRx`/`CancelRx` carries that the domain should keep; the
   matching rules that make intake idempotent; the identity-provider seed
   for the M2M applications.
3. **erx-gateway**: receives NCPDP SCRIPT 2017071 `NewRx` and `CancelRx`
   over HTTP, persists every raw message to object storage before
   acknowledging, validates against the schema, answers with the SCRIPT
   `Status` or `Error` message, then drives intake through pharmacy-api and
   sends the `CancelRxResponse` (and `Verify` when a return receipt was
   asked for) back to the vendor, with retries and a defined failure
   model.

Out of scope (deliberately, noted where the design leaves a seam): every
other SCRIPT transaction (`RxRenewalRequest`/`Response`, `RxChange`,
`RxFill`, `RxTransfer`, `Resupply`, `Census`, ...); EPCS signature
verification; allergies, insurance (`BenefitsCoordination`) and observations
as domain records (kept only inside the stored message); a review UI
(pharmacy-api exposes the review queue as an endpoint and in the admin);
metrics/tracing beyond structured logs; multi-pharmacy deployments.

---

## 2. Facts the design rests on

Checked against the code and a running Logto 1.43, the way
`plans/auth/plan.md` §8.2 did.

### 2.1 The repository

- One Django project, one image, one Compose service `web`; `migrate` is a
  separate one-shot service; `auth` and `auth-seed` sit behind the `auth`
  profile. CI builds `web` and runs pytest, ruff, the migrations check and
  the e2e suite through Compose.
- Every write goes through a service with a `WriteContext`; the change
  history (`FieldChange`) names exactly one of `changed_by_user` /
  `changed_by_staff` by CHECK constraint. A bearer principal without a
  `Staff` row cannot exist today: `BearerTokenAuthentication` refuses a
  token without `email` (401 `token_without_email`) before it ever reaches
  `services.staff.identify`.
- Live-row uniqueness is partial (`uniq_live_npi`, `uniq_live_ndc`,
  `uniq_live_ncpdp`, `uniq_live_location_npi`); the serializer's
  `reject_live_duplicate()` is the pre-check and `LIVE_UNIQUE_CONSTRAINTS`
  maps the race to a 409. `Patient` has no natural key at all.
- `Prescription.quantity` and `Fill.quantity_dispensed` are positive
  integers; every total is derived from them. `Patient.phone_number`,
  `Medication.ndc` and `Prescription.dose_amount` are required.
- The e2e suite is bash + curl + jq against one `BASE_URL`, mints staff
  tokens through `E2E_MINT_CMD`, and assumes `docker compose exec -T web`.

### 2.2 Logto 1.43 and machine-to-machine tokens

- A **machine-to-machine application** plus a **machine-to-machine role**
  granting scopes on the API resource, then
  `grant_type=client_credentials` with `resource=<audience>`, yields an
  ES384 `at+jwt` whose claims are exactly `jti, sub, iat, exp, scope,
  client_id, iss, aud` -- **`sub == client_id`**, no `email`, no profile,
  nothing from the user customizer (its `context.user` is undefined, and
  the existing script already returns `{}` then). TTL is the resource's
  (900 s).
- The token's `scope` is silently capped at what the M2M role grants
  (`fills:verify` requested, dropped) -- the same behaviour as user tokens.
- Logto has a second JWT customizer slot, `client-credentials`, so a
  service token can be decorated if ever needed; the design below does not
  depend on it.
- Application secrets are **generated by Logto**; `POST
  /applications/{id}/secrets` takes a name and expiry only, and the default
  secret's value is readable through `GET /applications/{id}/secrets`. A
  seed can therefore read a secret and hand it out, but cannot make Logto
  accept one of its choosing.
- Today the API answers that token with 401 `token_without_email`.

### 2.3 Libraries

- Django 6.1 ships the `django.tasks` API (DEP 14) with only the
  `ImmediateBackend` and `DummyBackend`. The database worker is the separate
  `django-tasks-db` 0.13 (BSD, same author as the DEP): Postgres
  `SELECT ... FOR UPDATE SKIP LOCKED`, `run_after`, priorities, a
  `db_worker` command and a prune command; **`enqueue()` is a plain INSERT
  in the caller's transaction**; no built-in retry policy (the attempt
  number is exposed through `TaskContext`); no scheduler.
- Celery 5.6.3, redis 8.1, django-redis 7.0, boto3 1.43, moto 5.2,
  lxml 6.1, httpx 0.28 are all available (`xmlschema` 4.3 too, but §2.4
  rules it out).
- `uv lock` is regenerated after the workspace change where the real
  package index is reachable; the lock is never edited by hand.

### 2.4 The SCRIPT 2017071 schema

**Shape.** Six files, no target namespace (instances are unqualified);
`transport.xsd` is the entry point. The root is `<Message>` with six
mandatory version attributes (all `20170715`), a `Header` and a `Body`
holding exactly one of 49 transactions. `Header`: `To` and `From` (each
with a `Qualifier`: `P` pharmacy, `D` prescriber, `C` clinic, `M`
mailbox, `CF` central fill, `ZZZ`), `MessageID` (`an1..35`),
`RelatesToMessageID` (mandatory on every response and follow-up),
`SentTime` (UTC), `Security`, `SenderSoftware` (mandatory),
`TestMessage`, `RxReferenceNumber` (the pharmacy's Rx number),
`PrescriberOrderNumber` (the prescribing system's reference -- "some
vendors carry it through the life of the prescription; others change it per
order"), `DigitalSignature`, and two order-group elements.

**Validators.** `xmlschema` (a strict XSD 1.0 processor) **refuses to load
the set**: NCPDP derives many types by restriction with `maxOccurs="0"`
elements, which it reports as illegal restrictions. libxml2 accepts it:
`lxml.etree.XMLSchema` compiles `transport.xsd` in 70 ms and reports
faults with line numbers and element paths (e.g. `Element 'Gender': [facet
'enumeration'] The value 'X' is not an element of the set {'M', 'F',
'U'}`). `xmllint --schema` agrees. So the gateway validates and parses with
lxml, and `xmlschema` is not a dependency.

**The response transactions, as the schema documents them.**

- `Status` -- "relays acceptance of a transaction back to the sender. A
  Status in response **indicates acceptance and responsibility**. A Status
  cannot be mailboxed and may not contain an error." Codes: `000`
  transaction successful, `010` **successful -- accepted by ultimate
  receiver** (that is us), plus mailbox codes. Optional `DescriptionCode`,
  `Description` (70), `PrescriptionDeliveryMethod`.
- `Error` -- "indicates the request was terminated ... when there is a
  communication problem or when the transaction actually had an error."
  `Code` is one of **`600` communication problem -- try again later, `601`
  receiver unable to process, `602` receiver system error, `700`
  configuration error, `900` transaction rejected**; up to ten
  `DescriptionCode`s (`500` XML syntax error -- parser error, XPath must
  accompany; `3000` does not follow the NCPDP standard; `2000` data format
  valid but content invalid for the situation/context, XPath must
  accompany; `1000` unable to identify based on the information
  submitted, XPath must accompany; `210` unable to process, please
  resubmit; `220` transaction is a duplicate; `4040` receiver does not
  support receiving this transaction type; and intermediary codes);
  `Description` free text.
- `Verify` -- "generated when the request transaction asks for return
  receipt" (`ReturnReceipt` on the request); its code, if used, must be
  `010`. It is a receipt, not a "processed later" notification.
- `NewRxResponseDenied` -- "a mechanism for the **prescriber** to deny
  the new prescription request" (the response to a pharmacy's
  `NewRxRequest`). Not a pharmacy-to-prescriber message.
- **There is no transaction by which a pharmacy retracts a `Status` on a
  `NewRx`.** `RxFill` ("notify the prescriber of the dispensing status",
  including not dispensed) is the only later channel, and it is out of
  scope here. This shapes §5.3 and §7.3.
- `CancelRxResponse` -- "sends to the prescriber system the results of a
  prescription cancellation request." `Response` is `Approved`
  (`ReferenceNumber`?, `Note` 70) or `Denied` (`ReasonCode` 0..10 from
  **`AA` patient unknown to the provider, `AB` never under provider care,
  `AC` no longer under provider care, `AP` already responded to by other
  means, `AR` prescription was transferred to another pharmacy**;
  `ReferenceNumber`?; `DenialReason` free text, which "must accompany" a
  denial when no `ReasonCode` fits; `Pharmacy`?). The patient, prescriber
  and medication blocks are **not** part of it (`maxOccurs="0"`). The
  header's `RelatesToMessageID` names the `CancelRx`; `RxReferenceNumber`
  carries our Rx number.

**`NewRx` body** (mandatory unless marked): `Patient` (a choice of
`HumanPatient` | `NonHumanPatient`); `Pharmacy`? (`NCPDPID` and `NPI`
both mandatory when present); `Prescriber` (`NonVeterinarian` |
`Veterinarian`); `Supervisor`?; `Facility`?; `AllergyOrAdverseEvent`?;
`BenefitsCoordination` 0..4; `Observation`?; `MedicationPrescribed`;
`ProhibitRenewalRequest`?; `FollowUpPrescriber`?; plus `ReturnReceipt`?,
`RequestReferenceNumber`?, `UrgencyIndicatorCode`?.

- `HumanPatient` (the NewRx variant): `Identification`? (Medicare,
  Medicaid, `MedicalRecordIdentificationNumberEHR`, `PatientAccountNumber`,
  `SocialSecurity`, `MutuallyDefined`, `REMSPatientID`), `Name`
  (`LastName`, `FirstName` 35 each; `MiddleName`?, `Suffix`?, `Prefix`?),
  `FormerName`?, `Gender` **`M` | `F` | `U`** (no fourth value in this
  release), `DateOfBirth` (`Date` or `DateTime`), **`Address` mandatory**
  (`AddressLine1`, `City`, `StateProvince`, `PostalCode`, `CountryCode`
  all mandatory; `AddressLine2`?), `CommunicationNumbers`? (when present
  `PrimaryTelephone` is mandatory; a phone is `Number` `n1..10`,
  `Extension`?, `SupportsSMS`? Y/N; also `HomeTelephone`*,
  `WorkTelephone`*, `Fax`*, `ElectronicMail`*, `DirectAddress`?),
  `PatientLocation`?, `LanguageNameCode`?, `SubstanceUse`?,
  `AlternateContact`?, `GestationalAge`?, `HospiceIndicator`?.
- `NonVeterinarian` prescriber: `Identification` (**`NPI` mandatory**;
  `DEANumber`?, `StateLicenseNumber`?, `Data2000WaiverID`?,
  `StateControlSubstanceNumber`?, `REMSHealthcareProviderEnrollmentID`?,
  Medicare/Medicaid/UPIN/HIN/SSN?), `Specialty`? (Health Care Provider
  Taxonomy code), `PracticeLocation`? (`Identification`?, `BusinessName`?),
  `Name`, `FormerName`?, **`Address` mandatory**, `PrescriberAgent`?,
  **`CommunicationNumbers` mandatory** (so a prescriber phone always
  arrives), `PrescriberPlaceOfService`?.
- `MedicationPrescribed`: `DrugDescription` (105) mandatory; `DrugCoded`?
  with `ProductCode`? (`Code` + `Qualifier`: **`ND` NDC**, `DI`, `RT`,
  `UN`, `UP`, `NH`, `GMP`, `GPI`, `GSP`), `Strength`? (`StrengthValue`
  free text 70, `StrengthForm` and `StrengthUnitOfMeasure` NCI codes),
  `DrugDBCode`? (`Code` + `Qualifier`: RxNorm **`SCD`, `SBD`, `GPK`,
  `BPK`** among First Databank, Medi-Span, Multum, Micromedex, Gold
  Standard codes), `DEASchedule`? (an NCI code, "required if the
  medication is a controlled substance"); **`Quantity` mandatory**
  (`Value` `n1..11` whose pattern is `[0-9]+(\.[0-9]+)?` -- **decimals
  are legal**; `CodeListQualifier` `38` original quantity, `40`, `87`,
  `QS` quantity sufficient, `CF`, `UQ`, `QT`; `QuantityUnitOfMeasure` an
  NCI code); `DaysSupply`? (`n1..3`); **`WrittenDate` mandatory** ("must
  precede or equal the transmission date"; transmission within 72 h
  recommended); `LastFillDate`?; **`Substitutions` mandatory** (`0` no
  product selection indicated, `1` substitution not allowed);
  **`NumberOfRefills` mandatory** (`n1..2`); `Diagnosis` 0..2
  (`ClinicalInformationQualifier`, `Primary`/`Secondary` with `Code`,
  `Qualifier` **`ABF` ICD-10-CM**, `DX` ICD-9, `LD` SNOMED, `Description`
  mandatory, `DateOfLastOfficeVisit`?); `PriorAuthorization`? (35);
  `PriorAuthorizationStatus`?; `Note`? (210); **`Sig` mandatory**
  (`SigText` 1000 mandatory, the structured sig optional); `DoNotFill`?
  (**`Y`** ordered but not to be dispensed, for reference and interaction
  checking; **`H`** hold -- the pharmacy may fill but should wait for the
  patient; **`E`** cover prescription for an emergency oral order);
  `OtherMedicationDate`* (`Date` + `Qualifier` including
  **`EffectiveDate`** "after which this prescription can be dispensed",
  **`ExpirationDate`** "the final date", `StartDate`); `RxFillIndicator`?
  (eight values); `PrescriberCheckedREMS`?; `CompoundInformation`?
  (final dosage form + 1..25 ingredients; `ProductCode`/`DrugDBCode`
  "must not be sent" for a compound); `SplitScript`?; `DeliveryRequest`?;
  `TimeZone`?; `OrderCaptureMethod`?; and a long tail of LTC and REMS
  elements.

**`CancelRx` body**: `Patient` (same choice), `Pharmacy`?, `Prescriber`
(NPI mandatory), `MedicationPrescribed` "as originally sent" (same shape:
description, quantity, written date, substitutions, refills, sig all
mandatory), `Supervisor`?, `Facility`?, `ChangeOfPrescriptionStatusFlag`?;
the header's `RxReferenceNumber`, `PrescriberOrderNumber` and
`RelatesToMessageID` are what identify the prescription.

Not in the set: a `CancelRx` or `CancelRxResponse` example. Those
fixtures are authored against the schema in Stage 3; the provided `NewRx`
example (Nurtec ODT, NDC `72618300002`, quantity 8, 3 refills) becomes the
first fixture as-is.

---

## 3. Stage 1 -- restructuring into two services

### 3.1 Layout

```text
pyproject.toml                 # workspace root: [tool.uv.workspace] members = ["services/*", "packages/*"];
                               # the ruff configuration (one style for both services); dev group: ruff
uv.lock                        # the single lock, covering both members
packages/
  pharmacy-auth/               # the shared auth package (§4.2.0): pyproject.toml, pharmacy_auth/, tests/
services/
  pharmacy-api/                # everything that is at the root today, moved with `git mv`
    pyproject.toml             # name "open-pharmacy-api"; its own dependencies and dev group
    Dockerfile
    Dockerfile.dockerignore    # BuildKit reads <Dockerfile>.dockerignore next to the Dockerfile
    manage.py  config/  pharmacy/
    .env.local  .env.example
  erx-gateway/                 # new (Stages 3-4)
    pyproject.toml             # name "erx-gateway"
    Dockerfile  Dockerfile.dockerignore
    manage.py  config/  erx/
    .env.local  .env.example
e2e/                           # stays at the root; cases split per service (§8.3)
docker-compose.yml  Makefile  AGENTS.md  README.md  docs/  plans/  .github/  scripts/
```

`git mv` keeps history. The root `.gitignore`, `.python-version`,
`LICENSE`, `README.md`, `AGENTS.md`, `docs/` and `plans/` stay at the root;
`AGENTS.md` gets a short per-service section and every `docker compose run
--rm web ...` in it becomes `pharmacy-api`.

### 3.2 The workspace

- Root `pyproject.toml` has no runtime dependencies of its own; it declares
  the workspace, the shared `[tool.ruff]` configuration (line length 100,
  the same rule set, `services/*/*/migrations` excluded), and a dev group
  with `ruff` so `uv run ruff check .` at the root lints both.
- Each member's `pyproject.toml` carries its own `dependencies`, its own
  `[dependency-groups] dev` (pytest, pytest-django, factory-boy, pgserver
  for pharmacy-api; pytest, pytest-django, moto, respx-or-equivalent for
  the gateway) and its own `[tool.pytest.ini_options]`.
- `packages/pharmacy-auth` is a workspace member both services depend on
  through `[tool.uv.sources] pharmacy-auth = { workspace = true }`. It
  depends on Django, DRF, drf-spectacular and PyJWT only; its tests run
  with a minimal settings module of its own.
- One `uv.lock`; `uv sync --package <name>` installs one member's closure,
  the shared package included.

### 3.3 One Dockerfile per service, built from the root

Two stages, so the final image holds only the service's venv and source:

1. `builder`: copy the root `pyproject.toml`, `uv.lock`, **every**
   member's `pyproject.toml` (uv needs all members to load the workspace,
   even when syncing one) and `packages/pharmacy-auth/` (its source must
   be present to be installed), then `uv sync --locked --package <name>
   --no-install-project --no-dev --no-editable` into `/opt/venv`, so the
   shared package is copied into the venv rather than linked to a
   checkout.
2. `app`: copy `/opt/venv`, copy `services/<name>/` only, `collectstatic`,
   unprivileged user, no entrypoint (migrations never run at boot -- the
   existing rule, unchanged). Default `CMD` is `runserver` locally,
   overridden with gunicorn in deployed tiers as today.

Compose builds each with `context: .` and `dockerfile:
services/<name>/Dockerfile`. The bind mount for local development becomes
`./services/<name>:/app`.

### 3.4 Compose

| Service | Role | Profile |
|---|---|---|
| `db` | PostgreSQL 18, three databases (`open_pharmacy`, `erx_gateway`, `auth`) created by the init script (`scripts/create-databases.sh`, replacing `create-auth-db.sh`) | |
| `pharmacy-api-migrate` | one-shot: `migrate --noinput && seed_local` (the current `migrate`) | |
| `pharmacy-api` | the API on `WEB_PORT` (default 8000), depends on its migrate | |
| `erx-gateway-migrate` | one-shot: `migrate --noinput && ensure_bucket` (§5.5) | |
| `erx-gateway` | the gateway on `ERX_PORT` (default 8010) | |
| `erx-gateway-worker` | `manage.py db_worker` (§6.2) | |
| `s3` | LocalStack (§6.1), port 4566 | |
| `redis` | Redis 7, port 6379 (§6.3) | |
| `vendor-sim` | the fake e-prescribing vendor the e2e suite asserts against (§8.3) | |
| `auth`, `auth-seed` | unchanged, plus the M2M seeding (§4.2.3) | `auth` |

Each service reads its own `services/<name>/.env.local`; the `db` service
keeps reading pharmacy-api's for `POSTGRES_*` (the credentials the
databases are created with; both services connect with the same role
locally, each to its own database -- the gateway never reads pharmacy-api's
tables, by construction, and the deployed tiers give each service its own
role).

Makefile: `units`, `lint` and `format` take `SERVICE=` (default both, run
in sequence); `e2e` brings up the whole stack and runs the suite; `token`
targets `pharmacy-api`.

### 3.5 CI

- `test.yml`: a matrix over `service: [pharmacy-api, erx-gateway]` for the
  `test` and `lint` jobs -- build that service's image, run pytest with the
  junit summary, ruff check/format, `makemigrations --check`, and
  `spectacular --fail-on-warn` (both services serve a schema). The PR
  comment names the service. A third matrix entry, `pharmacy-auth`, runs
  the shared package's tests and ruff in the pharmacy-api image (it is
  installed there).
- `e2e.yml`: `docker compose up -d --build pharmacy-api erx-gateway
  erx-gateway-worker vendor-sim`, wait on both health endpoints, run
  `./e2e/run.sh` (both services' cases, §8.3), upload the report, dump
  both services' logs on failure.

No path filtering: both services run on every push, which keeps the matrix
honest while the gateway is young.

### 3.6 Acceptance for Stage 1

The moved pharmacy-api passes its 554 tests and the 745 e2e assertions
unchanged; `docker compose up` starts `db`, both migrate steps and
`pharmacy-api` exactly as before; the gateway member exists as a skeleton
(health endpoint, settings, migrations table) so the workspace, Dockerfile
and CI matrix are exercised before the gateway has any logic.

---

## 4. Stage 2 -- pharmacy-api

### 4.1 New scopes

| Scope | Granted to | Purpose |
|---|---|---|
| `erx:intake` | the `erx-gateway` M2M role only | `POST /erx/intakes/` and `GET /erx/intakes/{id}/` |
| `erx:read` | `admin`, `pharmacist`, `certified_technician`, `technician`, `intern` | list and read intakes (the review queue) |
| `erx:review` | `admin`, `pharmacist` | resolve a `needs_review` intake, trigger or expire a response (§4.3.5) |

Twenty-five scopes. `ROLE_SCOPES` grows accordingly; the seed tops roles up
as it already does; `test-users.md`'s scope table is updated.

### 4.2 A service principal

#### 4.2.0 The shared package

`packages/pharmacy-auth` (import name `pharmacy_auth`) takes what
`pharmacy/auth/` has that knows nothing about staff: `claims.py`,
`providers/{base,oidc,logto}.py`, `verifier.py`, `principal.py`
(`Principal` without the `staff` field -- a service keeps a plain
principal; pharmacy-api subclasses it to add `staff`), `permissions.py`
(`HasScope`), `schema.py` (`BearerScheme`), the dev-key signing that
`mint_token` uses, plus two new modules: `service.py` (`is_service_token`,
§4.2.1) and `client.py` (a client-credentials token client with caching
and the dev-signed fallback, §4.2.3). Settings it reads are passed in by
the host app (`AUTH_ISSUER`, `AUTH_INTERNAL_URL`, the audience, the dev
key), never imported from a project. `pharmacy/auth/` keeps
`authentication.py` (the `Staff` resolution) and `scopes.py` (the scope
names are pharmacy-api's vocabulary; the gateway has its own three). The
existing `test_providers.py`, the verifier and principal tests move with
the code; `test_auth.py` stays.

#### 4.2.1 Recognizing one

A verified token is a **service token** when its `sub` equals its
`client_id` -- what RFC 9068 §2.2 says a client-credentials token carries
and what Logto emits (§2.2). The adapter may override
`is_service_token(raw)` for a provider that marks it differently. For a
service token, `BearerTokenAuthentication`:

- does **not** require `email`, and does **not** call
  `services.staff.identify`;
- builds `Principal(external_user_id=sub, client_id=sub, scopes=...,
  staff=None)` with `kind = "service"` (a new field; `"staff"` and
  `"login"` for the other two);
- throttles per client id (`pk` is already the subject).

`HasScope` is unchanged: a service holds the scopes its role grants and
nothing else. The two writes that name a person (starting and verifying a
fill) already refuse a principal without `staff` (403
`staff_identity_required`); a service token hits the same refusal.

No client allow-list in the API: the provider's M2M role is the authority
on what a client may do, as the user roles are for people.

#### 4.2.2 Audit attribution

`FieldChange.changed_by_service` (`CharField(255)`, blank): the client id.
The exactly-one CHECK becomes exactly one of `changed_by_user`,
`changed_by_staff`, `changed_by_service`. `WriteContext.service` and
`history.record` set it. Migration `0012_service_actor`. The
`ErxIntake` row (§4.3.2) is what ties the change rows back to the message.

#### 4.2.3 At the identity provider, and locally

`seed_auth` (and `docs/identity-providers/logto.md` for other
environments) adds:

1. A second **API resource** for the gateway, indicator
   `ERX_AUTH_AUDIENCE` (`https://erx-gateway.open-pharmacy.local` locally),
   TTL 900 s, with the scopes `erx:messages:read` and `erx:messages:retry`.
2. Two **M2M applications** and **M2M roles**: `erx-gateway` (role grants
   `erx:intake` on the pharmacy-api resource) and `pharmacy-api` (role
   grants the two gateway scopes on the gateway resource).
3. A **check** of both: a client-credentials token per application,
   obtained with the secret Logto generated (readable through the
   Management API), verified through the adapter, scopes compared -- the
   same "a token the API would refuse fails the seed" rule the test users
   have. In a deployed tier the client id and secret of each application
   go into the other service's environment (`AUTH_CLIENT_ID`,
   `AUTH_CLIENT_SECRET`).

Locally the services never hold those secrets. In every local mode --
with or without the `auth` profile -- and in CI, each service signs its
own service token with `AUTH_DEV_SIGNING_KEY` (`sub = client_id =
"erx-gateway-local"` / `"pharmacy-api-local"`, kid = the dev kid), and the
other service's verifier accepts it only while `local.py` sets the key:
the `mint_token` rule, unchanged. So there is nothing to restart and no
file to hand around, and the real grant is still proven by the seed on
every boot of the `auth` profile. `pharmacy_auth.client` picks the mode
from whether `AUTH_CLIENT_SECRET` is set. `mint_token --service
<client-id> --scopes ...` is added for the e2e suite and for curl.

### 4.3 The intake endpoint

#### 4.3.1 Contract

`POST /api/v1/erx/intakes/` (scope `erx:intake`):

```json
{"message_id": "<SCRIPT MessageID>", "pharmacy_ncpdp_id": "4417829", "pharmacy_npi": "1938756214"}
```

`pharmacy_ncpdp_id` is the header's `To` and always present;
`pharmacy_npi` is sent when the message's optional `Pharmacy` block is
there. Response: the intake record.

```json
{
  "id": "…", "message_id": "…", "message_type": "NewRx",
  "status": "completed",
  "outcome": {"prescription_id": "…", "patient": "created", "prescriber": "matched", "medication": "created"},
  "review": null,
  "error": null,
  "created_at": "…", "updated_at": "…"
}
```

| Status | Meaning |
|---|---|
| `processing` | a request holds the lease (§4.3.3); a concurrent retry gets this back with 200 and tries again later |
| `completed` | done; `outcome` names what was made or matched (`NewRx`), or `approved` / `denied` with a reason code (`CancelRx`) |
| `needs_review` | ingested as far as it safely could be; a person decides (§4.3.5); `review.kind`, `review.deadline_at` say what and by when |
| `failed` | permanent; `error.code` says why; the gateway reports it to the vendor (§7) |

HTTP: 201 when this request created the intake, 200 on a replay (the
record already existed, whatever its status). A replay never re-runs a
completed, failed or reviewed intake. `GET /erx/intakes/{id}/` and `GET
/erx/intakes/?status=&message_type=` (scopes as §4.1) read it; the list is
the review queue.

#### 4.3.2 `ErxIntake`

A new model, repository, service, serializer, view and filterset (the
usual five, `tests/test_layering.py` enforces it): `message_id`
(`CharField(35)` -- SCRIPT's MessageID length; unique among live rows),
`message_type`, `pharmacy_location` FK, `status`, `attempts`,
`lease_expires_at`, `document` (`JSONField`, the canonical document as
fetched -- kept so review and re-processing never depend on the gateway
being up), `outcome` (`JSONField`), `error` (`JSONField`, `{code,
message, details}` in the API's own envelope shape), `review_kind`,
`review_deadline_at`, `reviewed_by`/`reviewed_at`/`review_note`, and
nullable FKs `pharmacy_location` (null while `unknown_pharmacy`),
`patient`, `prescriber`, `medication`, `prescription`. Written only by
`services.erx_intake`.

#### 4.3.3 Processing, and why it is idempotent

1. **Claim.** In its own short transaction: insert the intake row with
   `status=processing` and a lease (`now + 60 s`). A unique-violation
   means the row exists: re-read it; if it is terminal, return it (200);
   if it is `processing` with a live lease, return it (200, the caller
   retries later); if its lease has expired (the previous worker died
   mid-way), take the lease over and continue.
2. **Fetch.** `GET {ERX_GATEWAY_URL}/api/v1/messages/{message_id}/` with
   pharmacy-api's own service token (§5.6). Store the document on the row.
   A 404 is permanent (`message_unknown`); a connection error or 5xx is
   transient -- the intake goes back to `received`-equivalent (lease
   cleared, `attempts += 1`) and the response is 503 `gateway_unavailable`,
   which the gateway's task treats as "retry later".
3. **Resolve the pharmacy.** The location by NCPDP ID (always sent),
   cross-checked against the NPI when one is sent. It must be a live
   `PharmacyLocation` of this deployment; anything else parks the intake
   as `needs_review` (`unknown_pharmacy`) with no records made: a
   person routes it to a location (`resolve` with `pharmacy_location_id`,
   which re-runs from here) or rejects it.
4. **Match or create, under natural keys, each in the same transaction:**
   - **Prescriber** by NPI (`uniq_live_npi`): matched -> reuse as is (a
     message never updates an existing record: that is the staff's
     record, and a prescriber's details changing is a different
     workflow); not found -> create from the message. An NPI failing the
     Luhn check is permanent `invalid_prescriber` (the gateway refuses it
     before the acknowledgement; this is the backstop).
   - **Medication** by NDC when the message carries one, else by
     `rxnorm_code`, else by normalized `(name, strength, form)`; create
     otherwise.
   - **Patient**: exact normalized `(last_name, first_name, dob)`
     among live rows, trying `FormerName` as a second key when the current
     name finds nothing; one match -> reuse; none -> create with the
     address the message carries (a new `Address` row); several ->
     `needs_review` (`ambiguous_patient`) with the candidates' ids in
     `outcome`.
   - **Prescription** via `services.prescriptions.create` (so it is
     numbered, audited and routed exactly like one typed in), with
     `intake_source = electronic` and the fields in §4.4. A medication
     without an NDC, a `DEASchedule`, a `DoNotFill`, or a
     compound creates it with status **`needs_review`** (§4.5) and the
     intake ends `needs_review` instead of `completed`
     (`medication_unidentified`, `controlled_substance`, `do_not_fill`,
     `emergency_cover`, `compound_prescription`): the record exists,
     nothing can be filled, and a pharmacist decides.
5. **Complete.** `status=completed`, FKs and `outcome` set, lease cleared.
   Steps 4-5 are one transaction, so a crash leaves no half-made records --
   the next attempt starts again from step 3 and finds nothing to reuse
   except what earlier *successful* intakes made.

A concurrent second request for the same `message_id` cannot create a
second prescription: the claim's unique constraint serializes them, and
the loser returns the winner's row.

#### 4.3.4 `CancelRx`

Same claim and fetch. Resolution, in order: the prescription the original
`NewRx`'s intake made (`RelatesToMessageID` -> `ErxIntake.prescription`);
else `prescriber_order_number` + prescriber NPI; else `RxReferenceNumber`
as our Rx number. Then:

| Found as | Outcome | On the wire (§5.4) |
|---|---|---|
| active / on hold, no fill in flight | `services.prescriptions.cancel` -> `approved` | `Approved`, `RxReferenceNumber` = our Rx number |
| already cancelled | `approved` (idempotent: the prescriber's intent is already true) | `Approved` + `Note` "already cancelled" |
| expired | `approved` (nothing will be dispensed) | `Approved` + `Note` "had expired on <date>" |
| completed, or any fill completed | `denied`, `already_dispensed` | `Denied` + `DenialReason` "dispensed on <date>; <qty> of <authorized> dispensed" |
| fill in flight | **parked**: `needs_review` (`cancel_with_fill_in_flight`) with `review_deadline_at = now + ERX_RESPONSE_DEADLINE`. The pharmacist cancels the fill and approves, or denies; at the deadline it is auto-denied (§4.3.5) | after the decision: `Approved`, or `Denied` + `DenialReason` "a fill was in progress" / the pharmacist's note |
| not found | `denied`, `not_found` | `Denied` + `DenialReason` "no matching prescription" |
| the `NewRx` intake is still `processing` | transient: 409 `intake_in_progress`; the gateway retries, so a cancel that overtakes its own `NewRx` waits for it | -- |

The schema's `ReasonCode` list for a denied cancel (`AA`, `AB`, `AC`,
`AP`, `AR` -- §2.4) describes a *prescriber's* reasons and a transfer;
none fits "already dispensed" or "not found", so those denials carry
`DenialReason` text only, which the schema allows ("must be accompanied by
the ReasonCode or DenialReason"). `AR` is emitted if a later transfer
feature ever applies. `outcome` carries the API's own code and the text.

#### 4.3.5 Review

Every `needs_review` intake carries `review_kind` and, when a response to
the vendor is owed, `review_deadline_at`. The kinds, and what resolving
each means:

| `review_kind` | Made so far | Resolve with (scope `erx:review`) |
|---|---|---|
| `unknown_pharmacy` | nothing | `{"pharmacy_location_id": …}` -> re-run from step 3; or `reject` |
| `ambiguous_patient` | nothing | `{"patient_id": …}` (a candidate, or one made by hand) -> re-run from step 4; or `reject` |
| `quantity_sufficient` | prescriber, medication, patient | `{"quantity": n, "days_supply"?: n}` -> create the prescription; or `reject` |
| `medication_unidentified` (no NDC), `compound_prescription` | everything, prescription `needs_review` | `{"ndc": …}` or `{"medication_id": …}` (the pharmacist identifies the product, or points at an existing medication) -> prescription `active`; or `reject` -> prescription cancelled |
| `controlled_substance`, `emergency_cover` | everything, prescription `needs_review` | `accept` -> prescription `active`; or `reject` -> prescription cancelled |
| `do_not_fill` | everything, prescription `needs_review` | `accept` -> prescription `on_hold` (that is the prescriber's instruction); or `reject` -> cancelled |
| `cancel_with_fill_in_flight` | the pending cancel | `approve` -> `services.prescriptions.cancel` (refused 409 `fill_in_progress` until the pharmacist has cancelled the fill); `deny` with a note; or the deadline passes |

`POST /erx/intakes/{id}/resolve/` takes `{"action": "accept" | "reject" |
"approve" | "deny" | "expire", "note"?, ...fields}`. `reject` sets
`failed` with `error.code = "rejected_on_review"` and the note. `expire`
is what the gateway's sweep calls when `review_deadline_at` has passed
: on a parked cancel it records `denied` with `DenialReason` "no
decision before the deadline"; on anything else it is a no-op (only a cancel
owes the vendor an answer). Every action is idempotent on a resolved
intake (200, unchanged).

The gateway learns the final state by polling `GET /erx/intakes/{id}/`
from its sweep (§5.8) and then sends the `CancelRxResponse`; a pharmacist
who wants the answer out *now* resolves the intake, and the next sweep (or
`POST /api/v1/messages/{id}/retry/` on the gateway, reachable through
pharmacy-api's `POST /erx/intakes/{id}/respond/`) sends it without waiting
for the interval.

### 4.4 Fields and models a `NewRx` / `CancelRx` carries

Checked element by element against the 2017071 schema (§2.4). "keep in
message" means the datum stays in the stored XML and the canonical
document, retrievable and auditable, but is not a column.

#### Header (`Message/Header`)

| SCRIPT | Action |
|---|---|
| `MessageID`, `RelatesToMessageID`, `SentTime`, `To`/`From` (+ `Qualifier`), `SenderSoftware/*`, `TestMessage`, `Security`, `DigitalSignature`, `RequestReferenceNumber`, `ReturnReceipt` | gateway's `ErxMessage` (§5.5); `MessageID` is `ErxIntake.message_id` |
| `PrescriberOrderNumber` | **add** `Prescription.prescriber_order_number` (`CharField(35)`, blank, indexed): the key a `CancelRx` (and later `RxChange`, `RxRenewal`) names |
| `RxReferenceNumber` | our `rx_number`; used for matching a `CancelRx`, and sent back on every response |

#### Patient (`Patient/HumanPatient`)

| SCRIPT | Ours | Action |
|---|---|---|
| `Name/LastName, FirstName` (mandatory, 35), `MiddleName`, `Suffix` (10) | `Patient` names | exists; `suffix` is 20 wide, fine |
| `Name/Prefix`, `FormerName` | -- | keep in message (`FormerName` is "additional matching criteria"; the matcher uses it as a second candidate key, never as a write) |
| `Gender` (`M`, `F`, `U`) | `gender` | M -> `male`, F -> `female`, U -> `unknown`; `other` is never produced from SCRIPT |
| `DateOfBirth/Date` or `/DateTime` | `dob` | exists; a `DateTime` is taken as its date |
| `Address/*` (mandatory in a `NewRx`) | `Address` | exists; `StateProvince`, `PostalCode`, `CountryCode` -> `state`, `postal_code`, `country`; a non-`US` country or a non-mailable US state/ZIP is `needs_review` (`unsupported_address`) |
| `CommunicationNumbers/PrimaryTelephone` (`Number` 10 digits, `Extension`, `SupportsSMS`), the other phone kinds, `ElectronicMail` | `phone_number`, `email`, `sms_opt_in` | **relax** `phone_number` to optional: `CommunicationNumbers` is optional on a patient. `PrimaryTelephone` -> E.164 (`+1` + 10 digits), extension kept in message; `SupportsSMS = Y` does **not** set `sms_opt_in` (consent is granted to us, not relayed) |
| `Identification/*` (EHR MRN, account number, Medicare, Medicaid, `SocialSecurity`, `MutuallyDefined`, `REMSPatientID`) | -- | keep in message; **SSN is redacted from the canonical document** and lives only in the raw XML in storage (a `PatientIdentifier` model is a later plan if matching needs the MRN) |
| `PatientLocation`, `LanguageNameCode`, `SubstanceUse`, `AlternateContact`, `GestationalAge`, `HospiceIndicator` | -- | keep in message |
| `NonHumanPatient` (veterinary) | -- | refused before the acknowledgement (§5.3) |
| `AllergyOrAdverseEvent`, `BenefitsCoordination` (0..4), `Observation`, `Facility` | -- | keep in message; each a later plan (allergies were already deferred) |

#### Prescriber (`Prescriber/NonVeterinarian`, `Supervisor`)

| SCRIPT | Ours | Action |
|---|---|---|
| `Identification/NPI` (mandatory) | `npi` | exists; the natural key; Luhn-checked before the acknowledgement |
| `Identification/DEANumber` | -- | **add** `Prescriber.dea_number` (`CharField(9)`, blank; two letters + seven digits with the DEA check digit) -- needed on any controlled-substance record |
| `Identification/StateLicenseNumber` | -- | **add** `license_number` (35, blank). The schema carries no issuing state for it; the prescriber's address state is *not* assumed to be the issuer, so no `license_state` column -- a later plan if a board lookup needs it |
| `Identification/Data2000WaiverID`, `StateControlSubstanceNumber`, `REMS*`, Medicare/Medicaid/UPIN/HIN/SSN | -- | keep in message (SSN redacted as for patients) |
| `Name/*` | names | exists |
| `Specialty` (Health Care Provider Taxonomy) | `practitioner_type` | **add** `taxonomy_code` (`CharField(10)`, blank); `practitioner_type` derived where the taxonomy family is unambiguous (363L -> NP, 363A -> PA, 1223 -> DDS, 213E -> DPM, 152W -> OD, 367A -> CNM, 367H -> CRNA), else `OTHER` -- MD vs DO cannot be told from a taxonomy and is not guessed |
| `PracticeLocation/BusinessName`, `/Identification` | -- | **add** `practice_name` (`CharField(70)`, blank); the identification kept in message |
| `Address/*` (mandatory) | `address` | exists |
| `CommunicationNumbers/PrimaryTelephone` (mandatory), `Fax`, `ElectronicMail` | -- | **add** `phone_number`, `fax_number` (E.164, blank), `email` (blank) -- the pharmacist needs a number to call about a prescription |
| `PrescriberAgent`, `PrescriberPlaceOfService`, `FormerName` | -- | keep in message |
| `Supervisor/*` (the supervising physician of a PA/NP) | -- | keep in message; a `Prescription.supervising_prescriber` FK is a later plan |
| `Veterinarian` | -- | refused before the acknowledgement (§5.3) |

#### Pharmacy (`Pharmacy`, optional in a `NewRx`)

`NCPDPID` + `NPI` (both mandatory when the block is present),
`BusinessName`, `Address`, `CommunicationNumbers`: used only to resolve
the home location (§4.3.3); nothing added. The header's `To` is the
key pharmacy-api resolves first.

#### Medication (`MedicationPrescribed`)

| SCRIPT | Ours | Action |
|---|---|---|
| `DrugDescription` (mandatory, 105) | `name` | exists; `name` is 255 wide |
| `DrugCoded/ProductCode` with `Qualifier = ND` | `ndc` | **relax** `ndc` to optional, unique among live rows when set: `DrugCoded` and `ProductCode` are both optional, and "must not be sent" for a compound. A medication without an NDC makes the prescription `needs_review` (`medication_unidentified`, §4.5) until a pharmacist supplies one. Other qualifiers (`UP`, `DI`, `UN`, ...) are kept in message and do not populate `ndc` |
| `DrugCoded/DrugDBCode` with an RxNorm qualifier (`SCD`, `SBD`, `GPK`, `BPK`) | -- | **add** `rxnorm_code` (`CharField(12)`, blank, indexed) and `rxnorm_qualifier` (3); a non-RxNorm drug-database code is kept in message |
| `DrugCoded/Strength/StrengthValue` (free text, 70) + `StrengthUnitOfMeasure` | `strength` | exists; `StrengthValue` as sent (the example carries `"75 MG"` in the value itself), the unit appended only when the value has no unit |
| `DrugCoded/Strength/StrengthForm` (NCI code) | `form` | mapped to `MedicationForm` for the common NCI codes (tablet, capsule, solution/suspension -> `liquid`, injection, cream/ointment/gel -> `topical`), else `other`; **add** `form_code` (`CharField(16)`, blank) to keep the code |
| `DrugCoded/DEASchedule` (NCI code; "required if a controlled substance") | -- | **add** `dea_schedule` (choices `CII`..`CV`, blank = not controlled), mapped from the NCI code. Any value -> the prescription is created `needs_review` and so is the intake (`controlled_substance`), because this pharmacy does not dispense controlled substances |
| `CompoundInformation/*` | -- | the medication row is made with the description, no NDC, `form` from `FinalCompoundPharmaceuticalDosageForm`; the prescription is `needs_review` (`compound_prescription`) and a pharmacist confirms |

#### Prescription (`MedicationPrescribed`, continued)

| SCRIPT | Ours | Action |
|---|---|---|
| `Quantity/Value` (mandatory; decimals legal) | `quantity` | **widen** to a decimal (§4.5); mapped as sent |
| `Quantity/CodeListQualifier` | -- | must be `38` (original quantity); `QS` (quantity sufficient) -> `needs_review` (`quantity_sufficient`) with no prescription yet, since the pharmacist sets the quantity; any other value is refused before the acknowledgement as content invalid for a `NewRx` |
| `Quantity/QuantityUnitOfMeasure/Code` (NCI) | -- | **add** `quantity_unit` (`CharField(16)`, blank) |
| `DaysSupply` | `Fill.days_supply` only | **add** `Prescription.days_supply` (nullable): what was prescribed, distinct from what each fill dispensed |
| `Sig/SigText` (mandatory, 1000) + structured sig | `instructions` | exists; the structured sig is kept in message |
| (no SCRIPT element) | `dose_amount` (required) | **relax** to optional; filled from the structured sig's dose when one is present |
| `NumberOfRefills` (mandatory) | `fills_authorized` | `refills + 1` |
| `WrittenDate/Date` or `/DateTime` (mandatory) | `written_at` | exists; a written date after `SentTime` is refused before the acknowledgement (the schema says it must not be) |
| `OtherMedicationDate` with `Qualifier = ExpirationDate` | `expires_at` | set when present; otherwise null -- state-law defaults are a later setting, not guessed here |
| `OtherMedicationDate` with `Qualifier = EffectiveDate` (or `StartDate`) | -- | **add** `fill_not_before` (`DateField`, nullable); the fill service refuses to start a fill before it (409 `not_yet_fillable`) |
| `Substitutions` (mandatory: `0` / `1`) | -- | **add** `substitution_allowed` (`BooleanField`, default True; `1` -> False) -- "dispense as written" changes what may be dispensed |
| `DoNotFill` (`Y`, `H`, `E`) | `status`, `notes` | `Y` and `H` -> the prescription is created **`needs_review`** with the instruction in `notes` and the intake is `needs_review` (`do_not_fill`); a pharmacist's `accept` moves it to `on_hold` (the prescriber's instruction). `E` (cover for an emergency oral order) -> `needs_review` (`emergency_cover`), since the emergency dispensing already happened and a pharmacist must reconcile it |
| `Note` (210) | `notes` | exists |
| `Diagnosis/Primary`, `Secondary` (`Code`, `Qualifier` ABF/DX/LD, `Description`) | -- | **add** `diagnoses` (`JSONField`, a list of `{code, qualifier, description, rank}`); a `PrescriptionDiagnosis` model is not worth a five-module resource for two codes |
| `PriorAuthorization` (35), `PriorAuthorizationStatus` | -- | **add** `prior_authorization` (`CharField(35)`, blank); the status kept in message |
| `RxFillIndicator` | -- | **add** `rx_fill_indicator` (`CharField(64)`, blank): what the prescriber wants told about fills, kept for the `RxFill` plan |
| `LastFillDate`, `PrescriberCheckedREMS`, `REMS*`, `SplitScript`, `DeliveryRequest`, `DeliveryLocation`, `TimeZone`, `OrderCaptureMethod`, `DrugCoverageStatusCode`, `DrugUseEvaluation`, `ProhibitRenewalRequest`, `FlavoringRequested`, `NumberOfPackagesToBeDispensed`, the LTC/IV/wound/titration tail | -- | keep in message |

#### New models

- `ErxIntake` (§4.3.2) -- pharmacy-api.
- No others in pharmacy-api. The candidates deliberately not created
  yet, each a plan of its own when wanted: `PatientIdentifier`
  (external ids), `PatientCoverage` (insurance from
  `BenefitsCoordination`), `Allergy`, `Observation`, and a prescription's
  `supervising_prescriber`.

Migrations: `0012_service_actor` (§4.2.2), `0013_erx_fields` (the adds,
the relaxations, the new status, the decimal quantities), `0014_erx_intake`.
The relaxations keep every existing row valid (a required field becoming
optional needs no data migration; an integer column widening to a decimal
converts in place). Serializers expose the new fields; `docs/api.md`,
`prescriptions.md` and `errors.md` gain the codes above.

### 4.5 Two changes to the prescription itself

**A `needs_review` prescription status.** A prescription that arrived
electronically but that a pharmacist has not yet cleared. It is the
parking state for every reviewed intake that has produced a record
(§4.3.5), so `on_hold` keeps its current meaning (a pause of an active
prescription) and nothing parked can be filled. Transitions:

| From | To | How |
|---|---|---|
| `needs_review` | `active` | the intake's `resolve/` (`accept`, or supplying the NDC / medication) |
| `needs_review` | `on_hold` | `resolve/` `accept` on a `do_not_fill` intake |
| `needs_review` | `cancelled` | `resolve/` `reject`, or `POST {id}/cancel/` |

It is never set through `PATCH` (400, like the terminal statuses), never
entered from another status, and a fill cannot be started on it (409
`not_yet_fillable`, the code `fill_not_before` also uses). Only intake
creates a prescription in it; a prescription typed in at the counter still
starts `active`. `expire_if_due` ignores it (a lapsed review stays a
review). `ALLOWED_TRANSITIONS`, the serializer's status validation, the
fill service, the filters and `docs/prescriptions.md` all change.

**Fractional quantities.** `Prescription.quantity` and
`Fill.quantity_dispensed` become `DecimalField(max_digits=11,
decimal_places=3)` (SCRIPT's `n1..11`; three places covers the units
pharmacies dispense -- mL, grams, insulin units), with `> 0` replacing
`>= 1` in the CHECK constraints and validators. The derived totals
(`quantity_authorized`, `quantity_dispensed`, `quantity_in_progress`,
`quantity_remaining`) are decimals; `fills_used`/`fills_remaining` already
are. Serializers render them with `coerce_to_string=False` like
`fills_used`, so a client that sent `30` reads back `30.0` -- the existing
e2e assertions compare numerically and hold. `PositiveIntegerField` goes
away from both models; the admin and the factories follow. This is the
one change in the plan that touches existing behaviour on every
prescription and fill, so it is its own commit in Stage 2b with the full
suite as its gate.

---

## 5. Stage 3 -- the erx-gateway

### 5.1 Shape

A second Django project (same stack as pharmacy-api -- DRF, drf-spectacular,
the settings split, the JSON logging, the error envelope -- so the two read
alike and the conventions in `AGENTS.md` hold for both), with one app,
`erx`:

```text
services/erx-gateway/
  config/settings/{base,local,uat,prod}.py   config/urls.py  config/wsgi.py
  erx/
    models/        message.py (ErxMessage), delivery.py (OutboundDelivery)
    repositories/  services/  serializers/  views/  admin/   -- the same layering, enforced by the same test
    script/        validate.py  (lxml XMLSchema over standards/ncpdp/script/schema/2017071/, compiled once per process)
                   parse.py     (lxml -> canonical document, §5.6)
                   build.py     (Status, Error, Verify, CancelRxResponse)
                   codes.py     (the ECL values used: Status, Error, DescriptionCode, ReasonCode3, qualifiers)
    storage.py     (object storage: put / get / key layout)
    auth/          (inbound service-token verification; vendor authentication)
    clients/       pharmacy_api.py  vendor.py  tokens.py
    tasks.py       (process_message, deliver, sweep)
    management/commands/  ensure_bucket  vendor_sim  mint_token
  standards/ncpdp/script/schema/2017071/   the six XSD files, committed
  tests/
```

### 5.2 Authentication, both directions

- **Vendor -> gateway** (`POST /script/`): Basic credentials.
  `ERX_VENDOR_CREDENTIALS` holds one or more `user:bcrypt-hash` pairs,
  compared in constant time; TLS and any IP restriction are the proxy's
  job. The `From` header's value is recorded as the sender, the Basic
  user as the credential; a mismatch between the two is logged, not
  refused, since the standard does not tie them.
- **pharmacy-api -> gateway** (`GET /api/v1/messages/{id}/`): a bearer
  service token for the gateway's audience with `erx:messages:read`,
  verified by `pharmacy_auth`'s `TokenVerifier` and `HasScope` from the
  shared package -- discovery/JWKS through
  `AUTH_ISSUER`/`AUTH_INTERNAL_URL`, `ERX_AUTH_AUDIENCE`, the dev key
  locally. No `Staff`, no email: a gateway principal is a client id and
  scopes; the gateway's authentication class is the package's
  `ServiceTokenAuthentication`, which refuses a user token (a person never
  calls the gateway).
- **gateway -> pharmacy-api**: `pharmacy_auth.client` obtains a token by
  client credentials (or dev-signs one locally), caches it until 60 s
  before expiry (in Redis when configured, else in process), and refreshes
  on a 401. pharmacy-api uses the same client to call the gateway.
- **gateway -> vendor**: Basic credentials, `ERX_VENDOR_OUTBOUND_URL`
  and `ERX_VENDOR_OUTBOUND_CREDENTIALS` (`user:password`, a secret).

### 5.3 Receiving a message

`POST /script/`, `Content-Type: application/xml` or `text/xml`, body <= 1
MiB (413 above). The principle from §2.4 -- a `Status` is "acceptance and
responsibility", and nothing in the standard lets a pharmacy take a `NewRx`
back afterwards -- means **everything the gateway can judge without
pharmacy-api is judged before the acknowledgement**, so the vendor's retry
or the prescriber's correction is the recovery for it. In order:

| Step | Refusal, as a SCRIPT `Error` (§5.4) |
|---|---|
| 1. Authenticate the vendor | HTTP 401; `Error 900` |
| 2. Parse with lxml (DTDs, entity expansion and network access disabled) | not well-formed: `900` + `500` "XML syntax error" + the parser message with line and column |
| 3. Validate against `transport.xsd` | `900` + `3000` + the first fault's XPath and reason |
| 4. Identify `Body`'s child | not `NewRx`/`CancelRx`: `900` + `4040` "receiver does not support this transaction type" |
| 5. Check the version attributes are `20170715` and `TestMessage` | other version: `900` + `3000` |
| 6. Content checks the schema cannot express (`2000`, each with the element's XPath): `HumanPatient` not `NonHumanPatient`; `NonVeterinarian` not `Veterinarian`; prescriber `NPI` passes Luhn; `ProductCode/Code` with `ND` is a well-formed NDC; `Quantity/CodeListQualifier` is `38` or `QS`; `WrittenDate` <= `SentTime`; for a `CancelRx`, at least one of `RxReferenceNumber`, `PrescriberOrderNumber`, `RelatesToMessageID` | `900` + `2000` + XPath + a one-line description |
| 7. Duplicate check on `(sender, MessageID)`: same id and same content hash -> **return the stored acknowledgement unchanged** (the vendor's retry of a lost ack); same id, different content | `900` + `220` "transaction is a duplicate" |
| 8. **Persist, then acknowledge** (§5.5): the raw bytes to object storage, then in one database transaction the `ErxMessage` row and the `process_message` task; only when that commits is the `Status` built, stored on the row, and returned | storage or database unavailable: **`Error 600` "communication problem -- try again later" + `210` "unable to process, please resubmit"**, and HTTP 503 alongside. Nothing is stored, so the retry is a first receipt |

The recipient is **not** checked here: a message addressed to a
pharmacy this deployment does not serve is acknowledged and parked by
intake for a person to route or reject, so the gateway holds no copy of
the pharmacy's identifiers and the acknowledgement never depends on
pharmacy-api being up.

What else is **not** checked here, because it needs the pharmacy's
database: whether the patient exists or is ambiguous, whether the
medication is known, whether the Rx counter is seeded, whether a
`CancelRx` finds its prescription, whether the drug is controlled. Those
become intake outcomes (§4.3) after the acknowledgement.

A `TestMessage` is validated, stored and acknowledged, never processed.

### 5.4 Acknowledgements and responses

Built by `script/build.py` from a template per message type, with our own
`MessageID` (UUID-derived, <= 35 chars), `RelatesToMessageID` = the inbound
id (mandatory on every response, §2.4), `SentTime` in UTC, `To`/`From`
swapped with their qualifiers, the six `20170715` version attributes, and
our `SenderSoftware`:

- `Status` -- synchronous, on a persisted `NewRx`/`CancelRx`: `Code 010`
  ("successful -- accepted by ultimate receiver"; `000` is the
  intermediary's code), `Description` "Accepted" (70 chars max). The
  vendor reads this as acceptance and responsibility, which is exactly
  when it is sent.
- `Error` -- synchronous, for §5.3's refusals: `Code` `900` for a rejection
  the vendor must not retry unchanged, `600` for "try again later";
  `DescriptionCode`(s) from §5.3's table; `Description` free text with the
  XPath when the code calls for one.
- `Verify` -- asynchronous, only when the request carried `ReturnReceipt`:
  `VerifyStatus/Code 010`, sent once the message is `completed`.
- `CancelRxResponse` -- asynchronous, after a `CancelRx` intake resolves:
  `Response/Approved` (`ReferenceNumber` = the request's
  `RequestReferenceNumber` when given, `Note`) or `Response/Denied`
  (`ReasonCode` only when one of the five ECL values applies,
  `DenialReason` text otherwise -- §4.3.4), with the header's
  `RxReferenceNumber` = our Rx number and `PrescriberOrderNumber`
  echoed. No patient, prescriber or medication blocks: the schema does not
  allow them in this transaction.

Every outbound message is validated against the XSD before it is sent (a
response we would reject is a bug, not a delivery).

### 5.5 Storage and the message record

**Object storage**: one private bucket (`ERX_BUCKET`), server-side
encryption requested on every put, keys
`inbound/{yyyy}/{mm}/{dd}/{message_id}.xml` and
`outbound/{yyyy}/{mm}/{dd}/{message_id}.xml`, the SHA-256 of the bytes in
object metadata and on the row. `boto3` with `endpoint_url` from
`ERX_S3_ENDPOINT_URL` (LocalStack locally, unset for AWS). `ensure_bucket`
is the gateway's migrate-time step: create the bucket if missing, set the
public-access block -- idempotent, never at server start. Retention is a
bucket lifecycle policy, not code; nothing here deletes.

**`ErxMessage`** (the gateway's database): `message_id`, `sender`,
`direction` (inbound/outbound), `message_type`, `related_to_message_id`,
`sent_time`, `received_at`, `content_sha256`, `storage_key`, `document`
(the canonical JSON, §5.6), `status` (§5.7), `attempts`,
`next_attempt_at`, `last_error`, `acknowledgement_key` (where the
`Status`/`Error` we returned is stored), `intake_id` and
`intake_status` (what pharmacy-api last said), `is_test`. Partial unique on
live `(sender, message_id, direction)`.

**`OutboundDelivery`**: one row per message we must send to the vendor --
`message` FK, `kind` (`cancel_rx_response`, `verify`), `status`
(`pending`, `delivered`, `failed`, `abandoned`), `attempts`,
`next_attempt_at`, `deadline_at`, `vendor_response_key` (their
`Status`/`Error` to us, stored too).

Logs never contain a message body, a patient name, or a date of birth; they
carry `message_id`, type, status transitions and error codes. The admin
shows rows, not documents; the document endpoint is service-token only.

### 5.6 The canonical document

`GET /api/v1/messages/{message_id}/` (scope `erx:messages:read`) returns:

```json
{
  "message_id": "…", "message_type": "NewRx", "sent_time": "…", "sender": "…",
  "relates_to_message_id": null, "prescriber_order_number": "…", "rx_reference_number": null,
  "is_test": false,
  "return_receipt_requested": false, "request_reference_number": null,
  "patient":   {"last_name": "…", "first_name": "…", "middle_name": "", "suffix": "", "prefix": "", "gender": "F",
                "dob": "1976-10-24", "former_name": null, "address": {…},
                "phones": [{"kind": "primary", "number": "…", "extension": "", "supports_sms": null}],
                "email": "", "identifiers": [{"kind": "mrn_ehr", "value": "…"}]},
  "prescriber": {"npi": "…", "dea_number": "", "state_license_number": "", "taxonomy_code": "",
                 "name": {…}, "practice": {"name": "", "identifiers": […]}, "address": {…}, "phones": […],
                 "fax": "", "email": "", "agent": null, "supervisor": null},
  "pharmacy":  {"ncpdp_id": "…", "npi": "…", "name": "…"},
  "medication": {"description": "…", "ndc": "…", "rxnorm": {"code": "…", "qualifier": "…"},
                 "strength": {"value": "…", "unit": "…"}, "form_code": "…", "dea_schedule": "",
                 "is_compound": false, "compound": null},
  "order": {"quantity": {"value": "30", "unit_code": "…", "qualifier": "…"}, "days_supply": 30,
            "sig_text": "…", "structured_sig": {…}, "refills": 2, "written_date": "…",
            "substitution_allowed": true, "note": "…", "diagnoses": […], "prior_authorization": "",
            "do_not_fill": null, "effective_date": null, "expiration_date": null, "rx_fill_indicator": "",
            "extra": {…}},
  "raw": {"storage_key": "…", "sha256": "…"}
}
```

`extra` holds every element the parser recognised but the domain does not
keep, as a faithful JSON rendering of the XML subtree; `SocialSecurity`
is redacted from the document (it stays only in the stored XML). The raw
XML is one more call (`?format=xml` on the same path) for a pharmacist who
needs the source. The shape is versioned (`"document_version":
1`) so pharmacy-api can refuse one it does not understand.

### 5.7 The message lifecycle

```text
received ──► processing ──► completed          (Verify sent if ReturnReceipt was asked; CancelRxResponse sent)
                  │              │
                  │              └─► needs_review ──► completed | failed   (pharmacy-api's review, §4.3.5)
                  ├─► failed  (permanent, after the acknowledgement: visible for a person, §7.3)
                  └─► stuck   (retries exhausted on a transient failure; alert, manual retry)
```

Transitions are made by `services.messages`, each under the row's lock, so
two workers cannot both advance a message.

### 5.8 Tasks

`process_message(message_id)` -- enqueued in the receipt transaction:

1. Load and lock the row; if not `received`/`processing`, return (an
   enqueued duplicate is harmless).
2. `POST` intake to pharmacy-api with `{message_id, pharmacy_ncpdp_id,
   pharmacy_npi}`.
3. On `completed` / `failed` / `needs_review`: record, then for a
   `CancelRx` create an `OutboundDelivery` (`cancel_rx_response`); for
   either type, a `verify` delivery once `completed` if the request
   carried `ReturnReceipt`.
4. On `processing` (lease held), 409 `intake_in_progress`, 503
   `gateway_unavailable`, a connection error, a timeout, any 5xx, or a
   token that cannot be obtained: transient -- `attempts += 1`, re-enqueue
   itself with `run_after = now + backoff(attempts)` (30 s, 1 m, 2 m, 5 m,
   15 m, 30 m, then hourly), until `ERX_MAX_PROCESS_AGE` (default 48 h),
   then `stuck`.
5. On 401/403 from pharmacy-api: a configuration fault, not a data fault --
   treated as transient with the slowest backoff, and logged at ERROR so it
   is seen.

`deliver(delivery_id)` -- the outbound side: build, validate, store, `POST`
to the vendor with Basic credentials; their `Status` -> `delivered`; their
`Error` -> `failed` (logged, manual); transport failure -> retry with the
same backoff until `deadline_at` (`created_at + ERX_RESPONSE_DEADLINE`),
then `abandoned` and an ERROR log.

`sweep()` -- self-rescheduling every `ERX_SWEEP_INTERVAL` (5 min): finds
rows `received`/`processing` whose task is missing or whose
`next_attempt_at` has passed without a run (a worker killed mid-task), and
enqueues them; polls every message in `needs_review` -- one resolved in
pharmacy-api becomes `completed`/`failed` here and gets its delivery, one
whose `review.deadline_at` has passed gets pharmacy-api's `expire` action
called on it, which turns a parked cancel into a denial the next
poll delivers; counts `stuck`/`abandoned` rows and logs them. It is the
backstop that makes the design safe against a lost task, not the primary
mechanism. A pharmacist who has just resolved an intake need not wait for
the interval: `retry/` (§5.9) runs the same poll-and-deliver for one
message immediately.

Tasks are idempotent by construction: every step re-reads state under the
lock and does nothing if it has already been done. The worker is
`erx-gateway-worker` (`manage.py db_worker`), one replica locally, N in
deployment (`SKIP LOCKED` shares the queue).

### 5.9 Operations endpoints

`GET /api/v1/messages/?status=&message_type=&since=` (scope
`erx:messages:read`; also the admin) and `POST
/api/v1/messages/{message_id}/retry/` (scope `erx:messages:retry`, held by
the pharmacy-api M2M role -- `POST /erx/intakes/{id}/respond/` on
pharmacy-api, scope `erx:review`, calls it, which is how a pharmacist
triggers a response now rather than at the next sweep -- and by an ops
M2M client). `GET
/api/v1/health/` reports database, storage (a HEAD on the bucket) and the
queue (the oldest pending task's age).

### 5.10 Settings (gateway)

`DATABASE_URL`, `SECRET_KEY`, `ALLOWED_HOSTS`, `DEBUG`; `AUTH_ISSUER`,
`AUTH_INTERNAL_URL`, `ERX_AUTH_AUDIENCE` (tokens we verify),
`AUTH_CLIENT_ID`/`AUTH_CLIENT_SECRET` (deployed tiers only), `PHARMACY_API_URL`,
`PHARMACY_API_AUDIENCE` (tokens we obtain), `AUTH_DEV_SIGNING_KEY` (local
only; when set and no client secret is, service tokens are dev-signed); `ERX_VENDOR_CREDENTIALS`, `ERX_VENDOR_OUTBOUND_URL`,
`ERX_VENDOR_OUTBOUND_CREDENTIALS`; `ERX_BUCKET`, `ERX_S3_ENDPOINT_URL`,
`AWS_*`; `REDIS_URL`; `ERX_MAX_PROCESS_AGE` (48 h), `ERX_SWEEP_INTERVAL`
(5 min), `ERX_RESPONSE_DEADLINE` (48 h: how long a response may stay
owed, and how long a parked cancel waits for a pharmacist). pharmacy-api
gains `ERX_GATEWAY_URL`, `ERX_GATEWAY_AUDIENCE`, `AUTH_CLIENT_ID`,
`AUTH_CLIENT_SECRET`, and `ERX_REVIEW_DEADLINE` (the same 48 h, set on a
parked cancel). Same rules as pharmacy-api: `.env.example` is the
contract, `.env.local` the throwaways, uat/prod refuse to start with
anything missing.

---

## 6. Research and recommendations

### 6.1 Object storage simulation

| | LocalStack (community) | MinIO | moto server | Garage / RustFS / SeaweedFS |
|---|---|---|---|---|
| License | Apache-2.0 | AGPL-3.0 | Apache-2.0 | AGPL / Apache / Apache |
| S3 fidelity | highest: the community edition implements the S3 API thoroughly (SSE flags, lifecycle, public-access block, presigned URLs, versioning), and the same boto3 code with `endpoint_url` runs against it and AWS | high; some AWS-specific semantics differ (KMS, public-access block) | good for mainstream calls, thinner on edge cases; it is the library the unit tests use anyway | good object stores, but S3 compatibility is a secondary goal and the images are less common |
| Footprint / boot | ~1 GB image, 5-10 s | ~100 MB, ~1 s | ~200 MB, ~2 s | small |
| Persistence | ephemeral in community (persistence is a Pro feature) -- fine locally, `ensure_bucket` runs every `up` | persistent volume | in-memory | persistent |
| Project health | active, widely used as *the* AWS simulator | in 2025 MinIO moved its community edition to source-only / maintenance and stopped publishing Docker images; a poor bet for a new dependency | active (boto ecosystem) | active |

**Recommendation: LocalStack in Compose and CI; moto's `mock_aws` in
process for the gateway's unit tests** (no container, milliseconds, and the
same `storage.py` code path). The simulation exists to prove the
integration against S3 semantics, so fidelity outranks footprint; the
ephemerality is harmless because the bucket is recreated at migrate time
and the e2e suite generates its own data per run.

### 6.2 Task queue

What the gateway needs from a queue: (a) work that is enqueued
**atomically with the message row** -- a message acknowledged to the vendor
must never exist without its task; (b) retries with backoff over hours, not
seconds; (c) a small periodic sweep; (d) at-least-once delivery with
idempotent tasks; (e) hundreds of messages a day, not thousands a second.

| | `django.tasks` + `django-tasks-db` | Celery (+ Redis broker) |
|---|---|---|
| Enqueue atomic with the write | **yes**: the task row is an INSERT in the caller's transaction (verified, §2.3) -- the transactional outbox for free | no: publish happens in `transaction.on_commit`; a crash between commit and publish loses the task, so a sweeper is required anyway |
| Retries / backoff | not built in; a task re-enqueues itself with `run_after` (a dozen lines, and the policy is ours) | built in (`autoretry_for`, exponential backoff, `max_retries`) |
| Scheduling | none; a self-rescheduling task or a loop command | Celery beat (another process) |
| Infrastructure | Postgres, already there; one `db_worker` process | broker + result backend (Redis), worker, optionally beat and flower |
| Semantics | at-least-once via `SKIP LOCKED`; a task whose worker dies stays `RUNNING` until the sweep re-enqueues it | at-least-once with `acks_late`; Redis visibility-timeout pitfalls at long runtimes; more knobs to get wrong |
| Throughput ceiling | a few hundred tasks/s per Postgres -- orders of magnitude above need | very high |
| Maturity / surface | young (the DEP landed in Django 6.0; the DB backend is 0.13) but small, typed, BSD, same author as the Django API; the API is Django's, so task code is portable across backends | 15 years, huge; correspondingly large configuration surface and failure modes to learn |
| Observability | rows in our own database; admin pages for free | flower, or events -> external |

**Recommendation: `django.tasks` with `django-tasks-db`.** Property (a) is
the one that matters most for a gateway whose acknowledgement is a
promise, and only the database-backed queue gives it without a second
mechanism. The features Celery has that it lacks (retry policy, sweep) are
a few dozen lines here and are specified in §5.8. If volume or fan-out ever
exceeds what Postgres should carry, the task functions are written to
Django's API and the swap is a backend plus the retry helper. Risks to
name: `django-tasks-db` 0.13 targets Django 5.2-6.0 and will be checked
against 6.1 in Stage 3 (fallback: pin Django 6.0 for the gateway, or the
`django-tasks` backport); the library is young, so the gateway wraps
enqueue/retry in one module (`tasks.py`) to keep the surface small.

### 6.3 Redis

Used for two things, neither load-bearing: the gateway's Django cache
(`django-redis`) so DRF's throttle on the vendor endpoint counts across
replicas, and the M2M token cache so N replicas share one token instead of
N. When `REDIS_URL` is unset in local, the cache falls back to `LocMemCache`
(uat/prod require it, like every other setting). Not used as a queue or a
lock: Postgres does both.

---

## 7. Failure modes

The principle: **the synchronous acknowledgement is the contract boundary.**
Before it, a failure is reported to the vendor and the vendor's retry is
the recovery. After it, the message is ours: transient failures are
retried by the gateway, permanent ones are reported back to the vendor
asynchronously where the standard allows and are otherwise put in front of a
person. The vendor is never asked to retry something we have acknowledged.

### 7.1 At receipt (synchronous; the vendor sees the answer)

| Failure | Answer | Who recovers |
|---|---|---|
| Vendor credentials wrong | HTTP 401 + `Error 900` | vendor (configuration) |
| Body too large / not XML / not well-formed | `Error 900` + `500` | vendor: do not retry unchanged |
| Unsupported transaction type | `Error 900` + `4040` | vendor |
| Schema-invalid, wrong release | `Error 900` + `3000` with the first fault's XPath | vendor: fix and resend as a new message |
| Content the schema allows but we cannot take (non-human patient, veterinarian, bad NPI/NDC, quantity qualifier, written date in the future, a `CancelRx` with no reference at all) | `Error 900` + `2000` with the XPath | prescriber corrects and resends |
| Duplicate `MessageID`, same content | the original acknowledgement, again | nobody: this *is* the recovery for a lost ack |
| Duplicate `MessageID`, different content | `Error 900` + `220` | vendor |
| Object storage unavailable | `Error 600` + `210`, and HTTP 503; nothing stored | vendor retries; we alert |
| Database unavailable | same as above (the object may be orphaned under a deterministic key; overwritten on retry) | vendor retries; we alert |
| Gateway down / unreachable | no answer | vendor retries (their transport layer) |
| Gateway crashes after commit, before the response is written | vendor gets a transport error and retries; the retry hits the duplicate rule and receives the stored acknowledgement | nobody |

### 7.2 After the acknowledgement, before pharmacy-api completes (transient)

| Failure | Handling |
|---|---|
| Worker down | tasks wait in the database; the queue resumes when it returns; `health/` reports the oldest pending age |
| Task lost | cannot happen on enqueue (same transaction); a worker killed mid-task leaves `RUNNING`, which the sweep re-enqueues |
| Identity provider down (no token) | retry with backoff; ERROR log after the third failure |
| pharmacy-api down, 5xx, timeout | retry with backoff up to `ERX_MAX_PROCESS_AGE`, then `stuck` + alert |
| pharmacy-api 401/403 (misconfigured scopes/audience) | slowest backoff, ERROR log immediately: a person must fix configuration |
| pharmacy-api cannot fetch the document (gateway API down while its worker is up) | pharmacy-api answers 503 `gateway_unavailable`; the gateway retries -- self-healing once the gateway is back |
| Intake lease held by a concurrent attempt, or a crash mid-intake | the lease (§4.3.3) serializes attempts; an expired lease is taken over; every step is find-or-create, so a half-done attempt is completed rather than duplicated |
| `CancelRx` arrives before its `NewRx` finished | 409 `intake_in_progress`; retried; resolved in order |
| Rx counter not seeded for the pharmacy (409 `rx_counter_not_seeded`) | treated as transient (it is configuration) with ERROR log |

### 7.3 Permanent failures after the acknowledgement

Because a `Status` is "acceptance and responsibility" and the standard
gives a pharmacy no transaction to retract one (§2.4), §5.3 moves every
judgement the gateway can make on its own -- patient and prescriber
type, NPI check digit, NDC format, quantity qualifier, written date --
*in front of* the acknowledgement, where an `Error` is the
answer and the vendor or prescriber corrects and resends under a new
message id. What can still fail permanently afterwards is what only the
pharmacy's own data can decide: a misrouted message, an ambiguous
patient a pharmacist could not resolve, a compound, a quantity-sufficient
or non-integral quantity the pharmacist declined, a controlled substance
, an emergency-cover prescription that does not reconcile, a rejected
review.

For those, the intake sits in pharmacy-api's review queue (`needs_review`
/ `failed`, `GET /erx/intakes/`) and in the gateway's list, and the
pharmacist follows up with the prescriber by phone -- as for a fax that
cannot be read. The standard's own later channel for "not dispensed" is
`RxFill`, which is a separate plan; when it lands, a `failed` intake will
emit one. The plan does not pretend a message can be silently dropped.

`needs_review` is distinct from `failed`: it is work a person can finish
(choose the patient, confirm the compound), and §4.3.5 finishes it through
the same idempotent path.

### 7.4 Sending to the vendor

| Failure | Handling |
|---|---|
| Vendor endpoint unreachable / 5xx | retry with backoff until `deadline_at`, then `abandoned` + ERROR log; the pharmacy-side state (the cancel happened, or not) is unaffected and visible |
| Vendor answers `Error` to our `CancelRxResponse`/`Verify` | `failed`, stored, ERROR log; manual |
| Our outbound message fails our own schema validation | a bug: `failed` before sending, ERROR log, unit-testable |
| A `CancelRx` we approved cannot be reported in time | the prescription is still cancelled here; the prescriber's system will show no response and they call -- the intake list shows the abandoned delivery |
| A parked `CancelRx` reaches its deadline with no pharmacist decision | auto-denied (`expire`, §4.3.5); the `Denied` says so; the fill proceeds as the pharmacist left it |

### 7.5 Things a person can do

Every state above is a row with a status in one of the two databases, in
the admin and behind a list endpoint; `retry/` re-enqueues a `stuck` or
`abandoned` item; `resolve/` finishes a review. Nothing requires a shell.

---

## 8. Testing

### 8.1 pharmacy-api (pytest, existing conventions)

Service principal: recognition (`sub == client_id`), no `Staff` row
created, email not required, scope denial, `staff_identity_required` on
fills, throttle bucket, audit rows with `changed_by_service`, the CHECK.
Intake: the claim (unique, lease, takeover), every outcome in §4.3.3-4,
idempotent replay (same row, same prescription, 200), concurrent claims
(two threads), each matching rule and each `needs_review`/`failed` code,
the gateway fetch stubbed at the HTTP boundary, review resolution, the
`CancelRx` table. Model additions: validators (DEA number, taxonomy
mapping), the relaxations, the migration. Seed: the M2M applications and
their token check against the fake provider (`fake_logto.py` grows the
client-credentials grant). Scope count and role tables.

### 8.2 erx-gateway (pytest)

Fixtures: a set of `NewRx`/`CancelRx` XML documents (valid, each invalid
variant, a test message, a compound, a no-NDC RxNorm-only drug, a
non-integral quantity, `DoNotFill`, `ReturnReceipt`, every `CancelRx`
reference variant), authored against the committed XSD and checked by a
test that every "valid" fixture validates and every "invalid" one fails
on the fault it is named for. The provided example `NewRx` is the first. Validation and parsing (every field in §5.6), response building
(each type validates against the XSD), vendor authentication, the receipt
pipeline step by step (each refusal, duplicate rules, storage/database
failure -> 503 + nothing stored), storage with `mock_aws`, the tasks with
the `ImmediateBackend` and stubbed HTTP, the backoff policy, the sweep, the
lifecycle transitions, the document endpoint's authentication, the
layering test.

### 8.3 e2e

One suite, the bash one, with the cases split per service:
`e2e/cases/pharmacy-api/*.sh` (the existing thirteen, moved) and
`e2e/cases/erx-gateway/*.sh`. `run.sh` learns `PHARMACY_API_URL`,
`ERX_GATEWAY_URL`, `VENDOR_SIM_URL` and `S3_URL` (keeping `BASE_URL` as
pharmacy-api's for compatibility), its `E2E_MINT_CMD` default names
`pharmacy-api`, `mint_token --service` gives it service tokens, and the
report groups features by service. Every run generates its own message
ids and patient names and never cleans up, as today. New cases:

- `pharmacy-api/95_erx_intake.sh`: a dev-minted service token, the
  intake replay (201 then 200, one prescription), scope denial for a
  staff token, the review list, `resolve/` on a parked intake, the
  `needs_review` prescription refusing a fill.
- `erx-gateway/10_receive.sh`: a valid `NewRx` -> HTTP 200 and a
  schema-valid `Status 010` whose `RelatesToMessageID` is ours; **the
  bytes we posted exist in the bucket under
  `inbound/…/<message_id>.xml`** -- fetched with curl straight from
  LocalStack's S3 endpoint (it enforces no signature by default; the
  object's bytes are compared with `cmp` and its `x-amz-meta-sha256`
  header with the digest the case computed) -- each refusal -> the
  `Error` code and `DescriptionCode` §5.3 names; the duplicate rule (same
  content -> identical `Status`; different content -> `900`/`220`); wrong
  Basic credentials -> 401.
- `erx-gateway/20_newrx_end_to_end.sh`: `NewRx` in; poll pharmacy-api
  until the intake is `completed` and the prescription exists with the
  message's values (`intake_source = electronic`, quantity, refills + 1,
  NDC, the prescriber's NPI, the patient's name and DOB); the same `NewRx`
  again makes no second prescription; a controlled-substance `NewRx` ends
  `needs_review` with the prescription `needs_review`, and a pharmacist's
  `accept` activates it; a `NewRx` without an NDC parks as
  `medication_unidentified`; one addressed to another NCPDP ID parks as
  `unknown_pharmacy`.
- `erx-gateway/30_cancelrx.sh`: cancel the prescription above -> the
  intake is `approved`, the prescription `cancelled`, and a schema-valid
  `CancelRxResponse/Approved` arrives at `vendor-sim` with
  `RelatesToMessageID` = the `CancelRx` id and our Rx number, **and its
  bytes exist in the bucket under `outbound/…`**; a `CancelRx` for a
  prescription with a fill in flight parks as `cancel_with_fill_in_flight`
  with a deadline, and a pharmacist's `deny` followed by `respond/`
  produces `Denied` at `vendor-sim` without waiting for the sweep; an
  unknown one -> `Denied` with a `DenialReason`.

`vendor-sim` is `manage.py vendor_sim` in the gateway image (local only,
like `seed_local`): an HTTP server that requires the outbound Basic
credentials, accepts what the gateway sends, answers a SCRIPT `Status
000`, and exposes `GET /received/` so the suite can assert. It is how the
suite plays the vendor's inbound side; the suite itself plays the
outbound side with curl. Since there is no vendor sandbox, this is
the only counterparty the gateway will ever be exercised against before
production, so its behaviour is kept strictly to the schema.

## 9. Stages, in order, each a reviewable change with the checks green

| Stage | Delivers | Gate |
|---|---|---|
| 1 | §3: the move, the workspace, the `pharmacy-auth` package extracted (§4.2.0), two Dockerfiles, Compose renames, per-service env and migrate, CI matrix, e2e split, a gateway skeleton with `health/` | existing tests and e2e unchanged and green in the new layout |
| 2a | §4.1-4.2: scopes, the service principal, audit attribution, `seed_auth` M2M apps and the check, `mint_token --service` | a dev-signed service token reaches a scoped endpoint and the seed's real client-credentials check passes; a user token is unchanged |
| 2b | §4.4-4.5: model fields and relaxations, the `needs_review` status, decimal quantities (its own commit), migrations, serializers, docs | every relaxation covered by a test; the full suite and e2e green after the decimal change; `makemigrations --check` clean |
| 2c | §4.3: `ErxIntake`, the intake service and endpoint, matching, `CancelRx` outcomes, review, the gateway client (stubbed) | the idempotency tests, including concurrency |
| 3 | §5.1-5.6: the gateway project, storage, `ErxMessage`, XSD validation, parsing, responses, the receipt endpoint, the document endpoint, Compose `s3`/`redis`/worker | a `NewRx` is acknowledged and retrievable; every refusal answers the right `Error` |
| 4 | §5.7-5.9 and §7: tasks, the pharmacy-api client, `NewRx` processing, `CancelRx` + `CancelRxResponse`, parked cancels and deadlines, `Verify` on `ReturnReceipt`, the sweep, `vendor-sim`, ops endpoints, the gateway's e2e cases | §8.3 green end to end, bucket check included |
| 5 | docs: `docs/erx.md`, `docs/deployment.md` (both services), `AGENTS.md`, README; `plans/e-prescribing/plan.md` status -> implemented | -- |

Stage 1 first; 2a-2c in order; 3 and 4 after 2c; 5 last.

---

## 10. Interpretations of the brief

- "Restructure into two services" means two Django projects, two
  databases, HTTP-only between them; no shared ORM models, no shared
  database.
- "Each service gets its own migrate step" includes non-schema setup a
  service needs before serving (the bucket), under the same
  never-at-boot rule.
- "The gateway calls an endpoint ... it should not send the whole xml
  message, it should send a message id, pharmacy-npi" -- the NCPDP ID is
  sent as well, and is the required one, because the NPI is optional in
  the standard's pharmacy block while the header's `To` is not.
- "Related" messages for `NewRx`/`CancelRx` intake are `Status`, `Error`,
  `CancelRxResponse`, and `Verify` when a request asks for a return
  receipt; nothing else (`NewRxResponseDenied` is a prescriber's message,
  `RxFill` is a later plan).
- `IntakeSource.ELECTRONIC` is the prescription origin for everything
  the gateway brings in, including a `NewRx` the vendor relayed from a fax
  (the origin code describes how *we* received it).
- A message never updates an existing patient, prescriber or medication
  record; matching reuses, creating fills gaps. Changing a record is a
  staff action.
- Idempotency at pharmacy-api is owned by pharmacy-api (the intake row),
  not by trusting the gateway to call once.
- "In-memory caching" is a cache, not a queue or a lock; Postgres does the
  latter two.
- "Park the prescription in an intake queue" and "flag for review"
  are the same mechanism: `ErxIntake.status = needs_review` with a
  `review_kind`, listed by `GET /erx/intakes/` and resolved by `resolve/`.
  Where the message has produced a prescription, that prescription is
  `needs_review` until the review ends, so nothing parked can be filled.
- "A pharmacist can also trigger the response" means a pharmacist
  resolves the parked intake in pharmacy-api and, if they want it sent
  before the next sweep, calls `respond/`; they never compose a SCRIPT
  message themselves.

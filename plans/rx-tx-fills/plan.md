# Fill records, dispensing workflow, Rx numbers, change history, and the service layer

Status: **implemented** (v4 approved; see "Implementation notes" at the end
for what differs from or was not in the text).

### Changes since v3

- `next_refill_date`, `refill_due`, `refill_buffer_days` and the settings
  model are all out (a separate ticket). No `core` app: nothing left needs one.
- Fill workflow is **B**: `pending → verified → completed`, `cancelled` from
  the first two, `verified → pending` to send a fill back. Transitions are
  **one `POST` action each** (§9), matching how `cancel/` already works;
  `PATCH` edits fields on a pending fill and never touches `status`.
- **At most one in-flight fill per prescription** (§5.4).
- **`X-Staff-Id` header** identifies the acting staff member on a write; the
  `WriteContext` carries it, the change history records it, `operator` and
  `verified_by` come from it (§2.4). The JWT that later carries staff
  identity replaces the header at that one seam. No `Staff`↔`User` link.
- Change history covers every model, `Fill` included (§8).
- **`RxCounter`** per pharmacy organization (`pharmacy` one-to-one,
  `last_issued` nullable, `step`) and a dedicated `services/rx_number.py`;
  the number is the counter value alone; seeding the counter is a
  deployment-time concern and nothing here does it (§7).
- No open questions block. The defaults in §13 are taken unless you object.

## 1. Scope

Replace the `fills_used` counter with a `Fill` row per dispensing that moves
through a status workflow, so a transfer under 21 CFR 1306.25 can produce the
date, location and dispensing identity of every previous fill. Add the
intake source (NCPDP prescription origin code) and an Rx number to `Prescription`. Add a
column-level change history for every model. Introduce the service and
repository layers it is built on.

Out of scope: the transfer record, a transferred-out status, the pre-transfer
completeness check, any 21 CFR 1306.13 partial-fill window, `next_refill_date`
and its configuration, an API over the change history, and any link between
`auth.User` and `Staff`.

## 2. Architecture: views → services → repositories

### 2.1 The rule

> Views get querysets from repositories and call services for anything that
> changes state. Nothing outside `pharmacy/repositories/` names a manager
> (`objects`, `all_objects`, or a related manager).

Reads do not go through services: a GET has queries and no rules, and a
service read would be an empty pass-through. What a read must not do is
define its own queryset — the locked re-read in a write path and the list
read share one definition in the repository, so joins cannot drift.

### 2.2 Layout

```
pharmacy/
  repositories/   __init__.py (one instance per model), base.py (Repository),
                  address.py medication.py patient.py prescriber.py pharmacy.py
                  pharmacy_location.py staff.py prescription.py fill.py
                  rx_counter.py field_change.py
  services/       __init__.py, base.py (CrudService),
                  address.py … staff.py (thin), prescription.py, fill.py,
                  rx_number.py
```

**`Repository`** — the only place a query is written. Stateless, one
instance per module, returns `QuerySet`s (DRF's filtering, pagination and
`nested_list` need one; this is not an ORM-hiding abstraction).

```text
class Repository:
    model = None
    def live(self): ...                 # objects + the joins every read needs
    def find(self, **lookup): ...       # duplicate checks, header lookups
    def get(self, pk): ...
    def get_locked(self, pk): ...       # select_for_update(of=("self",))
    def create(self, **fields): ...
    def save(self, instance, *, update_fields=None): ...
    def delete(self, instance): ...     # soft
    def live_reference_counts(self, instance, relations): ...
```

**`CrudService`** — writes only; no read methods.

```text
class CrudService:
    repository = None
    protected_relations = ()            # moves here from the viewset: it is a rule
    def create(self, ctx, **fields): ...
    def update(self, ctx, instance, **fields): ...
    def delete(self, ctx, instance): ...
```

Six reference services set `repository` and `protected_relations` and
nothing else — thin on purpose: a uniform call path now and a named place
for the rule that arrives later. `PrescriptionService` holds the
prescription rules (§5, §7); `FillService` holds the fill workflow (§4–5)
and is only reached through a prescription id.

**Views** are adapters: parse → service → serialize. `SoftDeleteModelViewSet`
declares `repository` and `service`; `get_queryset()` is
`self.repository.live()`; `perform_create` is
`serializer.instance = self.service.create(self.ctx, **serializer.validated_data)`.
`serializer.save()` is never called. `PharmacyScopedViewSet` resolves its
parent through `repositories.pharmacy`; a `PrescriptionScopedViewSet` of the
same shape serves fills. `nested_list` is unchanged.

**Serializers** validate and render, never write. `PrimaryKeyRelatedField`
querysets and `reject_live_duplicate` lookups come from repositories.

### 2.3 Decisions throughout

1. The service owns `transaction.atomic()` and the lock;
   `Repository.get_locked()` carries the `of=("self",)` reasoning. Same
   statement as today, different file.
2. Services raise the existing `Conflict` for state refusals and DRF's
   `ValidationError` for the quantity rule you asked to be a 400 (§5.2).
3. Model methods do not persist; `expire_if_due()` becomes a service
   decision. Model properties may traverse their own relations.
4. Stateless classes, one instance per module, `services.prescription.x()`.
5. Outside the rule: migrations, `seed_local`, tests. Stated in AGENTS.md.
6. `tests/test_layering.py` scans `views/`, `serializers/`, `services/` for
   `.objects`, `.all_objects`, `select_for_update` and fails on any hit.

### 2.4 Write context and `X-Staff-Id`

Every service write takes `ctx: WriteContext` first — a frozen dataclass the
base viewset builds once per request:

| | |
|---|---|
| `user` | `request.user` — always present, every endpoint is authenticated |
| `staff` | the `Staff` named by the `X-Staff-Id` header, or `None` |
| `path`, `method`, `request_id` | from the request and `RequestIDMiddleware` |

The header is read in one place (`views/base.py`), resolved through
`repositories.staff.find(pk=…, is_active=True)`. It is **optional on every
write** and ignored on reads; when present it must name a live, active
staff member or the request is a 400 `invalid_staff` before any service
runs. Fill create and `verify/` **require** it (§4) and refuse with 400
`staff_required` when it is absent. Declared in the schema as a header
parameter on every write operation, the way `PHARMACY_ID` is declared.

This is the seam: when the JWT carries staff identity, `WriteContext.staff`
is populated from the token instead of the header and nothing below the
view changes. Tests calling services directly build a `WriteContext` by
hand.

### 2.5 Audit hook

`CrudService.create/update/delete` snapshot the instance before and after,
diff field by field, and hand the diff to `repositories.field_change.record()`
in the same transaction. `PrescriptionService` and `FillService` go through
the same helper for their own writes, so there is exactly one place a
history row is produced (§8).

### 2.6 Sequencing

Two commits: (1) the layering refactor, behaviour-preserving, every existing
test unmodified and green; (2) the feature.

## 3. `Prescription`

| Field | Change |
|---|---|
| `fills_used` | **Removed**, with its CHECK. No backfill: not in production. |
| `quantity`, `fills_authorized` | Unchanged. |
| `rx_number` | **New.** `PositiveBigIntegerField`, indexed, assigned by `services.rx_number` on create (§7). Read-only. |
| `intake_source` | **New** (named `prescription_origin_code` in v4; renamed at your request). NCPDP D.0 419-DJ, stored as a word rather than the wire digit (also changed after v4): `not_specified`, `provider`, `telephone`, `electronic`, `fax`, `pharmacy` — codes 0–5 in that order. Set on create, immutable after (Q3). `pharmacy` is what NCPDP assigns when a new Rx number is created from an existing prescription — a transfer in — which is why one field covers what `intake_source` and `origin_format` did. |

Derived, never stored, on the model and serialized read-only:

- `quantity_authorized = quantity * fills_authorized`
- `quantity_dispensed = Σ quantity_dispensed over fills with status completed`
- `quantity_in_progress = the in-flight fill's quantity, or 0`
- `quantity_remaining = quantity_authorized - quantity_dispensed - quantity_in_progress`
- `fills_used = quantity_dispensed / quantity`, `fills_remaining = quantity_remaining / quantity` — `Decimal`, 8 places, rendered as JSON numbers (`DecimalField(decimal_places=8, coerce_to_string=False)`).

Constraints: `expires_after_written` stays. No CHECK over any total, and no
uniqueness on `rx_number` (§7).

`PATCH` to `quantity` or `fills_authorized` is a 400 on the field when the
new `quantity_authorized` would fall below `quantity_dispensed + quantity_in_progress`.
Cancelling a prescription with a fill in flight: Q6.

## 4. `Fill`

`pharmacy/models/fill.py`, `TimeStampedModel`. No `DELETE` route.

| Field | Type | Notes |
|---|---|---|
| `prescription` | FK `PROTECT`, `related_name="fills"` | |
| `sequence` | `PositiveIntegerField` ≥ 1 | `repositories.fill.next_sequence()` under the prescription lock. Unique with `prescription`, unconditional; cancelled fills keep their number. |
| `pharmacy_location` | FK `PROTECT` | Snapshot of the prescription's location at creation. |
| `status` | choices | `pending`, `verified`, `completed`, `cancelled`. Transitions in §9. |
| `operator` | FK `Staff`, `PROTECT`, non-null | `ctx.staff` at creation. Any active staff member (Q7). |
| `verified_by` | FK `Staff`, `PROTECT`, nullable | `ctx.staff` at `verify/`; must have `role == pharmacist`. Cleared by `unverify/`. |
| `quantity_dispensed` | `PositiveIntegerField`, CHECK ≥ 1 | Editable while `pending`. |
| `days_supply` | `PositiveIntegerField`, nullable, CHECK ≥ 1 | Editable while `pending`. |
| `dispensed_at` | `DateTimeField`, nullable | Server-set to now at `complete/`; never client-set. CHECK `(status = completed) = (dispensed_at IS NOT NULL)`. |

Constraints: CHECK `(status = verified OR status = completed) = (verified_by IS NOT NULL)`;
partial unique `(prescription) WHERE status IN (pending, verified) AND deleted IS NULL`,
named `uniq_live_fill_in_flight` and registered in `LIVE_UNIQUE_CONSTRAINTS`
so the concurrent-create race maps to the same 409 the service raises.

Derived: `is_partial = quantity_dispensed < prescription.quantity`.
`Meta.ordering = ["sequence"]`.

## 5. Accounting rules (in the services, under the prescription lock)

Every fill write — create, every action, `PATCH` — runs inside
`transaction.atomic()` after `repositories.prescription.get_locked(pk)`,
because every one of them changes a total the next write reads. The same row
lock the fill path holds today, widened to the workflow.

1. **Create** refuses with the existing codes: `expired` (lazy expiry
   recorded first), `not_active`, `no_fills_remaining` when
   `quantity_remaining == 0`; `details` carry the three quantities.
2. **The total rule.** `1 <= quantity_dispensed <= quantity_remaining`. No
   per-fill cap. A violation is a **400** on `quantity_dispensed`, with the
   quantities in the message. A `PATCH` raising a pending fill's quantity is
   checked against the remainder excluding that fill's own reservation.
   This is the authorized-quantity invariant: a business rule, held under
   the lock, pinned by the concurrency test.
3. Default `quantity_dispensed` when the body omits it:
   `min(quantity, quantity_remaining)`.
4. **One in flight.** A fill that is `pending` or `verified` is the
   prescription's in-flight fill; creating another is a 409
   `fill_in_progress` with `details.fill_id`. It reserves its quantity:
   `quantity_remaining` counts it, so it cannot be over-committed while
   someone else fills the same prescription.
5. **`complete/`** re-checks the prescription is still `active` (recording
   expiry if due), requires status `verified`, sets `dispensed_at`, and — if
   `quantity_dispensed` now equals `quantity_authorized` — marks the
   **prescription** `completed`. The quantity decides, not a count.
6. **`cancel/`** on a fill releases its reservation and is allowed whatever
   the prescription's status, so a fill left pending on a prescription that
   has since expired can be closed out.
7. Sequence, totals and status are all read under the lock; two concurrent
   creates produce one 201 and one 409.

## 6. (removed — `next_refill_date` is a separate ticket)

## 7. Rx number

The number is the counter value alone — no prefix. It is unique within a
pharmacy organization by construction (the counter only goes up) and not
across organizations, which is how Rx numbers work between real pharmacies:
a label and a transfer name the store by its NCPDP ID separately.

**`RxCounter`** (`pharmacy/models/rx_counter.py`), plain `models.Model` —
a counter is never soft-deleted or listed:

| Field | Type | |
|---|---|---|
| `pharmacy` | `OneToOneField` → `Pharmacy`, `PROTECT` | one per pharmacy organization, shared by all its stores |
| `last_issued` | `PositiveBigIntegerField`, **null**, default null | the last value handed out; null means the pharmacy has not been onboarded |
| `step` | `PositiveSmallIntegerField`, default 1 | the increment |

**`services/rx_number.py`** is the only thing that computes a number:
`next_rx_number(pharmacy)` — called by `PrescriptionService.create` inside
the prescription's transaction — takes the counter row with
`select_for_update`, refuses if there is no row or `last_issued` is null
(409 `rx_counter_not_seeded`, `details.pharmacy_id`), refuses if
`last_issued + step` would exceed 12 digits (409 `rx_number_exhausted`),
otherwise sets `last_issued += step`, saves through
`repositories.rx_counter`, and returns the new value. A row lock per
pharmacy is the cheapest collision-free primitive the ORM can express;
contention is only between two creates in the same chain. A Postgres
`SEQUENCE` would be runtime DDL and is not transactional; the row is.

**Seeding.** No migration creates a counter: in uat and prod the starting
value is a fact about the pharmacy and belongs to its onboarding step.
Locally, `seed_local` onboards every pharmacy it knows — Acme included —
with `last_issued = 1,000,000,000` (added after v4: "let's seed it,
1000000000 for starter"), through `get_or_create` so a re-run never rewinds
a counter that has issued numbers. Until a pharmacy has a counter, `POST
/prescriptions/` at any of its stores answers 409 `rx_counter_not_seeded`;
`docs/deployment.md` says so.

- `seed_local` sets explicit `rx_number`s on its demo prescriptions through
  the ORM (it is outside the layering), all below the counter's start.
- Tests that create prescriptions through the API get a counter from a
  fixture (`rx_counter(pharmacy)`), and `PharmacyFactory` gains a
  `RelatedFactory` for one so factory-built pharmacies can issue numbers.
  `PrescriptionFactory` takes its own number from a factory sequence.

No database uniqueness on `rx_number`: per-pharmacy uniqueness would need
the pharmacy denormalized onto `Prescription`, which is the drift surface
you ruled out, and the counter already guarantees it. Indexed, for lookup
by the number on a label.

## 8. Change history

`FieldChange` in `pharmacy/models/field_change.py`: append-only, plain
`models.Model` (no soft delete, no `updated_at` — an audit row is never
edited), UUID pk.

| Column | |
|---|---|
| `model` | the model's label, e.g. `prescription`, `fill` |
| `record_id` | UUID of the changed row |
| `field` | column name |
| `old_value`, `new_value` | `JSONField`; null on create / soft delete respectively; FKs as id strings, datetimes ISO-8601, decimals as strings |
| `changed_at` | server time |
| `changed_by_user` | FK `auth.User`, `PROTECT` — always set |
| `changed_by_staff` | FK `Staff`, `PROTECT`, nullable — `ctx.staff` |
| `request_path`, `request_method`, `request_id` | from the `WriteContext` |

One row per changed column per write, for **every model** — the six
reference resources through `CrudService`, prescriptions and fills through
their services, including status moves, lazy expiry, `dispensed_at`,
`verified_by`. A create is one row per populated field with
`old_value = null` (Q5); a soft delete is one row for `deleted`. Rows from
one request share `request_id`. Indexed on `(model, record_id, changed_at)`.

Written only through `repositories.field_change.record(ctx, instance, diff)`
in the writer's transaction, so a rolled-back write leaves no history.
No API (Q4). `seed_local` and migrations produce no rows.

## 9. API

**The collection is nested under the prescription; a fill's detail is
top-level** (changed after v4, at your request: "update the APIs for fills
collection and fill details"). The collection is laid out by the existing
`sub_resource()` helper with no detail route; the detail by the router,
which lays out `/fills/{id}/` and its actions and nothing at `/fills/`
because the viewset has no `list` or `create`.

| Method | Path | Staff header | |
|---|---|---|---|
| `GET` | `/prescriptions/{prescription_id}/fills/` | — | by `sequence`; filters `status`, `operator`, `verified_by` |
| `POST` | `/prescriptions/{prescription_id}/fills/` | **required** → `operator` | `{quantity_dispensed?, days_supply?}`; 201, status `pending` |
| `GET` | `/fills/{id}/` | — | |
| `PATCH` | `/fills/{id}/` | optional | `{quantity_dispensed?, days_supply?}`; `pending` only, else 409 `not_pending` |
| `POST` | `/fills/{id}/verify/` | **required**, must be a pharmacist → `verified_by` | `pending → verified` |
| `POST` | `/fills/{id}/unverify/` | optional | `verified → pending`, clears `verified_by` |
| `POST` | `/fills/{id}/complete/` | optional | `verified → completed`; §5.5 |
| `POST` | `/fills/{id}/cancel/` | optional | `pending` or `verified` → `cancelled` |
| `DELETE` | `/fills/{id}/` | | **open** — see the implementation notes |

Any transition from a state not listed is a 409 `invalid_transition` with
`details.status` — the key every other refusal uses for the current status.
`completed` and `cancelled` are terminal: no action and no `PATCH` touches
them (`already_terminal`, as on prescriptions).

The fill representation:
`{id, sequence, status, rx_number, pharmacy_location (summary), operator (staff summary), verified_by (staff summary | null), quantity_dispensed, is_partial, days_supply, dispensed_at, created_at, updated_at}`.

**`POST /prescriptions/{id}/fill/` is retired.** The prescription
representation gains `rx_number`, `intake_source`,
`quantity_authorized`, `quantity_dispensed`, `quantity_in_progress`,
`quantity_remaining`, `fills_remaining`; `fills_used` stays;
`TRANSITION_HINTS[COMPLETED]` points at `complete/`. `cancel/` on the
prescription stays.

Swagger: a `Fills` tag; `PRESCRIPTION_ID` and `X_STAFF_ID` parameters
declared alongside `PHARMACY_ID`. `docs/prescriptions.md` gets the workflow
and the new lifecycle row; `docs/errors.md` the new codes; `docs/api.md`
the resource, header and filter rows.

## 10. Migrations

`0008` depends on `0007_staff`; nothing past `0009`.

- **`0008_fills_rx_numbers_change_history`** — DDL: remove `fills_used` and
  `fills_used_lte_authorized`; add `intake_source` (existing rows
  get `not_specified`) and `rx_number` nullable; create `Fill`,
  `RxCounter`, `FieldChange` with constraints and indexes.
- **`0009_assign_rx_numbers`** — `RunPython` numbering any prescription
  already in a dev database (per pharmacy, `written_at` order, from 1 —
  arithmetic on rows that exist, no counter touched; the deployment-time
  seeding sets `last_issued` above them), then `AlterField` to non-null.
  Reverse nulls them. The `RunPython`-then-DDL pairing is verified on the
  embedded Postgres; if it trips "pending trigger events" I stop and ask.

## 11. Tests

**Commit 1:** every existing test unmodified and green; `test_layering.py`.

**Commit 2**, `test_prescriptions_api.py` and new `test_fills_api.py`:

- Existing prescription tests keep their assertions except where `fill/`
  was the mechanism. `TestFill`, `test_completed_cannot_gain_fills` and the
  lifecycle row are rewritten to `POST fills/` → `verify/` → `complete/`
  with the same assertions on the prescription. `test_fill_exhausted_is_409`
  builds its used fill with `FillFactory(status=completed)`;
  `test_put_of_the_current_body…` drops `fills_used=3`.
  `test_fill_and_cancel_cost_the_same_queries` is replaced by a direct
  assertion that `repositories.prescription.get_locked()` and `live()` carry
  the same joins. Create payloads gain `intake_source` if Q3
  makes it required.
- `TestStaffHeader`: missing header on fill create is 400 `staff_required`;
  unknown, deleted or inactive id is 400 `invalid_staff`; a technician on
  `verify/` is 400; the header is ignored on GET.
- `TestFillWorkflow`: create → `pending`, sequence 1, location snapshot,
  operator from the header, reservation visible in `quantity_remaining`;
  every legal transition; every illegal one is 409 `invalid_transition`;
  `unverify/` clears `verified_by`; `complete/` sets `dispensed_at` and
  flips the prescription on the last quantity; terminal fills refuse
  `PATCH` and actions; a second create while one is in flight is 409
  `fill_in_progress`; `cancel/` releases it.
- `TestQuantityRule`: over the remainder on create and `PATCH` is a 400 on
  `quantity_dispensed`; more than one fill's worth at once is allowed; 30 of
  90 is `is_partial` and 30 + 60 consume one authorized fill between them;
  `fills_remaining` is `0.33333333` after the 30.
- `TestRxNumber`: consecutive within a pharmacy across its stores,
  independent between pharmacies, first value is `last_issued + step`,
  honours `step`; no row or null `last_issued` is 409
  `rx_counter_not_seeded` and no prescription is written; the 12-digit
  bound is 409 `rx_number_exhausted`; two pharmacies may hold the same
  number.
- `TestChangeHistory`: a prescription `PATCH` produces one row per changed
  field with old/new, user, staff (when the header is sent), path, method,
  request id; a fill transition produces its rows; a create produces rows
  with `old_value = null`; a refused write produces none; every resource's
  create goes through the hook (parametrized over the six).
- `TestConcurrentFill` (`django_db(transaction=True)`): two threads `POST`
  fills; exactly one 201, one 409, one row. Two threads creating
  prescriptions in one pharmacy get distinct Rx numbers. Skipped loudly on a
  non-Postgres backend.
- `test_models.py`: the `Fill` CHECKs, `uniq_live_fill_in_flight` and the
  `(prescription, sequence)` unique as `IntegrityError`s.
- A migration test for `0009`; dropped with a note if flaky.

CI's four plus `spectacular --fail-on-warn`.

## 12. Running it, and files

As before: `uv sync --locked --all-groups` with the system Python,
`collectstatic`, `PHARMACY_TEST_DB=embedded uv run pytest` on the opt-in the
staff work added. Embedded is PostgreSQL 16.2, not 18; the report says so.

Created: `repositories/` (12 modules), `services/` (12), `models/fill.py`,
`models/rx_counter.py`, `models/field_change.py`,
`serializers/fill.py`, `views/fill.py`, `migrations/0008_*`, `0009_*`,
`tests/test_fills_api.py`, `tests/test_layering.py`.

Modified: `models/prescription.py`, `models/__init__.py`, every module in
`serializers/` and `views/`, `filters.py`, `urls.py`, `views/tags.py`,
`config/settings/base.py` (the `Fills` Swagger tag only), `exceptions.py`
(new codes and constraint name), `tests/test_prescriptions_api.py`,
`test_models.py`, `factories.py`, `seed_local.py`, `docs/prescriptions.md`,
`errors.md`, `api.md`, `development.md`, `deployment.md` (onboarding note),
`AGENTS.md`.

Not touched: `models/pharmacy_location.py`, `models/patient.py`,
`models/staff.py`, Compose, `.env.local`, Dockerfile, `pyproject.toml`,
`uv.lock`.

## 13. Open questions

Defaults in bold; none blocks. I take the defaults unless you say otherwise.

1. *(settled: counter only, §7.)*
2. **`unverify/`** or **`pending/`** for `verified → pending`. **Default:
   `unverify/`**, a verb like the other three.
3. **`intake_source` on create.** **Default: required, immutable
   after** (four existing create payloads gain it). Alternative: default `0`.
4. **History API.** **Default: none in this change.**
5. **History on create.** **Default: one row per populated field.**
   Alternative: one row per create with the record in `new_value`.
6. **Cancelling a prescription with a fill in flight.** **Default: 409
   `fill_in_progress` with `details.fill_id`** — cancel the fill first.
7. **Operator / verifier employer.** **Default: any active staff member.**
   Alternative: must be employed by the location's pharmacy (the PIC rule).

## 14. Where the whole-fill transfer spec goes imprecise

`no_fills_remaining` keys on `quantity_remaining == 0`; whether a
prescription with a partial balance outstanding is transferable is not
decided here; the count reported to a receiving pharmacy (float, floor, or
"0 refills plus 60 units") is the spec's choice and the remaining quantity
travels alongside it; and an **in-flight fill at transfer time** — reserved,
neither dispensed nor available — is a state the spec has to address.

## 15. Implementation notes

What was built follows §2–§13. Differences and discoveries, so review can
focus on them:

- **Not committed**, at your request: the work sits uncommitted on top of
  `baseline` after a soft reset. It was built as two steps — the layering
  refactor (behaviour-preserving; the 254 existing tests unmodified and
  green, plus the layering guard) and then the feature — and was verified
  at each.
- **Instances are plural, modules singular**: `repositories.prescriptions`,
  `services.fills`, `repositories/prescription.py`. An instance named
  `prescription` in `repositories/__init__.py` would shadow the submodule of
  the same name.
- **`ScopedViewSetMixin`** generalizes the staff work's
  `PharmacyScopedViewSet` (which remains, as a subclass) so fills could be
  scoped under prescriptions without a second copy; `ServiceViewSetMixin`
  carries the repository/service plumbing so `FillViewSet` can compose it
  with only the mixins it serves (no `PUT`, no `DELETE`).
- **`ApiError`** is a new base in `exceptions.py` for the 400s with a code
  of their own (`InvalidRequest`: `staff_required`, `invalid_staff`,
  `pharmacist_required`); `Conflict` now subclasses it. The quantity rule is
  a DRF `ValidationError` as planned, so it renders as `validation_error` on
  `quantity_dispensed`.
- **Change history details**: `id`, `updated_at` and safedelete's
  `deleted_by_cascade` are never recorded; a create records every populated
  (non-null, non-blank) column. FK columns are recorded under their
  `_id` name with the id as a string. The hook is `services.history`, called
  by `CrudService` and the two domain services; the layering test allows
  `field_change` and `rx_counter` to have no service of their own because
  each is written by exactly one named service.
- **`get_locked()` strips the prefetch** the read queryset carries; the
  test that replaced `test_fill_and_cancel_cost_the_same_queries` pins that
  the locked statement is one `SELECT ... FOR UPDATE OF` with the joins.
- **Migration `0009`** (`RunPython` then `AlterField` in one file) applied
  cleanly on the embedded Postgres; the executor test covers it. Existing
  rows get `intake_source = "not_specified"` in `0008`.
- **`seed_local` onboards the local pharmacies** with a counter at
  1,000,000,000 (your instruction after v4), and the one completed demo
  prescription became **cancelled**. With no fills seeded, `completed` would claim a dispensing
  no row describes and the derived quantities would contradict the status.
  Not in the plan text; a one-line revert if a completed demo row matters.
- **Concurrency tests were mutation-checked**: with the counter lock and the
  prescription lock removed, both race tests fail on every run.
- **Run on PostgreSQL 16.2** (embedded, `PHARMACY_TEST_DB=embedded`), not
  18: 395 tests pass; `ruff check`, `ruff format --check`,
  `makemigrations --check` and `spectacular --fail-on-warn` are clean.
  Compose on 18 remains the run that counts.
- **Fill routes restructured after v4**: the collection stays at
  `/prescriptions/{id}/fills/` (list, create); detail, `PATCH` and the four
  actions moved to `/fills/{id}/…`. Two viewsets share the serializer,
  repository and service. **`DELETE /fills/{id}/` is not built**: the
  approved plan says a fill is never deleted (a fill that should not have
  happened is cancelled), and "void/delete" on a dispensing record is a
  choice about what happens to the prescription's totals and the fill's
  sequence — raised for discussion rather than guessed.

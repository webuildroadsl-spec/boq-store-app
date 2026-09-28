# BOQ & Store — Road Construction Control App

Django + PostgreSQL backend for the BOQ and Store modules described in
[`docs/requirements.md`](docs/requirements.md). This repo is being built
one step at a time from Section 8 of that spec.

**Current status: Step 7 of 10** — Requisition, issue, return.

## Setup (local development)

1. Clone the repo and enter it.
2. Create and activate a virtualenv:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Create a PostgreSQL database and user matching what you'll put in
   `.env`, e.g.:
   ```sql
   CREATE USER boq_user WITH PASSWORD 'your-password';
   CREATE DATABASE boq_store_db OWNER boq_user;
   ```
5. Copy `.env.example` to `.env` and fill in real values (a generated
   `SECRET_KEY`, your DB credentials).
6. Run migrations and create an admin user:
   ```bash
   python manage.py migrate
   python manage.py createsuperuser
   ```
7. Run the dev server:
   ```bash
   python manage.py runserver
   ```
   Visit `http://127.0.0.1:8000/` — you'll be sent to `/login/`. Log in
   with the superuser you just created and you'll land on the home page
   with a logout button. `/admin/` is Django's built-in admin.

## Running tests

```bash
python manage.py test
```

## Project layout

- `config/` — Django project settings, root URLs, WSGI/ASGI entry
  points.
- `accounts/` — custom user model, login/logout views, home page, and
  the tests for all of it.
- `core/` — Company, Project, Section, UnitOfMeasure and
  ProjectMembership models; project-scoped list/detail views; the
  `core.permissions` helpers everything else should filter through.
- `boq/` — BOQ, Bill, BOQItem, VariationOrder models (including
  `BOQ.create_revision()` and `BOQ.approve()`); `bill_items_save` (the
  JSON API the grid saves to) and its per-role access rules;
  `static/boq/grid.js` (the manual entry grid's front end);
  `importer.py` (Excel import parsing/validation) and `exporter.py`
  (Excel/PDF export, and the import template download).
- `store/` — Store, StoreItem, Supplier, ItemCategory, StockMovement,
  GRN/GRNLine/GRNAttachment models; `StockMovement.current_balance()`
  (the weighted-average stock formula); `GRN.post()`; the
  `store.permissions` helpers (including the "Storekeeper sees only
  their own store" scoping).
- `templates/` — project-wide templates (`base.html`,
  `registration/login.html`).
- `docs/requirements.md` — the full requirements spec this build follows.

## The BOQ manual entry grid (step 3)

`/projects/<id>/boq/` shows the current BOQ's bills and their totals,
and lets a QS (or an Admin/superuser) add bills. Each bill's
`/bills/<id>/` page is the item grid itself — a small dependency-free
JavaScript spreadsheet-style table (`boq/static/boq/grid.js`) backed by
a JSON API (`boq/views.bill_items_save`). The JS is UI only; every
business rule below is still enforced in Django, through the same
`BOQItemForm` that validates each row.

Grid UX:

- Tab / Shift+Tab move through fields left-to-right, top-to-bottom
  (native browser field order).
- Arrow Up/Down move focus a row at a time in the same column; Arrow
  Left/Right move a cell over once the text cursor is at that edge.
- Enter moves down a row, adding a new blank one first if you're on the
  last row.
- Pasting a multi-cell block (e.g. copied out of Excel — tab-separated
  columns, newline-separated rows) fills forward from the cell you
  pasted into, adding rows as needed. Type and unit columns are matched
  by their code/label text (e.g. pasting "m³" resolves to that unit).
- "Add row" appends a blank row. A row's "✕" deletes it — instantly for
  a row that was never saved, via one small request for a saved one.
- Not attempted: cell range selection, multi-cell copy (paste only),
  undo. This is a working spreadsheet-style entry tool, not a full
  spreadsheet application.

Business rules enforced server-side, on every save:

- Rule 1 — amount = quantity × rate, rounded to 2 decimal places;
  headings carry no quantity, rate or amount.
- Rule 2 — Lump Sum and Provisional Sum items are entered as a single
  figure: quantity is forced to 1 and the unit to "sum", so what you
  type into "rate" *is* the amount.
- Rule 3 — an item reference must be unique within the BOQ version;
  the API rejects a duplicate with a per-row error instead of saving it.
- Section 2's "Create and edit BOQ" row — only a QS (or superuser) can
  add bills or edit items; Project Manager/Site Engineer/Viewer can look
  but not edit; a Storekeeper gets a 403 and can't see the BOQ at all.

**Testing note:** there's no headless browser in this environment, so
the JS itself isn't exercised by the automated test suite — only
reviewed and checked for syntax errors. What *is* fully tested (19
tests) is the JSON API it depends on: the same calculation, rule-3, and
permission behavior as before, now posted as JSON instead of a Django
form. It's also been exercised manually end-to-end over real HTTP
(logging in, adding a bill, posting the exact payload the grid would
send, reading the saved amount back) — see the commit message for the
figures. If you can click through it in a real browser before step 4,
that's worth doing; it hasn't been.

What's *not* built yet, on purpose (later steps per Section 8): BOQ
versions/approval/variation orders and "only one Approved version"
(step 5), the full bill summary with contingency/VAT (step 7's
reporting), and material allowances (step 9).

## BOQ Excel import and export (step 4)

`/projects/<id>/boq/` now also links to:

- **Download the import template** — a blank `.xlsx` with the header
  row the importer expects, plus a few example rows (a heading, a
  measured item, a lump sum).
- **Import a BOQ from Excel** — upload → preview → confirm. Nothing is
  saved until you confirm, and confirming re-validates every row
  server-side one more time; if even one row still has an error, the
  whole import is refused (no partial import).
- **Download as Excel / as PDF** — the current BOQ, in the "bill
  totals plus a summary page" layout the spec asks for. These are
  read-only (`can_view_boq`, not `can_edit_boq`) — anyone who can see
  the BOQ can export it.

Scope decisions made with the user before building this, because real
BOQ Excel layouts vary a lot and the spec itself flags this as an open
question:

- **Flat layout only.** One row per item, with the bill number and
  title repeated on every row of that bill (matching the downloadable
  template) — not a sheet with merged bill-header rows. A contractor
  whose existing BOQs use a different layout would need to re-shape
  them into this one first (or ask for that layout to be supported
  later).
- **Item type is inferred, not a mapped column.** A row with no
  quantity and no rate is a Heading; a row whose unit is "sum" is a
  Lump Sum (quantity forced to 1, matching rule 2); everything else is
  Measured. Provisional Sum / Prime Cost / Daywork rows import as one
  of those two and can be reclassified afterward in the manual-entry
  grid — there's no way to tell them apart from a spreadsheet cell
  alone without an explicit column for it.
- Column headers are matched by name (case/spacing-insensitive, a
  short alias list — "Qty" and "Quantity" both work), with the mapping
  shown and editable on the preview screen for a file whose headers
  don't match at all.
- Bill number/title only need to appear once per bill in the file
  (the row where that bill first appears) — every subsequent row for
  that bill can leave the title blank, matching how a real spreadsheet
  is usually filled in.

**Tested:** `boq/test_importer.py` unit-tests `auto_detect_mapping` and
`parse_rows` directly (headings, lump sum inference, duplicate
references — both within the file and against the existing BOQ,
unknown units/sections, missing required fields) plus the literal
acceptance test — a 500-row workbook across 10 bills is built with
openpyxl in the test itself, its expected bill/grand totals are
computed independently using the same rounding rule the model uses,
and the imported BOQ's totals are asserted to match to the cent.
`boq/test_import_views.py` drives the same thing through the Django
test client as a browser would (real multipart file upload,
upload → preview → confirm, permission checks on every step and on
both export endpoints). It's also been exercised manually end-to-end
against the running dev server with curl: a real 500-line, 10-bill
workbook uploaded, previewed, confirmed, and the resulting
`BOQ.grand_total` (2,270,719.90) checked against the same figure
computed independently outside the app — then the Excel and PDF
exports and the template download were downloaded and opened to
confirm they're genuine, readable files.

**Not built** (deliberately, per Section 8): re-importing into a BOQ
that already has items only adds new ones (rule 3's uniqueness check
rejects a duplicate reference rather than updating it) — there's no
"replace" or "merge" import yet. WeasyPrint needs system-level Pango/
cairo libraries; they're present in this sandbox already, but the
production server will need them installed too (see WeasyPrint's own
install docs for the target OS).

## BOQ versions, approval and variation orders (step 5)

`/projects/<id>/boq/versions/` lists every version of a project's BOQ —
Original, Revisions and Variation Orders together, newest first — and
is where "Create revision" and "Approve" live. A single version's own
page (`boq_version_detail`) shows those same actions for that version,
plus links to "All versions" and "Variation orders".

- **Revise BOQ** (rule 4/5) — `BOQ.create_revision()` deep-copies every
  bill and item of the current **Approved** version into a brand-new
  Draft (a new version number, `parent_item` links remapped onto the
  copies, not the originals) — the source version itself is never
  touched. Only usable from an Approved version, by a QS (or Admin).
- **Approve** — `BOQ.approve(user)` sets the Draft to Approved, records
  who and when, and — rule 5, "only one version per project can be
  Approved at a time" — supersedes whichever version was previously
  Approved. This is the step 5 acceptance test: *"Approving Rev 1
  supersedes Original."* Gated on a new `can_approve_boq` (Project
  Manager or Admin only — Section 2's "Approve BOQ revision or
  variation" row; a QS can build a revision but not sign it off).
- **Variation orders** — `/projects/<id>/boq/variation-orders/` lists
  them and has the "New variation order" form (date, description,
  reason, instructed by). Creating one needs an existing Approved BOQ
  (there's nothing to vary from otherwise) and copies it into a new
  Draft version the same way a revision does — the QS then adds, omits
  or changes items on it through the ordinary manual-entry grid.
  `VariationOrder.value_impact` is that version's grand total minus
  the Approved baseline it was copied from, so it updates live as
  items are edited. Approving the VO's linked BOQ version also marks
  the VO itself Approved — Section 2 treats "approve a revision" and
  "approve a variation" as the same action, so there's one approve
  button, not two.
- **Compare versions** — `/projects/<id>/boq/versions/compare/?a=<pk>&b=<pk>`
  matches every item between the two versions by item reference and
  reports each as added, removed, changed or unchanged, plus its value
  difference, alongside the overall grand-total difference. This is
  the other half of the step 5 acceptance test.

**Scope simplification, disclosed here rather than left implicit:** the
plain `/boq/` and Excel import/export URLs (no version specified) don't
take a version id — they resolve to "the most recent Draft in
progress, or the current Approved version if nothing is in Draft"
(`views._current_boq`). In the normal flow there's only ever one Draft
per project at a time (the original before its first approval, or the
one revision/VO being worked on), so this is unambiguous in practice;
if it ever isn't, the highest version number wins. Import/export are
only offered on a version's own page when that page's version *is*
this current one — visiting a Superseded or an unrelated Draft version
shows a note pointing at "All versions" instead of a misleading export
button. Making import/export properly version-scoped (their own
`boq_pk`-based URLs) is straightforward follow-up work, not done here
to keep this step's surface area to what the acceptance test needed.

**Not built** (rule 6, deferred until the Store module exists): "an
item that has stock issued against it cannot be deleted in a later
version" has nothing to enforce yet — there's no stock issue concept
until step 6/7. `create_revision()` currently copies every item
unconditionally; this rule will need revisiting once issues exist.

**Tested:** `boq/test_versions.py` — `create_revision()` (bill/item
copying, `parent_item` remapping onto the new copies, editing a
revision leaving the source untouched, version numbering), `approve()`
(the literal acceptance test — approving Rev 1 supersedes Original —
plus "only one Approved version" holding across three versions, and
rejecting an approve on a non-Draft), the revise/approve views'
permissions (QS can revise/create a VO but not approve; a Viewer can
do neither), the full VO flow (refused without an Approved BOQ, copies
the approved version, value impact tracks edits, approving the linked
BOQ also approves the VO, VO numbers increment per project), and
`compare_versions` (added/removed/changed/unchanged classification and
value differences, matching the second half of the acceptance test).
88 tests pass overall. Also exercised manually end-to-end over real
HTTP: revise → approve (confirmed Original became Superseded) → create
a VO → add an item to it → approve it (confirmed the VO's status
flipped to Approved and its value impact matched the added item's
amount) → compare two versions (confirmed the diff and value
difference matched what was actually changed).

## Store items, suppliers, stores, and GRN (step 6)

A project's stores live at `/projects/<id>/stores/` (linked from the
project page). Item categories, store items, and suppliers are master/
setup data managed through `/admin/`, the same way Company, Unit of
measure and ProjectMembership already are — the spec doesn't ask for a
bespoke screen to create them, just somewhere for them to live before
a GRN can reference them.

- **Stock balance is never stored, only computed.** Section 5's own
  framing — "the stock balance is always calculated from those
  movements, never typed in" — is implemented literally:
  `StockMovement` is the only place a quantity or cost lives, and
  `StockMovement.current_balance(store, item)` sums every movement's
  signed quantity and its own `total_cost` to get
  `(quantity, value, average_unit_cost)`. Because each movement
  carries *its own* cost rather than a shared running figure, this one
  formula is already the weighted-average calculation Section 8 asks
  for: receiving 100 @ 150.00 then 100 @ 170.00 sums to 200 @ average
  160.00, and — once step 7 adds issues, which will simply be another
  `StockMovement` with a negative quantity at that same average cost —
  issuing 50 will leave exactly 150 @ 160.00, value 24,000.00. Nothing
  about this formula needs to change when issues arrive.
- **GRN — record, then post.** Section 5.2: "the Storekeeper records a
  delivery. Posting adds stock at the supplier's unit cost." A GRN
  starts Draft (header fields, then lines added and removed freely,
  plus file attachments up to 10 MB each), and `GRN.post(user)` is a
  separate, explicit action: it creates one `StockMovement` per line
  (all-or-nothing inside a transaction, same reasoning as the BOQ
  Excel import), and locks the GRN — Section 2's rule "Posted
  documents ... cannot be edited or deleted" is enforced at the view
  level, since there's no reversing-document type to correct a mistake
  with yet (issues/transfers/adjustments are steps 7-8; this gap is
  intentional and disclosed, not silently missing).
- **Who can do what.** "Record goods received (GRN)" (Section 2) is
  Storekeeper-only, narrowed further here to *that store's own*
  assigned storekeeper (`Store.storekeeper`, one person per store per
  Section 5.1) rather than anyone with the Storekeeper role on the
  project — `store.permissions.can_manage_grn`. Viewing is looser:
  any project member can see a store's balance and GRN history, except
  a Storekeeper is scoped to only the store(s) they're assigned to
  (Section 2's "View reports and dashboards ... Storekeeper: Own
  store") — `store.permissions.stores_for_user`. A store another
  Storekeeper doesn't manage isn't just forbidden to them, it's not
  shown at all (404, not 403) — same "don't confirm it exists" pattern
  as everywhere else in this app.

**The step 6 acceptance test** — "GRN of 200 bags cement at 150.00
shows stock 200, value 30,000.00" — is a direct unit test
(`store/tests.py`) and was also run manually end-to-end over real
HTTP: logged in as the store's storekeeper, created a Draft GRN, added
a 200 @ 150.00 line, posted it, and read the resulting stock balance
page — it showed exactly `200.000 t` / `30000.00`, and a follow-up edit
attempt on the now-Posted GRN correctly got a 403. Smoke-test data
cleaned up afterward.

**Tested:** `store/tests.py` — the acceptance test itself, the
weighted-average test from Section 8 (both the receipt-only and the
"issue 50 at the average cost" half, using a plain `StockMovement`
directly since issuing isn't built yet), a zero-movements balance,
and `GRN.post()`'s behavior (creates one movement per line, refuses to
post with no lines, refuses to post twice, locks the GRN).
`store/test_views.py` — the full create → add line → post flow over
the test client, a blocked edit after posting, per-store GRN
numbering (not global), and the visibility/permission rules above
(a Storekeeper sees only their own store and gets a 404 for another
one; a Viewer can see a GRN but not create one; a PM sees every
store). 108 tests pass overall (88 existing + 20 new).

**Not built** (deliberately, later steps per Section 8): requisition,
issue, transfer, return and stock count (step 7-8) — so there's no way
yet to *remove* stock, no reversing document to correct a posting
mistake, and rule 6 ("an item with stock issued can't be deleted in a
later BOQ version") still has nothing to enforce. Material allowances
linking a BOQ item to a store item (step 9) don't exist yet either,
so `StockMovement.boq_item` is wired up but nothing sets it.

## Requisition, issue, return (step 7)

Materials now leave a store, not just arrive. Two new document types
join GRN, plus a request that precedes them:

- **Store requisition is project-scoped, not store-scoped.** Section
  5's own data-model table lists a requisition's fields as "number,
  date, **project**, section, requested by, lines" — no store —
  because the person asking for materials doesn't know which store
  will end up fulfilling the request; that's the Storekeeper's call,
  made later, at issue time. `store.permissions.can_create_requisition`
  restricts creating one to Project Manager or Site Engineer (Section
  2's row for this action), deliberately excluding QS and Storekeeper.
  A requisition's status (Pending / Partly issued / Issued / Rejected)
  is never set by hand except Rejected — `update_status()` recomputes
  it from how much of each line has actually been issued, every time
  an issue against it is posted, and never overwrites a Rejected
  status once it's set.
- **Issue connects to a requisition optionally.** `Issue.requisition`
  is nullable — a Storekeeper can issue directly against a BOQ item
  with no requisition at all, or against one, per Section 5.2's "the
  Storekeeper issues materials against a requisition (or directly,
  with a BOQ item)." Either way, posting is where stock actually
  leaves: `Issue.post(user)` is the exact step 7 acceptance test.
- **The acceptance test, enforced in one place, checked in several.**
  "Issuing 250 bags when stock is 200 is blocked" — every line's
  requested quantity is summed *per item across the whole issue*
  before comparing against `StockMovement.current_balance()`, so two
  lines for the same item in one issue can't sneak past a naive
  line-by-line check. "Issue without a BOQ item is blocked" is
  enforced at three layers, defense-in-depth: `IssueLine.boq_item` has
  no `null=True` at the database level (a plain `NOT NULL` column, so
  even a direct ORM `.update()` can't bypass it — confirmed by test);
  `IssueLineForm`'s `boq_item` field is a required `ModelChoiceField`
  with no blank option, so a line without one never even saves — the
  form re-renders with "This field is required" instead of redirecting;
  and `Issue.post()` re-checks `boq_item_id is None` anyway, the same
  "never trust a single layer" habit used everywhere else in this app.
  Both checks run for *every* line before any `StockMovement` is
  written — one bad line blocks the whole issue, not just itself, same
  all-or-nothing reasoning as GRN posting and the BOQ Excel import.
  The unit cost used for each movement is the store's current
  weighted-average cost, computed *once per item* at the start of
  posting rather than per line, so multiple lines for the same item
  in one issue all see the same cost.
- **Return to store restores stock at the store's current average
  cost**, not whatever cost the material was originally issued at —
  there's nothing recorded anywhere that tracks that, since Section
  5.1 doesn't ask for it on the Issue itself. This is a disclosed
  simplification: a return posted long after the store's average cost
  has moved on will credit stock back in at today's average, not
  yesterday's. `ReturnLine.boq_item` is optional (unlike `IssueLine`'s,
  which is required) — the spec's acceptance test only names issuing,
  not returning, so returning without a BOQ item is allowed. A
  Damaged-condition line still restores quantity (the item is
  physically back in the store); valuing damaged stock differently, or
  writing it off, is future work.
- **Still no reversing-document type.** Same gap as GRN in step 6: a
  Posted issue or return "cannot be edited or deleted" per Section 2,
  and there's still nothing to correct a mistake with except a fresh
  document in the opposite direction (a return to undo an issue,
  another issue to undo an over-generous return) — not a true reversal,
  just the closest tool available until steps 7-8's reversing documents
  (if any are added) or a dedicated correction flow exists.
- **A `return_line_delete` view was added for parity** with GRN's and
  Issue's line-delete, so a Draft return's lines can be removed through
  the UI the same way a Draft GRN's or issue's can — this wasn't
  strictly required by the acceptance test but left it out would have
  been an inconsistency, not a deliberate simplification.

**The step 7 acceptance test** was run manually end-to-end over real
HTTP, in addition to being a unit and view test: logged in as the
store's storekeeper with a store already carrying exactly 200 t of
stock (from a posted GRN, same as step 6), created a Draft issue,
added a line for 250 t against a real BOQ item, and posting was
blocked with exactly `CEM-01-SMK: issuing 250.000 t would exceed the
200.000 t in stock at MAIN.` — stock and issue status were unchanged
afterward. Separately, adding a line with no BOQ item re-rendered the
form with "This field is required" rather than saving anything.
Deleting the bad line, adding a valid 150 t line, and posting
succeeded normally; a return of 50 t against that issue then posted
and brought the balance back to 100 t at the same 150.00 average cost.
The project-scoped requisition flow (create as Pending, reject) was
also exercised as a Project Manager. Smoke-test data cleaned up
afterward.

**Tested:** `store/tests.py` — the acceptance test itself at the model
level (250 blocked, exactly 200 allowed), the database-level rejection
of a null `boq_item`, the average-cost calculation an issue uses
(confirmed to use the *current* average, not a stale GRN cost),
requisition status recomputation across two partial issues, rejecting
a requisition (and refusing to reject twice), and a return restoring
stock at the average cost the store still carried. `store/test_views.py`
— requisition creation restricted to PM/Site Engineer (QS and
Storekeeper blocked), rejecting a requisition over the test client,
the full issue create → add line → post flow, the 250-when-200 block
surfacing as a page error, a missing-BOQ-item line being rejected by
the form, a Storekeeper being blocked from managing issues at a store
they don't keep, and a full return create → add line → post flow. 125
tests pass overall (108 existing + 17 new).

**Not built** (deliberately, later steps per Section 8): transfer
between stores and stock count/adjustment (step 8) — so there's still
no way to move stock between stores without an issue-then-GRN
workaround, and no reversing-document type exists for any Posted
document yet, GRN included. Material allowances linking a BOQ item to
a store item (step 9) still don't exist, so the `boq_item` recorded on
each issue/return movement isn't reconciled against anything yet.

## How project access is scoped (step 2)

Every user who isn't a Django superuser only ever sees the projects
they have a `ProjectMembership` row for — set this up per user per
project in `/admin/`. A superuser (`is_superuser=True`) is treated as
the spec's company-wide "Admin" and sees every project without needing
a membership row for each one. See `core/permissions.py` for exactly
how that's enforced, and `core/tests.py` for the acceptance test (a
Storekeeper on one project gets a 404 on another).

## Why a custom user model on day one?

Section 2 of the spec gives each user a role per project. Django makes
it very costly to switch `AUTH_USER_MODEL` after other tables already
reference the built-in `User`, so `accounts.User` is created now (as a
plain, unmodified subclass of `AbstractUser`) even though the role
fields themselves are step 2's work, not step 1's.

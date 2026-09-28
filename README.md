# BOQ & Store — Road Construction Control App

Django + PostgreSQL backend for the BOQ and Store modules described in
[`docs/requirements.md`](docs/requirements.md). This repo is being built
one step at a time from Section 8 of that spec.

**Current status: Step 5 of 10** — BOQ versions, approval, variation orders, compare.

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

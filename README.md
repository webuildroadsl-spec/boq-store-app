# BOQ & Store — Road Construction Control App

Django + PostgreSQL backend for the BOQ and Store modules described in
[`docs/requirements.md`](docs/requirements.md). This repo is being built
one step at a time from Section 8 of that spec.

**Current status: Step 3 of 10** — BOQ, bills, items; manual entry grid.

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
- `boq/` — BOQ, Bill, BOQItem models; `bill_items_save` (the JSON API
  the grid saves to) and its per-role access rules;
  `static/boq/grid.js` (the manual entry grid's front end).
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
(step 5), Excel import/export (step 4), the full bill summary with
contingency/VAT (step 7's reporting), and material allowances (step 9).

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

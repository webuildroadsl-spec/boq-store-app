# BOQ & Store Modules — Requirements Specification

*Sep 27, 2026 · @Petmartopia*

## 1. Purpose and scope

The first release (MVP) delivers two linked modules for road construction
contractors: **BOQ** (contract quantities and rates) and **Store**
(materials in, out and on hand per site). Their link lets managers see
material used against what the BOQ allows.

**In scope:** projects and sites, BOQ entry and import, BOQ revisions and
variations, store items, goods received, issues to site, transfers,
returns, stock counts, stock balance, material reconciliation against
BOQ, basic reports, user roles.

**Out of scope for now (later phases):** personnel and payroll, plant and
equipment, measurement and interim payment certificates (IPCs),
procurement approvals, accounting.

### How to use this spec with Claude Code

1. Keep this document in the code repository as `docs/requirements.md`.
2. Start each session by asking Claude Code to read it.
3. Build one step from Section 8 at a time, and run its acceptance tests
   before moving on.
4. When a rule changes, update this spec first, then the code.

## 2. Users and roles

Six roles cover the MVP. Each user is assigned a role and one or more
projects, and sees only data for those projects.

| Action | Admin | Project Manager | QS | Site Engineer | Storekeeper | Viewer |
|---|---|---|---|---|---|---|
| Manage users, roles, company settings | Yes | No | No | No | No | No |
| Create and edit projects | Yes | Yes | No | No | No | No |
| Create and edit BOQ | Yes | View | Yes | View | No | View |
| Approve BOQ revision or variation | Yes | Yes | No | No | No | No |
| Set material allowances per BOQ item | Yes | View | Yes | View | No | View |
| Request materials (store requisition) | No | Yes | No | Yes | No | No |
| Record goods received (GRN) | No | No | No | No | Yes | No |
| Issue materials to site | No | No | No | No | Yes | No |
| Approve issue above allowance | Yes | Yes | No | No | No | No |
| Transfers between stores | No | Approve | No | No | Yes | No |
| Stock count and adjustment | No | Approve | No | No | Yes | No |
| View reports and dashboards | Yes | Yes | Yes | Yes | Own store | Yes |

**Rules:**

- A user can hold different roles on different projects.
- No user can approve their own adjustment or over-allowance issue.
- Posted documents (GRN, issue, transfer, adjustment) cannot be edited or
  deleted; mistakes are corrected with a reversing document.

## 3. Shared foundations

Every record belongs to a company and a project; everything below is
built first and reused by both modules.

- **Company** — name, address, logo, default currency, tax settings. One
  company per installation in the MVP; design tables with a `company_id`
  so the system can later serve many contractors.
- **Project** — code (e.g. `BTR-2026`), name, client,
  consultant/engineer, contract number, location, start and end dates,
  contract sum, currency, status (Planning, Active, Suspended, Completed,
  Closed).
- **Section / chainage** — road projects are split by section or
  chainage (e.g. `Ch 0+000 – 2+500`). Fields: project, code, name, start
  chainage, end chainage (km, 3 decimals). BOQ items and store issues can
  be tagged to a section.
- **Unit of measure** — master list: m, m², m³, km, t, kg, L, nr, item,
  sum, day, hr. Each unit has a code, name and type (length, area,
  volume, weight, count, time, lump sum).
- **Currency** — default Sierra Leone Leone (SLE); USD also supported
  because many road contracts are priced in dollars. Each project has one
  contract currency; store costs are recorded in the project currency.
- **Audit trail** — every create, edit, approve and delete records the
  user, timestamp, old value and new value. Audit records cannot be
  edited.
- **Attachments** — any record can hold files (photos, scanned delivery
  notes, PDFs), max 10 MB each, stored off the database.

## 4. BOQ module

The BOQ holds the contract quantities and rates per project, grouped
into bills, and keeps every revision so the original contract can always
be compared with the current one.

### 4.1 Data model

| Table | Key fields |
|---|---|
| BOQ | project, version number, type (Original, Revision, Variation), status (Draft, Submitted, Approved, Superseded), approved by, approved date, notes |
| Bill | BOQ, bill number (e.g. 1), title (e.g. General Items, Earthworks, Sub-base and Base, Surfacing, Drainage, Road Furniture), sort order |
| BOQ item | bill, item reference (e.g. 4.02), description, unit, quantity, rate, amount, section (optional), item type (Measured, Provisional Sum, Prime Cost, Lump Sum, Daywork), parent item (for headings) |
| Variation order | project, VO number, date, description, reason, instructed by, status, linked BOQ version |
| Material allowance | BOQ item, store item, quantity per unit of BOQ item, wastage % (see Section 6) |

### 4.2 Functions

- **Create BOQ manually** — add bills, headings and items in a
  spreadsheet-style grid; keyboard navigation, copy/paste rows.
- **Import BOQ from Excel** — upload `.xlsx`, map columns (ref,
  description, unit, qty, rate) on a preview screen, flag rows with
  errors, then import. Provide a downloadable template.
- **Export BOQ** — to Excel and PDF, in the standard layout with bill
  totals and a summary page.
- **Bill summary** — total per bill, subtotal, provisional sums,
  contingency %, VAT/GST %, grand total.
- **Revise BOQ** — copying an approved BOQ creates a new Draft version;
  approving it marks the old one Superseded.
- **Variation orders** — add, omit or change items; each VO produces a
  new BOQ version and shows its value impact.
- **Compare versions** — side-by-side view of any two versions showing
  added, removed and changed items and the value difference.
- **Search and filter** — by bill, section, description text, item type.

### 4.3 Business rules

1. Amount = quantity × rate, rounded to 2 decimal places. Headings have
   no quantity, rate or amount.
2. Lump sum and provisional sum items may have amount only (quantity 1,
   unit `sum`).
3. Item references must be unique within a BOQ version.
4. Only Draft versions can be edited. Approved versions are read-only.
5. Only one version per project can be Approved (current) at a time.
6. An item that has stock issued against it cannot be deleted in a later
   version; it can only be set to quantity 0 with a reason.
7. Grand total = sum of bill totals + contingency + tax, each shown
   separately.
8. Every approval records who approved it and when.

## 5. Store module

The Store module tracks every material movement through posted
documents, and the stock balance is always calculated from those
movements, never typed in.

### 5.1 Data model

| Table | Key fields |
|---|---|
| Store | project, code, name, location, storekeeper (user), type (Main yard, Site store, Fuel depot) |
| Item category | name (e.g. Aggregates, Cement and Binders, Bitumen, Steel, Pipes and Culverts, Fuel and Lubricants, Road Furniture, Consumables) |
| Store item | code, name, category, unit, minimum stock level, reorder quantity, is fuel (yes/no), active |
| Supplier | name, contact person, phone, email, address |
| Stock movement | date, store, item, quantity (+ in / − out), unit cost, total cost, document type, document ID, BOQ item (optional), section (optional), created by |
| GRN (goods received) | number, date, store, supplier, delivery note number, vehicle number, received by, lines (item, quantity, unit cost), attachments, status |
| Store requisition | number, date, project, section, requested by, lines (item, quantity, BOQ item), status (Pending, Partly issued, Issued, Rejected) |
| Issue | number, date, store, requisition (optional), issued to (person or plant), lines (item, quantity, BOQ item, section), status |
| Transfer | number, date, from store, to store, lines, dispatched by, received by, status (In transit, Received) |
| Return to store | number, date, store, returned by, lines (item, quantity, condition), linked issue |
| Stock count | number, date, store, counted by, lines (item, system qty, counted qty, difference, reason), approved by |

### 5.2 Documents and flow

1. **Requisition** — a Site Engineer requests materials for a BOQ item
   and section.
2. **GRN** — the Storekeeper records a delivery. Posting adds stock at
   the supplier's unit cost.
3. **Issue** — the Storekeeper issues materials against a requisition
   (or directly, with a BOQ item). Posting removes stock.
4. **Transfer** — stock leaves one store on dispatch and arrives at the
   other only when received.
5. **Return** — unused materials come back and are credited to the
   original BOQ item.
6. **Stock count** — physical count; differences post as adjustments
   after Project Manager approval.

### 5.3 Business rules

1. Stock on hand = sum of posted movements for that store and item.
2. An issue or transfer cannot exceed stock on hand (no negative stock).
3. Valuation uses weighted average cost, recalculated on every GRN.
4. Every issue of a construction material must name a BOQ item.
   Consumables and fuel may be issued to a cost heading instead.
5. Document numbers are automatic and sequential per store per year,
   e.g. `GRN-MAIN-2026-0015`.
6. Posted documents are locked; corrections use a reversing document
   linked to the original.
7. Transfers in transit are shown separately and count in neither
   store's stock.
8. Items below minimum stock level appear on the reorder alert list.
9. Dates cannot be in the future; back-dating more than 7 days needs
   Project Manager approval.

## 6. BOQ–Store link: material reconciliation

The system compares material issued to each BOQ item with the quantity
the BOQ allows, and flags overuse before it becomes a loss.

The top row (actual) is recorded by the store: Delivery → GRN posted →
Store stock → Issue to site → BOQ item used. The bottom row (allowed) is
calculated from the BOQ: BOQ quantity → × allowance + wastage → Allowed
qty. The two rows meet at the reconciliation report as "used vs
allowed"; an amount over the allowance needs Project Manager approval.

**Material allowance** — the QS sets, per BOQ item, how much of each
store item one unit of work consumes, plus a wastage percentage.
Example: 1 m³ of Class 20 concrete in culverts = 0.32 t cement, 0.55 m³
sand, 0.85 m³ aggregate, wastage 5%.

**Formulas:**

```
Allowed = Quantity executed (or BOQ quantity) × allowance per unit × (1 + wastage %)
Variance = Net issued - Allowed, where Net issued = Issued - Returned
```

**Rules:**

1. Until measurement is built (Phase 3), the allowed quantity uses the
   BOQ quantity; afterwards it uses quantity executed to date.
2. When a new issue would push net issued above allowed, the system
   warns the Storekeeper and routes the issue to a Project Manager for
   approval with a reason.
3. Variance is shown in quantity and in value (quantity × weighted
   average cost).
4. Variance above 5% shows amber; above 10% shows red. Thresholds are
   set per project.

## 7. Reports, non-functional requirements and tech stack

### 7.1 Reports (MVP)

**Dashboard (per project):** BOQ grand total, value of stock on hand,
value issued this month, top 5 materials over allowance, reorder alerts.

| Report | What it shows | Export |
|---|---|---|
| BOQ summary | Bill totals, contingency, tax, grand total for the current version | PDF, Excel |
| BOQ version comparison | Items added, removed, changed; value difference | PDF, Excel |
| Stock balance | Quantity and value per item per store, as at any date | PDF, Excel |
| Stock ledger (bin card) | Every movement for one item in one store, with running balance | PDF, Excel |
| Material reconciliation | Allowed vs net issued per BOQ item and material, variance, colour flags | PDF, Excel |
| Issues by BOQ item / section | Materials and cost issued to each BOQ item or chainage | Excel |
| Reorder alert | Items below minimum stock | Screen, Excel |
| GRN register | All deliveries by supplier and date | Excel |

### 7.2 Non-functional requirements

- **Mobile-first** — GRN, issue and requisition screens must work on a
  6-inch Android phone.
- **Offline** — storekeepers can create GRNs and issues offline; they
  sync when a connection returns. The server rejects any synced document
  that would cause negative stock and tells the user.
- **Performance** — list pages load in under 3 seconds on a 3G
  connection; a 2,000-line BOQ imports in under 30 seconds.
- **Security** — HTTPS only, hashed passwords, session timeout after 30
  minutes idle, role checks on the server for every request (not only in
  the interface).
- **Backups** — automatic daily database backup kept for 30 days, stored
  off the main server; tested restore once a month.
- **Numbers** — quantities to 3 decimal places, money to 2; all money in
  the project currency.
- **Language** — English interface; dates as DD/MM/YYYY.

### 7.3 Recommended tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python + Django | Built-in login, permissions, admin panel; fits a Python background |
| Database | PostgreSQL | Reliable with money and transactions |
| Frontend | Django templates + HTMX, Tailwind CSS | Simple, fast on weak connections, no separate app to maintain |
| Offline | Progressive Web App (service worker + IndexedDB) for store screens | Works on site without internet |
| Excel / PDF | openpyxl, WeasyPrint | Import/export BOQ and reports |
| Hosting | Linux VPS (Nginx + Gunicorn) or a managed platform | Not dependent on office power or internet |
| Code | Git + GitHub | History and rollback for AI-written code |

## 8. Build order and acceptance tests

Build in ten steps; give Claude Code one step at a time and do not start
the next until every test for the current step passes.

| Step | Build | Done when (acceptance test) |
|---|---|---|
| 1 | Django project, PostgreSQL, GitHub repo, login | You can log in and out; code is on GitHub |
| 2 | Company, project, section, unit, roles and permissions | A Storekeeper on Project A cannot see Project B |
| 3 | BOQ, bills, items; manual entry grid | Item 4.02: 1,250.500 m³ × 185.00 = 231,342.50; bill total updates |
| 4 | BOQ Excel import and export | A real 500-line BOQ imports with totals matching the original Excel to the cent |
| 5 | BOQ versions, approval, variation orders, compare | Approving Rev 1 supersedes Original; compare shows changed items and value difference |
| 6 | Store items, suppliers, stores; GRN | GRN of 200 bags cement at 150.00 shows stock 200, value 30,000.00 |
| 7 | Requisition, issue, return | Issuing 250 bags when stock is 200 is blocked; issue without a BOQ item is blocked |
| 8 | Transfer, stock count, adjustment, reversing documents | Stock in transit counts in neither store; posted GRN cannot be edited |
| 9 | Material allowances and reconciliation report | Allowance 0.32 t/m³, 5% wastage, 100 m³ = 33.6 t allowed; issuing 36 t shows +2.4 t, amber |
| 10 | Reports, dashboard, offline store screens, backups | Offline GRN syncs correctly; restore from last night's backup works |

**Weighted average test (step 6):** receive 100 bags at 150.00, then 100
bags at 170.00 → average cost 160.00; issue 50 → remaining value
24,000.00.

**First prompt for Claude Code:**

> Read docs/requirements.md. We are building step 1 of Section 8 only.
> Set up a Django project with PostgreSQL and user login, explain each
> file you create, and write tests for login. Do not start step 2.

### Open questions to settle with a pilot contractor

- Are BOQ rates and store costs in the same currency on their projects?
- Do they issue fuel from the main store or a separate depot?
- Who approves over-allowance issues when the Project Manager is off
  site?
- Which Excel layout do their current BOQs use?

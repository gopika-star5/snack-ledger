# Snack Ledger

Snack Ledger is an order-to-audit enterprise ledger system tailored for small-scale food manufacturers.
It seamlessly couples field sales, GST-compliant invoicing, and dispatch with recipe-based BOM production, consumption variance tracking, and lot costing.
An append-only stock ledger guarantees that inventory balances and auditor reports (sales, GST, purchase, stock, collections) are continuously reconciled from atomic transactions.

---

## One-Command Setup

Run the application using Docker Compose:

```bash
docker compose up --build
```

Then seed the benchmark master data, opening stock, and demo user accounts:

```bash
docker compose run --rm web python manage.py seed_benchmark
```

The application will be accessible at `http://localhost:8000`.

---

## Test Logins

All demo accounts share the password: `demo1234`

| Username | Role | Device / Scope | Password |
|---|---|---|---|
| `salesman_a` | Salesman | Mobile order capture, own order history, customer-wise performance | `demo1234` |
| `salesman_b` | Salesman | Mobile order capture, own order history, customer-wise performance | `demo1234` |
| `admin1` | Factory Admin | Order review & approval, GST invoicing, dispatch, payments | `demo1234` |
| `prod1` | Production Supervisor | Raw material purchases, production runs, variance tracking | `demo1234` |
| `acct1` | Accountant / Auditor | Read-only auditor reports (GST, sales/purchase registers, stock ledger) | `demo1234` |
| `owner1` | Owner | Executive dashboard, daily KPI summaries, stock & variance alerts | `demo1234` |

---

## Stack and Why

- **Python 3.12 & Django 5.2 LTS:** Provides a battle-tested ORM with transactional row-level locking (`select_for_update`), integrated authentication, and native support for server-side PDF generation via WeasyPrint without client build overhead.
- **PostgreSQL 16:** Guarantees strict ACID transactions, relational foreign-key constraints preventing phantom deletions, and high concurrency control for sequential invoice number generation.
- **HTMX & Tailwind CSS (CDN):** Delivers dynamic, reactive user interactions and a clean mobile-responsive layout for field salesmen without Node.js dependencies, npm toolchains, or JavaScript framework complexity.
- **Docker & Docker Compose:** Encapsulates the complete runtime environment across operating systems, bundling native rendering libraries (libpango, harfbuzz) required for pixel-perfect PDF invoice exports.

---

## Features by Tier

### Must Have
- **Role-Based Authentication & Navigation:** Separate workspaces and automatic home redirects for Salesman, Admin, Production, Accountant, and Owner.
- **Master Data Management:** Items (raw materials, packaging, finished goods), multi-dimensional Units with conversion factors, Customers, Suppliers, and multi-line Bills of Materials (BOM).
- **Mobile Order Entry & Admin Review:** Field order booking with live pricing; Admin approval/rejection with real-time stock shortage indicators.
- **GST Invoicing & PDF Generation:** State-aware tax calculation (CGST + SGST for intra-state vs. IGST for inter-state), financial-year sequential numbering, and downloadable PDF invoices via WeasyPrint.
- **Automated Production Execution:** Production runs automatically decrement raw material and packaging inventory and increment finished goods stock.
- **Planned vs. Actual Variance Tracking:** BOM-calculated expected usage compared against actual issues, flagging variances exceeding configured tolerance thresholds.
- **Ledger-Based Stock Summary & Ledger:** Dynamic stock calculation with complete transaction traceability (`source_type`, `source_id`).
- **Auditor Reports & Data Export:** Sales register, stock summary, and stock movement reports with one-click CSV and Excel (.xlsx) downloads.

### Should Have
- **Procurement & Supplier Records:** Raw material purchase logging linked to suppliers, updating stock and feeding the Purchase Register.
- **Payments & Customer Receivables:** Invoice payment logging, customer outstanding balances, and aged debt breakdown (0–30, 31–60, 61–90, 90+ days).
- **Batch Costing with Overheads:** Real-time production costing combining FIFO material issues with labour and utility overheads to compute per-packet cost and margin.
- **GST Compliance Summary:** Tax summaries structured to map directly to GSTR-1 (outward supplies) and GSTR-3B monthly tax liabilities.
- **Variance Alerts:** Immediate visual warnings when ingredient consumption or batch yield breaches the BOM tolerance percentage (e.g., 3%).
- **Executive Owner Dashboard:** High-level summary of daily sales, production outputs, low-stock warnings, yield variances, and outstanding balances.

### Extras Built
- **Batch & Expiry Tracking with FEFO Dispatch:** Lot-level stock tracking with shelf-life expiry computation; finished goods are dispatched using First-Expiry-First-Out (FEFO), while raw materials use First-In-First-Out (FIFO).
- **Immutable Ledger Enforcement:** `StockMovement` rows are strictly append-only; database updates and deletions are blocked at both model and QuerySet levels (`ImmutableStockMovementError`).
- **Audit Logging:** System-wide `AuditLog` captures user, timestamp, model, action, and JSON snapshot payload for key events.
- **Invoice Cancellation via Credit Notes:** Dispatched invoices are cancelled using formal Credit Notes and compensating stock reversal movements—records are never deleted.
- **Interactive Benchmark Verification System:** Both a CLI command (`python manage.py run_benchmark`) and a Web UI (`/benchmark/`) verify all Section 5 requirements inside an isolated, rolled-back transaction.
- **Client-Side Order Idempotency:** Orders accept client-generated UUIDs (`client_uuid`) to prevent duplicate submissions on unstable mobile connections.
- **WhatsApp Share Link:** Pre-composed WhatsApp sharing link helper for quick invoice communication.

---

## Assumptions

- **HSN Code:** Finished goods (Murukku 200 g) use HSN code `2106` with a standard GST rate of 5%.
- **Opening Finished Goods Cost:** Valued at Rs 30 (3,000 paise) per packet in opening inventory lots.
- **Inventory Valuation Methods:** Raw materials and packaging use First-In-First-Out (FIFO) based on received date. Finished goods use First-Expiry-First-Out (FEFO) based on lot expiry date.
- **Overhead Allocation:** Batch overhead expenses (labour, power/gas) are allocated equally per good packet produced (`total_overhead / actual_good_output`).
- **Damaged Packaging Absorption:** The purchase cost of damaged pouches is absorbed directly into the batch material cost, distributed across the good packets produced.
- **Financial Year Numbering:** Invoice numbers follow the Indian financial year (April 1 to March 31), formatted as `YYYY-YY/NNNN` (e.g., `2026-27/0001`), generated with row-level locks to ensure gapless sequencing.

---

## Benchmark Table

The benchmark reproduces the exact scenario defined in Section 5 of `docs/problem.md`. All 19 checks pass:

| Check | Expected | Actual | Status |
|---|---|---|---|
| Flour closing balance | 120 kg | 120 kg | PASS |
| Cooking oil closing balance | 19.5 L | 19.5 L | PASS |
| Spice mix closing balance | 3.8 kg | 3.8 kg | PASS |
| Pouches closing balance | 306 | 306 | PASS |
| Cartons closing balance | 21 | 21 | PASS |
| Finished Murukku after production | 238 | 238 | PASS |
| Finished Murukku after dispatch | 88 | 88 | PASS |
| Yield variance | -6% (flagged) | -6.00% (flagged) | PASS |
| Oil consumption variance | +5% (flagged) | +5.00% (flagged) | PASS |
| Material cost | 401000 paise | 401000 paise | PASS |
| Cost per packet (with overheads 120000 + 60000) | 3090 paise (Rs 30.90) | 3090 paise (Rs 30.90) | PASS |
| Margin per packet | 1910 paise | 1910 paise | PASS |
| Retailer A invoice taxable | 750000 paise | 750000 paise | PASS |
| Retailer A invoice CGST | 18750 paise | 18750 paise | PASS |
| Retailer A invoice SGST | 18750 paise | 18750 paise | PASS |
| Retailer A invoice total | 787500 paise | 787500 paise | PASS |
| Retailer A outstanding | 387500 paise | 387500 paise | PASS |
| Distributor B order shortage | Ordered 120, Available 88, Shortage 32 | Ordered 120, Available 88, Shortage 32 | PASS |
| Distributor B GST tax rule | IGST applies (Karnataka) | IGST applies (Karnataka) | PASS |

### How to Run the Benchmark

1. **Via CLI Command:**
   ```bash
   docker compose run --rm web python manage.py run_benchmark
   ```
   This executes the complete end-to-end scenario inside an atomic database transaction that is rolled back upon completion, printing the verification results to the terminal.

2. **Via Web Interface:**
   Log in as `admin1` or `owner1` (password `demo1234`) and visit `/benchmark/` or click the **Benchmark** card from the Admin or Owner home screen. Click **Run Benchmark** to view live results with status indicators.

---

## How to Run the Tests

Execute the automated test suite through Docker Compose:

```bash
docker compose run --rm web python manage.py test
```

To run individual test modules:

```bash
# Core ledger, units, and benchmark tests
docker compose run --rm web python manage.py test core.tests

# Production run posting, variance, and views
docker compose run --rm web python manage.py test production.tests

# Sales, invoicing, dispatch, and payments
docker compose run --rm web python manage.py test sales

# Financial, GST, stock, and collection reports
docker compose run --rm web python manage.py test reports
```

---

## Known Gaps

1. **Offline Service Worker / Client Queue:** The order form includes client-generated idempotency tokens (`client_uuid`) to prevent duplicate submissions, but background offline sync using Service Workers and IndexedDB is not implemented; device connectivity is required at the moment of submission.
2. **Dedicated Tally XML Import:** Financial and inventory reports export to standard CSV and Excel (.xlsx) formats, but a dedicated Tally-specific XML schema export is not built.
3. **Automated Carton Unbundling:** Inventory dispatches operate in primary stock units (packets); automatic de-cartoning or dual-unit multi-pack conversions during dispatch require separate manual stock adjustments.
4. **Government e-Invoice Portal Integration:** Invoice data structures, HSN codes, and tax rates comply with GST standards, but direct mock API integration with the NIC/IRP e-Invoice and e-Way Bill portal is not included.
5. **Automated Backorder Splitting:** Orders with insufficient inventory flag shortages during admin review, but automatic splitting into partial shipments and backorder lines is not handled automatically.

from datetime import date, timedelta
from decimal import Decimal
import io
import uuid
import openpyxl

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from core.ledger import get_balance, receive_stock, issue_stock
from core.models import Customer, Item, Supplier, Unit, StockMovement
from production.models import BOM, BOMLine, ProductionRun, RunIssue
from production.services import post_run
from sales.models import Order, OrderLine, Invoice, Payment
from sales.services import create_invoice

User = get_user_model()


class ReportsSystemTests(TestCase):
    def setUp(self):
        call_command('seed_benchmark', verbosity=0)

        self.salesman_a = User.objects.get(username='salesman_a')
        self.admin_user = User.objects.get(username='admin1')
        self.prod_user = User.objects.get(username='prod1')
        self.acct_user = User.objects.get(username='acct1')
        self.owner_user = User.objects.get(username='owner1')

        self.retailer_a = Customer.objects.get(name='Retailer A')
        self.distributor_b = Customer.objects.get(name='Distributor B')
        self.murukku = Item.objects.get(name='Murukku 200 g')
        self.flour = Item.objects.get(name='Flour')
        self.supplier = Supplier.objects.get(name='Demo Flour Mills')
        self.kg = Unit.objects.get(code='kg')

        # Set up clients
        self.salesman_client = Client()
        self.salesman_client.login(username='salesman_a', password='demo1234')

        self.prod_client = Client()
        self.prod_client.login(username='prod1', password='demo1234')

        self.acct_client = Client()
        self.acct_client.login(username='acct1', password='demo1234')

        self.admin_client = Client()
        self.admin_client.login(username='admin1', password='demo1234')

        self.owner_client = Client()
        self.owner_client.login(username='owner1', password='demo1234')

        # ── Create Benchmark Order & Invoice (Retailer A: 150 pkts @ ₹50 = ₹7,500 + 5% GST = ₹7,875) ──
        self.order = Order.objects.create(
            client_uuid=uuid.uuid4(),
            customer=self.retailer_a,
            salesman=self.salesman_a,
            status=Order.STATUS_APPROVED,
        )
        self.order_line = OrderLine.objects.create(
            order=self.order,
            item=self.murukku,
            qty=Decimal('150'),
            price_paise=5000,
        )
        self.benchmark_invoice = create_invoice(self.order, user=self.admin_user)

        # ── Create Benchmark Purchase (60 kg flour @ ₹45/kg = ₹2,700) ──
        from production.models import Purchase, PurchaseLine
        self.purchase = Purchase.objects.create(
            supplier=self.supplier,
            date=date.today(),
            total_paise=270000,
            created_by=self.admin_user,
            note="Benchmark Flour Purchase",
        )
        self.purchase_line = PurchaseLine.objects.create(
            purchase=self.purchase,
            item=self.flour,
            qty_in_purchase_unit=Decimal('60'),
            purchase_unit=self.kg,
            qty_in_stock_unit=Decimal('60'),
            unit_cost_paise=4500,
            line_total_paise=270000,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Requirement: Role-Based Access Control
    # ─────────────────────────────────────────────────────────────────────────

    def test_salesman_gets_403_on_all_reports(self):
        """salesman_a gets 403 forbidden on Reports page and all report views."""
        urls = [
            reverse('reports_index'),
            reverse('sales_register'),
            reverse('sales_summary'),
            reverse('gst_summary'),
            reverse('purchase_register'),
            reverse('stock_summary'),
            reverse('stock_ledger'),
            reverse('production_report'),
            reverse('collections_outstanding'),
            reverse('owner_dashboard'),
        ]
        for url in urls:
            response = self.salesman_client.get(url)
            self.assertEqual(response.status_code, 403, f"Expected 403 for salesman_a on {url}")

    def test_production_gets_403_on_all_reports(self):
        """production supervisor (prod1) gets 403 on all report views."""
        urls = [
            reverse('reports_index'),
            reverse('sales_register'),
            reverse('sales_summary'),
            reverse('gst_summary'),
            reverse('purchase_register'),
            reverse('stock_summary'),
            reverse('stock_ledger'),
            reverse('production_report'),
            reverse('collections_outstanding'),
            reverse('owner_dashboard'),
        ]
        for url in urls:
            response = self.prod_client.get(url)
            self.assertEqual(response.status_code, 403, f"Expected 403 for prod1 on {url}")

    def test_accountant_admin_owner_get_200_on_all_reports(self):
        """accountant, admin, and owner can access all report views."""
        urls = [
            reverse('reports_index'),
            reverse('sales_register'),
            reverse('sales_summary'),
            reverse('gst_summary'),
            reverse('purchase_register'),
            reverse('stock_summary'),
            reverse('stock_ledger'),
            reverse('production_report'),
            reverse('collections_outstanding'),
        ]
        for client in [self.acct_client, self.admin_client, self.owner_client]:
            for url in urls:
                response = client.get(url)
                self.assertEqual(response.status_code, 200, f"Expected 200 on {url}")

    def test_owner_dashboard_accessible_by_owner_and_admin(self):
        """Owner dashboard returns 200 for owner and admin, 403 for accountant."""
        url = reverse('owner_dashboard')
        self.assertEqual(self.owner_client.get(url).status_code, 200)
        self.assertEqual(self.admin_client.get(url).status_code, 200)
        # accountant should NOT see owner dashboard
        self.assertEqual(self.acct_client.get(url).status_code, 403)

    # ─────────────────────────────────────────────────────────────────────────
    # Requirement: Sales Register
    # ─────────────────────────────────────────────────────────────────────────

    def test_sales_register_shows_benchmark_invoice_total_rupees(self):
        """sales register HTML shows invoice total ₹7,875.00 for the benchmark invoice."""
        response = self.acct_client.get(reverse('sales_register'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "₹7,875.00")
        self.assertContains(response, self.benchmark_invoice.number)
        self.assertContains(response, "Retailer A")
        self.assertContains(response, "₹7,500.00")  # Taxable
        self.assertContains(response, "₹187.50")    # CGST
        self.assertContains(response, "₹187.50")    # SGST

    def test_sales_register_csv_export_contains_7875(self):
        """The CSV export opens and contains 7875.00 for that invoice."""
        response = self.acct_client.get(reverse('sales_register') + '?export=csv')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        content = response.content.decode('utf-8')
        self.assertTrue(
            "7875.00" in content or "787500" in content,
            f"Expected 7875.00 or 787500 in CSV export. Content: {content}"
        )
        self.assertIn(self.benchmark_invoice.number, content)
        self.assertIn("Retailer A", content)

    def test_sales_register_excel_export(self):
        """The Excel export opens with openpyxl and has valid spreadsheet content."""
        response = self.acct_client.get(reverse('sales_register') + '?export=excel')
        self.assertEqual(response.status_code, 200)
        self.assertIn('spreadsheetml', response['Content-Type'])
        wb = openpyxl.load_workbook(io.BytesIO(response.content))
        self.assertIn("Sales Register", wb.sheetnames)
        ws = wb["Sales Register"]
        found_invoice = False
        found_total = False
        for row in ws.iter_rows(values_only=True):
            if self.benchmark_invoice.number in row:
                found_invoice = True
                if 7875.0 in row or 7875 in row or "7875.00" in [str(c) for c in row]:
                    found_total = True
        self.assertTrue(found_invoice, "Benchmark invoice number not found in Excel sheet")
        self.assertTrue(found_total, "Benchmark total (7875) not found in invoice row in Excel sheet")

    # ─────────────────────────────────────────────────────────────────────────
    # Requirement: GST Summary
    # ─────────────────────────────────────────────────────────────────────────

    def test_gst_summary_shows_cgst_and_sgst_187_50(self):
        """GST summary shows CGST 187.50 and SGST 187.50."""
        response = self.acct_client.get(reverse('gst_summary'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn("187.50", content)
        self.assertContains(response, "5.00%")
        self.assertContains(response, "₹7,500.00")

    def test_gst_summary_csv_and_excel_export(self):
        """GST summary exports to CSV and Excel properly."""
        csv_resp = self.admin_client.get(reverse('gst_summary') + '?export=csv')
        self.assertEqual(csv_resp.status_code, 200)
        csv_text = csv_resp.content.decode('utf-8')
        self.assertIn("187.50", csv_text)

        excel_resp = self.admin_client.get(reverse('gst_summary') + '?export=excel')
        self.assertEqual(excel_resp.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(excel_resp.content))
        self.assertIn("GST Summary", wb.sheetnames)

    # ─────────────────────────────────────────────────────────────────────────
    # Requirement: Sales Summary
    # ─────────────────────────────────────────────────────────────────────────

    def test_sales_summary_customer_and_product(self):
        """Customer-wise and product-wise sales summary displays properly."""
        response = self.owner_client.get(reverse('sales_summary'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Retailer A")
        self.assertContains(response, "Murukku 200 g")
        self.assertContains(response, "₹7,875.00")

        csv_resp = self.owner_client.get(reverse('sales_summary') + '?export=csv')
        self.assertEqual(csv_resp.status_code, 200)
        csv_text = csv_resp.content.decode('utf-8')
        self.assertIn("Retailer A", csv_text)
        self.assertIn("Murukku 200 g", csv_text)

        excel_resp = self.owner_client.get(reverse('sales_summary') + '?export=excel')
        self.assertEqual(excel_resp.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(excel_resp.content))
        self.assertIn("Customer Sales Summary", wb.sheetnames)
        self.assertIn("Product Sales Summary", wb.sheetnames)

    # ─────────────────────────────────────────────────────────────────────────
    # Requirement: Purchase Register
    # ─────────────────────────────────────────────────────────────────────────

    def test_purchase_register_view_and_exports(self):
        """Purchase register displays benchmark purchase and exports properly."""
        response = self.acct_client.get(reverse('purchase_register'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Demo Flour Mills")
        self.assertContains(response, "Flour")
        self.assertContains(response, "₹2,700.00")

        csv_resp = self.acct_client.get(reverse('purchase_register') + '?export=csv')
        self.assertEqual(csv_resp.status_code, 200)
        csv_text = csv_resp.content.decode('utf-8')
        self.assertIn("Demo Flour Mills", csv_text)
        self.assertIn("2700.00", csv_text)

        excel_resp = self.acct_client.get(reverse('purchase_register') + '?export=excel')
        self.assertEqual(excel_resp.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(excel_resp.content))
        self.assertIn("Purchase Register", wb.sheetnames)

    # ─────────────────────────────────────────────────────────────────────────
    # Requirement: Date-range filter
    # ─────────────────────────────────────────────────────────────────────────

    def test_date_range_filter(self):
        """Date range filter excludes records outside range."""
        past_from = (date.today() - timedelta(days=30)).isoformat()
        past_to = (date.today() - timedelta(days=20)).isoformat()

        response = self.acct_client.get(reverse('sales_register') + f'?from_date={past_from}&to_date={past_to}')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.benchmark_invoice.number)

        today_str = date.today().isoformat()
        response2 = self.acct_client.get(reverse('sales_register') + f'?from_date={today_str}&to_date={today_str}')
        self.assertEqual(response2.status_code, 200)
        self.assertContains(response2, self.benchmark_invoice.number)

    # ─────────────────────────────────────────────────────────────────────────
    # Requirement: Reports Page Connected to Home Pages
    # ─────────────────────────────────────────────────────────────────────────

    def test_home_pages_contain_reports_link(self):
        """accountant, admin, and owner home pages have links/cards to Reports."""
        acct_resp = self.acct_client.get(reverse('accountant_home'))
        self.assertEqual(acct_resp.status_code, 200)
        self.assertContains(acct_resp, reverse('reports_index'))

        admin_resp = self.admin_client.get(reverse('admin_home'))
        self.assertEqual(admin_resp.status_code, 200)
        self.assertContains(admin_resp, reverse('reports_index'))

        owner_resp = self.owner_client.get(reverse('owner_home'))
        self.assertEqual(owner_resp.status_code, 200)
        self.assertContains(owner_resp, reverse('reports_index'))

    # ─────────────────────────────────────────────────────────────────────────
    # NEW: Stock Summary Tests
    # ─────────────────────────────────────────────────────────────────────────

    def test_stock_summary_closing_equals_get_balance(self):
        """
        For every item, stock summary closing must equal get_balance(item).
        Tests the core invariant: closing = SUM(StockMovement.qty) for each item.
        """
        response = self.acct_client.get(reverse('stock_summary'))
        self.assertEqual(response.status_code, 200)

        # Verify the view context rows
        rows = response.context['rows']
        self.assertTrue(len(rows) > 0, "Stock summary should have rows")

        for row in rows:
            item = row['item']
            live_balance = get_balance(item)
            self.assertEqual(
                row['closing'],
                live_balance,
                f"Closing for {item.name} is {row['closing']} but get_balance returns {live_balance}"
            )

    def test_stock_summary_flour_closing_balance(self):
        """
        Flour closing balance should be 100 (opening) from seed.
        When a purchase movement is added it should update.
        """
        flour = self.flour
        # Seed has 100 kg opening. The setUp creates a Purchase object but
        # not via receive_stock (the purchase fixture does not call ledger).
        # So ledger balance for flour = 100 kg (opening only from seed).
        balance = get_balance(flour)
        response = self.acct_client.get(reverse('stock_summary'))
        rows = {r['item'].pk: r for r in response.context['rows']}
        self.assertIn(flour.pk, rows)
        self.assertEqual(rows[flour.pk]['closing'], balance)

    def test_stock_summary_csv_export(self):
        """Stock summary CSV export works and contains item names."""
        response = self.acct_client.get(reverse('stock_summary') + '?export=csv')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        content = response.content.decode('utf-8')
        self.assertIn('Flour', content)
        self.assertIn('Murukku 200 g', content)

    def test_stock_summary_excel_export(self):
        """Stock summary Excel export opens correctly."""
        response = self.acct_client.get(reverse('stock_summary') + '?export=excel')
        self.assertEqual(response.status_code, 200)
        self.assertIn('spreadsheetml', response['Content-Type'])
        wb = openpyxl.load_workbook(io.BytesIO(response.content))
        self.assertIn("Stock Summary", wb.sheetnames)

    # ─────────────────────────────────────────────────────────────────────────
    # NEW: Stock Ledger Tests
    # ─────────────────────────────────────────────────────────────────────────

    def _create_production_run(self):
        """Helper: create and post a benchmark production run."""
        bom = BOM.objects.get(finished_item=self.murukku)
        flour = Item.objects.get(name='Flour')
        oil = Item.objects.get(name='Cooking oil')
        spice = Item.objects.get(name='Salt and spice mix')

        # Receive flour purchase first (so stock is available)
        receive_stock(flour, Decimal('60'), 4500, 'purchase', 'test-purchase', self.admin_user)

        run = ProductionRun.objects.create(
            bom=bom,
            batches=Decimal('1'),
            run_date=date.today(),
            actual_good_output=Decimal('188'),
            damaged_pouches=Decimal('6'),
            cartons_used=Decimal('9'),
            created_by=self.prod_user,
        )
        RunIssue.objects.create(run=run, item=flour, issued_qty=Decimal('40'))
        RunIssue.objects.create(run=run, item=oil, issued_qty=Decimal('10.5'))
        RunIssue.objects.create(run=run, item=spice, issued_qty=Decimal('1.2'))
        return post_run(run, user=self.prod_user)

    def test_stock_ledger_shows_production_movements(self):
        """Stock ledger shows movements with source_type='production'."""
        self._create_production_run()

        response = self.acct_client.get(reverse('stock_ledger'))
        self.assertEqual(response.status_code, 200)

        movements = response.context['movements']
        prod_movements = [m for m in movements if m.source_type == 'production']
        self.assertTrue(len(prod_movements) > 0, "Stock ledger should contain production movements")

        # Flour should have been issued
        flour_out = [m for m in prod_movements if m.item.name == 'Flour' and m.qty < 0]
        self.assertTrue(len(flour_out) > 0, "Flour issue in production should appear in ledger")

    def test_stock_ledger_item_filter(self):
        """Stock ledger item filter reduces results to only that item."""
        self._create_production_run()
        flour = self.flour
        response = self.acct_client.get(reverse('stock_ledger') + f'?item_id={flour.pk}')
        self.assertEqual(response.status_code, 200)
        movements = response.context['movements']
        for m in movements:
            self.assertEqual(m.item_id, flour.pk, "All movements should be for Flour when filtered")

    def test_stock_ledger_csv_and_excel_export(self):
        """Stock ledger exports work."""
        self._create_production_run()
        csv_resp = self.acct_client.get(reverse('stock_ledger') + '?export=csv')
        self.assertEqual(csv_resp.status_code, 200)
        content = csv_resp.content.decode('utf-8')
        self.assertIn('Flour', content)

        excel_resp = self.acct_client.get(reverse('stock_ledger') + '?export=excel')
        self.assertEqual(excel_resp.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(excel_resp.content))
        self.assertIn("Stock Ledger", wb.sheetnames)

    # ─────────────────────────────────────────────────────────────────────────
    # NEW: Production Report Tests
    # ─────────────────────────────────────────────────────────────────────────

    def test_production_report_yield_variance_flagged(self):
        """
        Production report shows yield variance -6% flagged for the benchmark run.
        Benchmark: 200 expected, 188 actual → (188-200)/200 * 100 = -6%
        BOM tolerance = 3%, so this must be flagged.
        """
        self._create_production_run()

        response = self.acct_client.get(reverse('production_report'))
        self.assertEqual(response.status_code, 200)

        rows = response.context['rows']
        self.assertTrue(len(rows) > 0, "Production report should have at least one run")

        benchmark_row = rows[0]  # most recent first
        self.assertTrue(benchmark_row['yield_flagged'], "Yield should be flagged for -6% variance")
        self.assertEqual(
            benchmark_row['yield_variance_percent'],
            Decimal('-6.00'),
            f"Expected -6.00% yield variance, got {benchmark_row['yield_variance_percent']}"
        )

    def test_production_report_csv_and_excel_export(self):
        """Production report exports work."""
        self._create_production_run()

        csv_resp = self.acct_client.get(reverse('production_report') + '?export=csv')
        self.assertEqual(csv_resp.status_code, 200)
        content = csv_resp.content.decode('utf-8')
        self.assertIn('-6.00', content)
        self.assertIn('Yes', content)  # Yield Flagged

        excel_resp = self.acct_client.get(reverse('production_report') + '?export=excel')
        self.assertEqual(excel_resp.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(excel_resp.content))
        self.assertIn("Production Report", wb.sheetnames)

    # ─────────────────────────────────────────────────────────────────────────
    # NEW: Collections & Outstanding Tests
    # ─────────────────────────────────────────────────────────────────────────

    def test_collections_outstanding_shows_payment_and_ageing(self):
        """
        Outstanding = ₹387,500 paise when benchmark invoice (₹787,500) has ₹400,000 payment.
        Test: total_outstanding_paise = 787500 - 400000 = 387500 for Retailer A.
        """
        # Record payment of ₹4,000 (400000 paise)
        Payment.objects.create(
            invoice=self.benchmark_invoice,
            customer=self.retailer_a,
            amount_paise=400000,
            date=date.today(),
            mode='bank_transfer',
        )

        response = self.acct_client.get(reverse('collections_outstanding'))
        self.assertEqual(response.status_code, 200)

        # Total outstanding for Retailer A: 787500 - 400000 = 387500
        customer_rows = response.context['customer_rows']
        retailer_a_row = next((r for r in customer_rows if r['customer'].name == 'Retailer A'), None)
        self.assertIsNotNone(retailer_a_row, "Retailer A should appear in outstanding rows")
        self.assertEqual(
            retailer_a_row['total_outstanding'],
            387500,
            f"Outstanding for Retailer A should be 387500 paise, got {retailer_a_row['total_outstanding']}"
        )

    def test_collections_outstanding_total_collected(self):
        """Collections total equals sum of payments in period."""
        Payment.objects.create(
            invoice=self.benchmark_invoice,
            customer=self.retailer_a,
            amount_paise=400000,
            date=date.today(),
            mode='bank_transfer',
        )
        response = self.acct_client.get(reverse('collections_outstanding'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_collected_paise'], 400000)

    def test_collections_outstanding_csv_and_excel_export(self):
        """Collections & outstanding exports work."""
        Payment.objects.create(
            invoice=self.benchmark_invoice,
            customer=self.retailer_a,
            amount_paise=400000,
            date=date.today(),
        )
        csv_resp = self.acct_client.get(reverse('collections_outstanding') + '?export=csv')
        self.assertEqual(csv_resp.status_code, 200)
        content = csv_resp.content.decode('utf-8')
        self.assertIn('Retailer A', content)
        self.assertIn('4000.00', content)

        excel_resp = self.acct_client.get(reverse('collections_outstanding') + '?export=excel')
        self.assertEqual(excel_resp.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(excel_resp.content))
        self.assertIn("Collections", wb.sheetnames)
        self.assertIn("Outstanding", wb.sheetnames)

    # ─────────────────────────────────────────────────────────────────────────
    # NEW: Owner Dashboard Tests
    # ─────────────────────────────────────────────────────────────────────────

    def test_owner_dashboard_shows_outstanding(self):
        """
        Owner dashboard shows outstanding 387500 paise when benchmark invoice
        has a 400000 payment (787500 - 400000 = 387500).
        """
        Payment.objects.create(
            invoice=self.benchmark_invoice,
            customer=self.retailer_a,
            amount_paise=400000,
            date=date.today(),
        )
        response = self.owner_client.get(reverse('owner_dashboard'))
        self.assertEqual(response.status_code, 200)
        # Outstanding = 387500 paise = ₹3,875.00
        self.assertEqual(
            response.context['total_outstanding_paise'],
            387500,
            f"Owner dashboard should show 387500 paise outstanding, got {response.context['total_outstanding_paise']}"
        )
        self.assertContains(response, "₹3,875.00")

    def test_owner_dashboard_stock_levels(self):
        """Owner dashboard shows stock levels for finished goods and raw materials."""
        response = self.owner_client.get(reverse('owner_dashboard'))
        self.assertEqual(response.status_code, 200)
        finished = response.context['finished_stock']
        raw = response.context['raw_stock']
        self.assertTrue(len(finished) > 0)
        self.assertTrue(len(raw) > 0)

    def test_owner_dashboard_variance_alerts_after_flagged_run(self):
        """Owner dashboard shows variance alerts when flagged runs exist."""
        self._create_production_run()
        response = self.owner_client.get(reverse('owner_dashboard'))
        self.assertEqual(response.status_code, 200)
        flagged = response.context['flagged_runs']
        self.assertTrue(len(flagged) > 0, "Should have at least one flagged run in dashboard")

    def test_owner_dashboard_today_orders(self):
        """Owner dashboard shows today's orders count."""
        response = self.owner_client.get(reverse('owner_dashboard'))
        self.assertEqual(response.status_code, 200)
        # The setUp creates one order
        self.assertGreaterEqual(response.context['todays_orders_count'], 1)

    def test_reports_index_shows_all_new_report_cards(self):
        """Reports index page contains cards for all 5 new reports."""
        response = self.acct_client.get(reverse('reports_index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stock Summary")
        self.assertContains(response, "Stock Ledger")
        self.assertContains(response, "Production Report")
        self.assertContains(response, "Collections")
        # Owner dashboard is in the index but accountant will see it listed
        self.assertContains(response, "Owner Dashboard")

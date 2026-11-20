from datetime import date, timedelta
from decimal import Decimal
import io
import uuid
import openpyxl

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from core.models import Customer, Item, Supplier, Unit
from production.models import Purchase, PurchaseLine
from sales.models import Order, OrderLine, Invoice
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
    # "Access: accountant is read-only; admin and owner see all; salesman and production get 403."
    # ─────────────────────────────────────────────────────────────────────────

    def test_salesman_gets_403_on_all_reports(self):
        """salesman_a gets 403 forbidden on Reports page and all report views."""
        urls = [
            reverse('reports_index'),
            reverse('sales_register'),
            reverse('sales_summary'),
            reverse('gst_summary'),
            reverse('purchase_register'),
        ]
        for url in urls:
            response = self.salesman_client.get(url)
            self.assertEqual(response.status_code, 403, f"Expected 403 for salesman_a on {url}")

    def test_production_gets_403_on_all_reports(self):
        """production supervisor (prod1) gets 403 on Reports page and all report views."""
        urls = [
            reverse('reports_index'),
            reverse('sales_register'),
            reverse('sales_summary'),
            reverse('gst_summary'),
            reverse('purchase_register'),
        ]
        for url in urls:
            response = self.prod_client.get(url)
            self.assertEqual(response.status_code, 403, f"Expected 403 for prod1 on {url}")

    def test_accountant_admin_owner_get_200_on_all_reports(self):
        """accountant, admin, and owner can access Reports page and all report views."""
        urls = [
            reverse('reports_index'),
            reverse('sales_register'),
            reverse('sales_summary'),
            reverse('gst_summary'),
            reverse('purchase_register'),
        ]
        for client in [self.acct_client, self.admin_client, self.owner_client]:
            for url in urls:
                response = client.get(url)
                self.assertEqual(response.status_code, 200, f"Expected 200 on {url}")

    # ─────────────────────────────────────────────────────────────────────────
    # Requirement: Sales Register
    # "Tests: sales register shows invoice total ₹7,875.00 for the benchmark invoice;
    #  the CSV export opens and contains 787500 paise or 7875.00 for that invoice"
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
        """The CSV export opens and contains 787500 paise or 7875.00 for that invoice."""
        response = self.acct_client.get(reverse('sales_register') + '?export=csv')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        content = response.content.decode('utf-8')
        # Check that either 787500 or 7875.00 is present
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
        # Find cell with invoice number and total
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
    # Requirement: GST Summary of Outward Supplies
    # "Tests: GST summary shows CGST 187.50 and SGST 187.50"
    # ─────────────────────────────────────────────────────────────────────────

    def test_gst_summary_shows_cgst_and_sgst_187_50(self):
        """GST summary shows CGST 187.50 and SGST 187.50."""
        response = self.acct_client.get(reverse('gst_summary'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn("187.50", content)
        self.assertContains(response, "187.50")
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
    # Requirement: Customer-wise & Product-wise Sales Summary
    # ─────────────────────────────────────────────────────────────────────────

    def test_sales_summary_customer_and_product(self):
        """Customer-wise and product-wise sales summary displays properly."""
        response = self.owner_client.get(reverse('sales_summary'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Retailer A")
        self.assertContains(response, "Murukku 200 g")
        self.assertContains(response, "₹7,875.00")

        # CSV Export
        csv_resp = self.owner_client.get(reverse('sales_summary') + '?export=csv')
        self.assertEqual(csv_resp.status_code, 200)
        csv_text = csv_resp.content.decode('utf-8')
        self.assertIn("Retailer A", csv_text)
        self.assertIn("Murukku 200 g", csv_text)

        # Excel Export
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

        # CSV Export
        csv_resp = self.acct_client.get(reverse('purchase_register') + '?export=csv')
        self.assertEqual(csv_resp.status_code, 200)
        csv_text = csv_resp.content.decode('utf-8')
        self.assertIn("Demo Flour Mills", csv_text)
        self.assertIn("2700.00", csv_text)

        # Excel Export
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

        # Invoices should not be in the past range
        response = self.acct_client.get(reverse('sales_register') + f'?from_date={past_from}&to_date={past_to}')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.benchmark_invoice.number)

        # Invoices should be in current date range
        today_str = date.today().isoformat()
        response2 = self.acct_client.get(reverse('sales_register') + f'?from_date={today_str}&to_date={today_str}')
        self.assertEqual(response2.status_code, 200)
        self.assertContains(response2, self.benchmark_invoice.number)

    # ─────────────────────────────────────────────────────────────────────────
    # Requirement: Reports Page Connected to Home Pages
    # ─────────────────────────────────────────────────────────────────────────

    def test_home_pages_contain_reports_link(self):
        """accountant, admin, and owner home pages have links/cards to Reports."""
        # Accountant home
        acct_resp = self.acct_client.get(reverse('accountant_home'))
        self.assertEqual(acct_resp.status_code, 200)
        self.assertContains(acct_resp, reverse('reports_index'))

        # Admin home
        admin_resp = self.admin_client.get(reverse('admin_home'))
        self.assertEqual(admin_resp.status_code, 200)
        self.assertContains(admin_resp, reverse('reports_index'))

        # Owner home
        owner_resp = self.owner_client.get(reverse('owner_home'))
        self.assertEqual(owner_resp.status_code, 200)
        self.assertContains(owner_resp, reverse('reports_index'))

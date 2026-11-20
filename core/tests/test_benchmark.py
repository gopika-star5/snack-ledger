import datetime
from decimal import Decimal
import io
import uuid

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from core.benchmark import run_benchmark, execute_benchmark
from core.ledger import get_balance, receive_stock
from core.models import Customer, Item, Supplier, Unit
from production.models import BOM, ProductionRun, Purchase, PurchaseLine, RunIssue, RunOverhead, RunVarianceLine
from production.services import post_run
from sales.models import Order, OrderLine, Invoice, Dispatch
from sales.services import calculate_tax, create_invoice, customer_outstanding, dispatch_invoice, record_payment

User = get_user_model()


class BenchmarkEndToEndTest(TestCase):
    """
    End-to-end benchmark scenario from docs/problem.md section 5:
    Runs the benchmark steps in order through the existing service functions
    and asserts every expected value.
    """

    def setUp(self):
        call_command('seed_benchmark', verbosity=0)
        self.system_user = User.objects.get(username='system')
        self.admin_user = User.objects.get(username='admin1')
        self.owner_user = User.objects.get(username='owner1')
        self.salesman_a = User.objects.get(username='salesman_a')
        self.salesman_b = User.objects.get(username='salesman_b')

        self.retailer_a = Customer.objects.get(name='Retailer A')
        self.distributor_b = Customer.objects.get(name='Distributor B')
        self.supplier = Supplier.objects.get(name='Demo Flour Mills')

        self.flour = Item.objects.get(name='Flour')
        self.oil = Item.objects.get(name='Cooking oil')
        self.spice = Item.objects.get(name='Salt and spice mix')
        self.pouch = Item.objects.get(name='200 g pouch')
        self.carton = Item.objects.get(name='Carton')
        self.murukku = Item.objects.get(name='Murukku 200 g')

        self.bom = BOM.objects.get(finished_item=self.murukku)
        self.kg = Unit.objects.get(code='kg')

    def test_run_benchmark(self):
        """Test the run_benchmark function imported from core.benchmark."""
        results = run_benchmark()
        self.assertEqual(len(results), 19)
        for check in results:
            self.assertTrue(
                check.passed,
                f"Check '{check.name}' failed. Expected: {check.expected}, Actual: {check.actual}"
            )

    def test_benchmark_scenario_end_to_end(self):
        # ── Initial Opening Balances Verification ───────────────────────────
        self.assertEqual(get_balance(self.flour), Decimal('100'))
        self.assertEqual(get_balance(self.oil), Decimal('30'))
        self.assertEqual(get_balance(self.spice), Decimal('5'))
        self.assertEqual(get_balance(self.pouch), Decimal('500'))
        self.assertEqual(get_balance(self.carton), Decimal('30'))
        self.assertEqual(get_balance(self.murukku), Decimal('50'))

        # ── Step 1: Purchase 60 kg flour at 4500 paise (Rs 45/kg) ────────────
        purchase = Purchase.objects.create(
            supplier=self.supplier,
            date=datetime.date.today(),
            created_by=self.system_user,
        )
        qty_purchased = Decimal('60')
        rate_paise = 4500
        line_total = int(qty_purchased * rate_paise)
        PurchaseLine.objects.create(
            purchase=purchase,
            item=self.flour,
            qty_in_purchase_unit=qty_purchased,
            purchase_unit=self.kg,
            qty_in_stock_unit=qty_purchased,
            unit_cost_paise=rate_paise,
            line_total_paise=line_total,
        )
        purchase.total_paise = line_total
        purchase.save()

        receive_stock(
            item=self.flour,
            qty=qty_purchased,
            unit_cost_paise=rate_paise,
            source_type='purchase',
            source_id=str(purchase.pk),
            user=self.system_user,
        )

        # Flour balance right after purchase = 100 + 60 = 160 kg
        self.assertEqual(get_balance(self.flour), Decimal('160'))

        # ── Step 2: Production run of Murukku 200 g ─────────────────────────
        # Issue 40 kg flour, 10.5 L oil (0.5 L over standard), 1.2 kg spice mix
        # Output: 188 good packets, 6 damaged pouches, 9 cartons used
        run = ProductionRun.objects.create(
            bom=self.bom,
            batches=Decimal('1'),
            run_date=datetime.date.today(),
            actual_good_output=Decimal('188'),
            damaged_pouches=Decimal('6'),
            cartons_used=Decimal('9'),
            created_by=self.system_user,
        )
        RunIssue.objects.create(run=run, item=self.flour, issued_qty=Decimal('40'))
        RunIssue.objects.create(run=run, item=self.oil, issued_qty=Decimal('10.5'))
        RunIssue.objects.create(run=run, item=self.spice, issued_qty=Decimal('1.2'))

        # Post the run through production service function
        post_run(run, self.system_user)
        run.refresh_from_db()

        # Check stock balances after production run:
        # flour 120 kg (100 + 60 - 40)
        self.assertEqual(get_balance(self.flour), Decimal('120'))
        # cooking oil 19.5 L (30 - 10.5)
        self.assertEqual(get_balance(self.oil), Decimal('19.5'))
        # spice mix 3.8 kg (5 - 1.2)
        self.assertEqual(get_balance(self.spice), Decimal('3.8'))
        # pouches 306 (500 - (188 + 6))
        self.assertEqual(get_balance(self.pouch), Decimal('306'))
        # cartons 21 (30 - 9)
        self.assertEqual(get_balance(self.carton), Decimal('21'))
        # finished Murukku 238 after production (50 + 188)
        self.assertEqual(get_balance(self.murukku), Decimal('238'))

        # Yield variance: 188 vs 200 expected, -6 percent, flagged
        self.assertEqual(run.yield_variance_percent, Decimal('-6.00'))
        self.assertTrue(run.yield_flagged)

        # Oil variance: 10.5 vs 10.0 expected, +5 percent, flagged
        oil_variance = RunVarianceLine.objects.get(run=run, item=self.oil)
        self.assertEqual(oil_variance.variance_percent, Decimal('5.00'))
        self.assertTrue(oil_variance.flagged)
        self.assertEqual(oil_variance.direction, 'adverse')

        # Material cost: 401000 paise
        # (40*4200 + 10.5*14000 + 1.2*20000 + 194*250 + 9*1500 = 401000)
        self.assertEqual(run.material_cost_paise, 401000)

        # Overheads: labour 120000 + gas/power 60000
        RunOverhead.objects.create(run=run, description='Labour', amount_paise=120000)
        RunOverhead.objects.create(run=run, description='Gas and power', amount_paise=60000)

        # Total cost = 401000 + 180000 = 581000 paise
        # Cost per packet is 3090 paise (Rs 30.90) and margin 1910 paise (5000 - 3090)
        self.assertEqual(run.overhead_cost_paise, 180000)
        self.assertEqual(run.total_cost_paise, 581000)
        self.assertEqual(run.cost_per_packet_paise, 3090)
        self.assertEqual(run.cost_per_packet_rupees, '30.90')
        self.assertEqual(run.margin_paise, 1910)

        # ── Step 3: Salesman A places order for Retailer A: 150 packets ─────
        order_a = Order.objects.create(
            client_uuid=uuid.uuid4(),
            customer=self.retailer_a,
            salesman=self.salesman_a,
            status=Order.STATUS_SUBMITTED,
        )
        line_a = OrderLine.objects.create(
            order=order_a,
            item=self.murukku,
            qty=Decimal('150'),
            price_paise=5000,
        )
        self.assertEqual(line_a.total_paise, 750000)

        # ── Step 4: Admin approves the order ─────────────────────────────────
        avail_murukku = get_balance(self.murukku)
        order_a.has_shortage = line_a.qty > avail_murukku
        order_a.status = Order.STATUS_APPROVED
        order_a.save()
        self.assertFalse(order_a.has_shortage)

        # ── Step 5: Admin raises GST invoice (CGST + SGST) ───────────────────
        invoice_a = create_invoice(order_a, user=self.admin_user)
        self.assertEqual(invoice_a.status, Invoice.STATUS_POSTED)
        # Retailer A invoice: taxable 750000, CGST 18750, SGST 18750, total 787500 paise
        self.assertEqual(invoice_a.taxable_paise, 750000)
        self.assertEqual(invoice_a.cgst_paise, 18750)
        self.assertEqual(invoice_a.sgst_paise, 18750)
        self.assertEqual(invoice_a.igst_paise, 0)
        self.assertEqual(invoice_a.total_paise, 787500)

        # ── Step 6: Dispatch ─────────────────────────────────────────────────
        dispatch = dispatch_invoice(invoice_a, user=self.admin_user)
        self.assertEqual(dispatch.status, Dispatch.STATUS_DISPATCHED)
        # finished Murukku 88 after dispatch (238 - 150 = 88)
        self.assertEqual(get_balance(self.murukku), Decimal('88'))

        # ── Step 7: Record payment of 400000 paise from Retailer A ──────────
        payment = record_payment(
            invoice=invoice_a,
            amount_paise=400000,
            mode='bank_transfer',
            reference='TXN-BENCHMARK-4000',
            user=self.admin_user,
        )
        self.assertIsNotNone(payment)
        # outstanding for Retailer A is 387500 paise (787500 - 400000 = 387500)
        self.assertEqual(customer_outstanding(self.retailer_a), 387500)

        # ── Step 8: Salesman B places order for Distributor B: 120 packets ───
        order_b = Order.objects.create(
            client_uuid=uuid.uuid4(),
            customer=self.distributor_b,
            salesman=self.salesman_b,
            status=Order.STATUS_SUBMITTED,
        )
        line_b = OrderLine.objects.create(
            order=order_b,
            item=self.murukku,
            qty=Decimal('120'),
            price_paise=5000,
        )

        # Distributor B ordered 120, available 88, shortage 32
        dist_b_ordered = line_b.qty
        dist_b_available = get_balance(self.murukku)
        dist_b_shortage = max(Decimal('0'), dist_b_ordered - dist_b_available)

        self.assertEqual(dist_b_ordered, Decimal('120'))
        self.assertEqual(dist_b_available, Decimal('88'))
        self.assertEqual(dist_b_shortage, Decimal('32'))

        # IGST applies (Karnataka customer, Tamil Nadu company)
        self.assertEqual(self.distributor_b.state, 'Karnataka')
        tax_calc_b = calculate_tax(self.distributor_b.state, line_b.total_paise, self.murukku.gst_rate_percent)
        self.assertEqual(tax_calc_b.taxable_paise, 600000)
        self.assertEqual(tax_calc_b.cgst_paise, 0)
        self.assertEqual(tax_calc_b.sgst_paise, 0)
        self.assertEqual(tax_calc_b.igst_paise, 30000)
        self.assertEqual(tax_calc_b.total_paise, 630000)


class RunBenchmarkCommandTest(TestCase):
    """
    Tests the management command "run_benchmark" that runs inside a rolled-back transaction.
    """

    def test_run_benchmark_command_output(self):
        out = io.StringIO()
        call_command('run_benchmark', stdout=out)
        output = out.getvalue()

        # Check table headers and key results
        self.assertIn("Check", output)
        self.assertIn("Expected", output)
        self.assertIn("Actual", output)
        self.assertIn("PASS", output)
        self.assertIn("Flour closing balance", output)
        self.assertIn("Cooking oil closing balance", output)
        self.assertIn("Yield variance", output)
        self.assertIn("Material cost", output)
        self.assertIn("Retailer A invoice total", output)
        self.assertIn("Distributor B order shortage", output)
        self.assertIn("All 19 benchmark checks PASSED", output)


class BenchmarkViewAccessTest(TestCase):
    """
    Tests the Benchmark page access control and rendering:
    - admin and owner have access (200 OK)
    - other roles (salesman, production, accountant) receive 403 Forbidden
    - anonymous users are redirected to login
    - admin and owner home pages have links to the benchmark
    """

    def setUp(self):
        call_command('seed_benchmark', verbosity=0)
        self.client = Client()

    def test_admin_can_access_benchmark_page(self):
        self.client.login(username='admin1', password='demo1234')
        response = self.client.get(reverse('benchmark'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "System Benchmark")
        self.assertContains(response, "Run Benchmark")
        self.assertContains(response, "PASS")

    def test_owner_can_access_benchmark_page(self):
        self.client.login(username='owner1', password='demo1234')
        response = self.client.get(reverse('benchmark'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "System Benchmark")
        self.assertContains(response, "PASS")

    def test_salesman_gets_403(self):
        self.client.login(username='salesman_a', password='demo1234')
        response = self.client.get(reverse('benchmark'))
        self.assertEqual(response.status_code, 403)

    def test_production_gets_403(self):
        self.client.login(username='prod1', password='demo1234')
        response = self.client.get(reverse('benchmark'))
        self.assertEqual(response.status_code, 403)

    def test_accountant_gets_403(self):
        self.client.login(username='acct1', password='demo1234')
        response = self.client.get(reverse('benchmark'))
        self.assertEqual(response.status_code, 403)

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse('benchmark'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_admin_home_has_benchmark_card(self):
        self.client.login(username='admin1', password='demo1234')
        response = self.client.get(reverse('admin_home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('benchmark'))
        self.assertContains(response, "Benchmark")

    def test_owner_home_has_benchmark_card(self):
        self.client.login(username='owner1', password='demo1234')
        response = self.client.get(reverse('owner_home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('benchmark'))
        self.assertContains(response, "Benchmark")

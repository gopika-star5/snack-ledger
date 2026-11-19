from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.ledger import get_balance, receive_stock
from core.models import Item, Supplier, Unit
from production.models import BOM, ProductionRun, Purchase, PurchaseLine, RunIssue, RunVarianceLine
from production.services import post_run

User = get_user_model()


class RunViewsAccessControlTest(TestCase):
    """Only the production and admin roles can open these pages."""

    def setUp(self):
        call_command('seed_benchmark', verbosity=0)
        self.murukku = Item.objects.get(name='Murukku 200 g')
        self.bom = BOM.objects.get(finished_item=self.murukku)
        self.system_user = User.objects.get(username='system')

        # Create a sample posted run to test run_result access
        self.sample_run = ProductionRun.objects.create(
            bom=self.bom,
            batches=Decimal('1'),
            run_date=date.today(),
            actual_good_output=Decimal('188'),
            damaged_pouches=Decimal('6'),
            cartons_used=Decimal('9'),
            created_by=self.system_user,
        )
        flour = Item.objects.get(name='Flour')
        oil = Item.objects.get(name='Cooking oil')
        spice = Item.objects.get(name='Salt and spice mix')
        RunIssue.objects.create(run=self.sample_run, item=flour, issued_qty=Decimal('40'))
        RunIssue.objects.create(run=self.sample_run, item=oil, issued_qty=Decimal('10'))
        RunIssue.objects.create(run=self.sample_run, item=spice, issued_qty=Decimal('1.2'))
        post_run(self.sample_run, self.system_user)

        self.urls = [
            reverse('run_list'),
            reverse('run_create'),
            reverse('run_result', kwargs={'pk': self.sample_run.pk}),
        ]

    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        for url in self.urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, f"Expected 302 for anonymous user on {url}")
            self.assertIn('/login/', response.url)

    def test_unauthorized_roles_forbidden(self):
        unauthorized_users = ['salesman_a', 'acct1', 'owner1']
        for username in unauthorized_users:
            self.client.login(username=username, password='demo1234')
            for url in self.urls:
                response = self.client.get(url)
                self.assertEqual(
                    response.status_code,
                    403,
                    f"User '{username}' should get 403 on {url}, got {response.status_code}",
                )
            self.client.logout()

    def test_production_role_allowed(self):
        self.client.login(username='prod1', password='demo1234')
        for url in self.urls:
            response = self.client.get(url)
            self.assertEqual(
                response.status_code,
                200,
                f"Production user should get 200 on {url}, got {response.status_code}",
            )

    def test_admin_role_allowed(self):
        self.client.login(username='admin1', password='demo1234')
        for url in self.urls:
            response = self.client.get(url)
            self.assertEqual(
                response.status_code,
                200,
                f"Admin user should get 200 on {url}, got {response.status_code}",
            )


class FullFormFlowBenchmarkTest(TestCase):
    """Test full form flow using the benchmark values."""

    def setUp(self):
        call_command('seed_benchmark', verbosity=0)
        self.prod_user = User.objects.get(username='prod1')
        self.flour = Item.objects.get(name='Flour')
        self.oil = Item.objects.get(name='Cooking oil')
        self.spice = Item.objects.get(name='Salt and spice mix')
        self.pouch = Item.objects.get(name='200 g pouch')
        self.carton = Item.objects.get(name='Carton')
        self.murukku = Item.objects.get(name='Murukku 200 g')

        # Benchmark Step 1: Purchase 60 kg flour at 4500 paise
        supplier = Supplier.objects.get(name='Demo Flour Mills')
        kg = Unit.objects.get(code='kg')
        purchase = Purchase.objects.create(
            supplier=supplier,
            date=date.today(),
            created_by=self.prod_user,
        )
        PurchaseLine.objects.create(
            purchase=purchase,
            item=self.flour,
            qty_in_purchase_unit=Decimal('60'),
            purchase_unit=kg,
            qty_in_stock_unit=Decimal('60'),
            unit_cost_paise=4500,
            line_total_paise=270000,
        )
        purchase.total_paise = 270000
        purchase.save()
        receive_stock(self.flour, Decimal('60'), 4500, 'purchase', str(purchase.pk), self.prod_user)

    def test_full_benchmark_production_run_flow(self):
        self.client.login(username='prod1', password='demo1234')

        # 1. GET New Run page
        create_url = reverse('run_create')
        get_res = self.client.get(create_url)
        self.assertEqual(get_res.status_code, 200)
        content = get_res.content.decode()
        self.assertIn('Murukku 200 g', content)
        self.assertIn('Flour', content)
        self.assertIn('Cooking oil', content)
        self.assertIn('Salt and spice mix', content)

        # 2. POST New Run with Benchmark scenario values:
        #    Batches: 1
        #    Flour: 40 kg, Oil: 10.5 L (0.5 L over standard), Spice: 1.2 kg
        #    Actual output: 188 packets, Damaged pouches: 6, Cartons: 9
        post_data = {
            'batches': '1',
            'run_date': str(date.today()),
            f'issue_{self.flour.id}': '40',
            f'issue_{self.oil.id}': '10.5',
            f'issue_{self.spice.id}': '1.2',
            'actual_good_output': '188',
            'damaged_pouches': '6',
            'cartons_used': '9',
        }

        post_res = self.client.post(create_url, post_data)
        self.assertEqual(post_res.status_code, 302)

        # Verify run was created and posted
        run = ProductionRun.objects.latest('id')
        self.assertEqual(run.status, 'posted')
        self.assertEqual(post_res.url, reverse('run_result', kwargs={'pk': run.pk}))

        # 3. Check benchmark inventory balances
        # Flour: 100 opening + 60 purchase - 40 issued = 120 kg
        self.assertEqual(get_balance(self.flour), Decimal('120'))
        # Oil: 30 opening - 10.5 issued = 19.5 L
        self.assertEqual(get_balance(self.oil), Decimal('19.5'))
        # Spice: 5 opening - 1.2 issued = 3.8 kg
        self.assertEqual(get_balance(self.spice), Decimal('3.8'))
        # Pouches: 500 opening - (188 used + 6 damaged) = 306
        self.assertEqual(get_balance(self.pouch), Decimal('306'))
        # Cartons: 30 opening - 9 used = 21
        self.assertEqual(get_balance(self.carton), Decimal('21'))
        # Finished Murukku: 50 opening + 188 produced = 238 packets
        self.assertEqual(get_balance(self.murukku), Decimal('238'))

        # 4. Check benchmark variances
        # Yield variance: 188 vs 200 = -6.00%, flagged = True
        self.assertEqual(run.yield_variance_percent, Decimal('-6.00'))
        self.assertTrue(run.yield_flagged)

        # Oil variance: 10.5 vs 10 = +5.00%, flagged = True, direction = adverse
        oil_var = RunVarianceLine.objects.get(run=run, item=self.oil)
        self.assertEqual(oil_var.variance_percent, Decimal('5.00'))
        self.assertTrue(oil_var.flagged)
        self.assertEqual(oil_var.direction, 'adverse')

        # 5. Check Result page presentation
        result_res = self.client.get(reverse('run_result', kwargs={'pk': run.pk}))
        self.assertEqual(result_res.status_code, 200)
        result_content = result_res.content.decode()

        self.assertIn('Production Run #', result_content)
        self.assertIn('188', result_content)
        self.assertIn('5.00%', result_content)
        self.assertIn('-6.00%', result_content)
        self.assertIn('-10.00%', result_content)
        self.assertIn('Flagged', result_content)
        self.assertIn('Favourable', result_content)
        self.assertIn('Yield (Murukku 200 g)', result_content)

        # 6. Check List page presentation
        list_res = self.client.get(reverse('run_list'))
        self.assertEqual(list_res.status_code, 200)
        list_content = list_res.content.decode()
        self.assertIn(f'#{run.id}', list_content)
        self.assertIn('Murukku 200 g', list_content)
        self.assertIn('posted', list_content.lower())

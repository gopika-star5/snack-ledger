import datetime
from decimal import Decimal
from django.test import TestCase
from django.contrib.auth.models import User, Group
from django.urls import reverse
from django.core.management import call_command
from core.models import Item
from core.services import get_owner_summary_data
from production.models import BOM, ProductionRun, RunIssue
from production.services import post_run
from core.ledger import receive_stock


class OwnerHomeOperationsStatusTests(TestCase):
    def setUp(self):
        call_command('seed_benchmark', verbosity=0)
        self.owner_user = User.objects.get(username='owner1')
        self.client.force_login(self.owner_user)
        self.flour = Item.objects.get(name='Flour')
        self.murukku = Item.objects.get(name='Murukku 200 g')

    def test_operations_healthy_by_default(self):
        """No flagged runs and no low stock -> Healthy in green."""
        data = get_owner_summary_data()
        self.assertTrue(data['operations_healthy'])
        self.assertEqual(data['alert_count'], 0)

        response = self.client.get(reverse('owner_home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Healthy")
        self.assertContains(response, "text-emerald-400")
        self.assertNotContains(response, "Needs attention")

    def test_operations_needs_attention_with_flagged_production_run(self):
        """Flagged production run -> Needs attention with alert count in amber."""
        admin_user = User.objects.get(username='admin1')
        prod_user = User.objects.get(username='prod1')
        bom = BOM.objects.get(finished_item=self.murukku)
        oil = Item.objects.get(name='Cooking oil')
        spice = Item.objects.get(name='Salt and spice mix')

        receive_stock(self.flour, Decimal('60'), 4500, 'purchase', 'test-p', admin_user)

        run = ProductionRun.objects.create(
            bom=bom,
            batches=Decimal('1'),
            run_date=datetime.date.today(),
            actual_good_output=Decimal('188'),  # -6% variance, exceeds 3% tolerance
            damaged_pouches=Decimal('6'),
            cartons_used=Decimal('0'),
            created_by=prod_user,
        )
        RunIssue.objects.create(run=run, item=self.flour, issued_qty=Decimal('40'))
        RunIssue.objects.create(run=run, item=oil, issued_qty=Decimal('10.5'))
        RunIssue.objects.create(run=run, item=spice, issued_qty=Decimal('1.2'))
        post_run(run, user=prod_user)

        data = get_owner_summary_data()
        self.assertFalse(data['operations_healthy'])
        self.assertEqual(data['alert_count'], 1)

        response = self.client.get(reverse('owner_home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Needs attention")
        self.assertContains(response, "(1)")
        self.assertContains(response, "text-amber-400")
        self.assertNotContains(response, "● Healthy")

    def test_operations_needs_attention_with_low_stock(self):
        """Item balance at or below reorder level -> Needs attention in amber."""
        self.flour.reorder_level = Decimal('500')
        self.flour.save()

        data = get_owner_summary_data()
        self.assertFalse(data['operations_healthy'])
        self.assertEqual(data['alert_count'], 1)

        response = self.client.get(reverse('owner_home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Needs attention")
        self.assertContains(response, "(1)")
        self.assertContains(response, "text-amber-400")

    def test_operations_needs_attention_multiple_alerts_summed(self):
        """Both flagged run and low stock -> count = 2."""
        admin_user = User.objects.get(username='admin1')
        prod_user = User.objects.get(username='prod1')
        bom = BOM.objects.get(finished_item=self.murukku)
        oil = Item.objects.get(name='Cooking oil')
        spice = Item.objects.get(name='Salt and spice mix')

        receive_stock(self.flour, Decimal('60'), 4500, 'purchase', 'test-p2', admin_user)
        run = ProductionRun.objects.create(
            bom=bom,
            batches=Decimal('1'),
            run_date=datetime.date.today(),
            actual_good_output=Decimal('188'),
            damaged_pouches=Decimal('6'),
            cartons_used=Decimal('0'),
            created_by=prod_user,
        )
        RunIssue.objects.create(run=run, item=self.flour, issued_qty=Decimal('40'))
        RunIssue.objects.create(run=run, item=oil, issued_qty=Decimal('10.5'))
        RunIssue.objects.create(run=run, item=spice, issued_qty=Decimal('1.2'))
        post_run(run, user=prod_user)

        self.flour.reorder_level = Decimal('500')
        self.flour.save()

        data = get_owner_summary_data()
        self.assertFalse(data['operations_healthy'])
        self.assertEqual(data['alert_count'], 2)

        response = self.client.get(reverse('owner_home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Needs attention")
        self.assertContains(response, "(2)")

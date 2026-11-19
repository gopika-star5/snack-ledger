from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase

from core.ledger import get_balance, receive_stock
from core.models import AuditLog, Item, StockLot, Supplier, Unit
from production.models import BOM, ProductionRun, Purchase, PurchaseLine, RunIssue, RunVarianceLine
from production.services import post_run

User = get_user_model()


class PostRunTest(TestCase):
    def setUp(self):
        call_command('seed_benchmark', verbosity=0)
        self.user = User.objects.get(username='system')
        self.flour = Item.objects.get(name='Flour')
        self.oil = Item.objects.get(name='Cooking oil')
        self.spice = Item.objects.get(name='Salt and spice mix')
        self.pouch = Item.objects.get(name='200 g pouch')
        self.carton = Item.objects.get(name='Carton')
        self.murukku = Item.objects.get(name='Murukku 200 g')
        self.bom = BOM.objects.get(finished_item=self.murukku)

    def test_post_run_benchmark(self):
        # 1. Purchase 60 kg flour at 4500 paise
        supplier = Supplier.objects.get(name='Demo Flour Mills')
        purchase = Purchase.objects.create(
            supplier=supplier,
            date=date.today(),
            created_by=self.user,
        )
        kg = Unit.objects.get(code='kg')
        qty = Decimal('60')
        rate = 4500
        line_total = int(qty * rate)
        PurchaseLine.objects.create(
            purchase=purchase,
            item=self.flour,
            qty_in_purchase_unit=qty,
            purchase_unit=kg,
            qty_in_stock_unit=qty,
            unit_cost_paise=rate,
            line_total_paise=line_total,
        )
        purchase.total_paise = line_total
        purchase.save()
        receive_stock(self.flour, qty, rate, 'purchase', str(purchase.pk), self.user)

        # 2. Production run: issues 40 kg flour, 10.5 L oil, 1.2 kg spice mix;
        #    good output 188; 6 damaged pouches; 9 cartons.
        run = ProductionRun.objects.create(
            bom=self.bom,
            batches=Decimal('1'),
            run_date=date.today(),
            actual_good_output=Decimal('188'),
            damaged_pouches=Decimal('6'),
            cartons_used=Decimal('9'),
            created_by=self.user,
        )
        RunIssue.objects.create(run=run, item=self.flour, issued_qty=Decimal('40'))
        RunIssue.objects.create(run=run, item=self.oil, issued_qty=Decimal('10.5'))
        RunIssue.objects.create(run=run, item=self.spice, issued_qty=Decimal('1.2'))

        # 3. Post run
        post_run(run, self.user)
        run.refresh_from_db()

        # Assert balances
        self.assertEqual(get_balance(self.flour), Decimal('120'))
        self.assertEqual(get_balance(self.oil), Decimal('19.5'))
        self.assertEqual(get_balance(self.spice), Decimal('3.8'))
        self.assertEqual(get_balance(self.pouch), Decimal('306'))
        self.assertEqual(get_balance(self.carton), Decimal('21'))
        self.assertEqual(get_balance(self.murukku), Decimal('238'))

        # Assert yield variance: -6% flagged
        self.assertEqual(run.yield_variance_percent, Decimal('-6.00'))
        self.assertTrue(run.yield_flagged)

        # Assert oil variance: +5% flagged
        oil_variance = RunVarianceLine.objects.get(run=run, item=self.oil)
        self.assertEqual(oil_variance.variance_percent, Decimal('5.00'))
        self.assertTrue(oil_variance.flagged)
        self.assertEqual(oil_variance.direction, 'adverse')

        # Assert material cost 401000 paise
        self.assertEqual(run.material_cost_paise, 401000)

        # Assert status posted
        self.assertEqual(run.status, 'posted')

        # Assert cannot be posted again
        with self.assertRaises(ValidationError):
            post_run(run, self.user)

        # Assert cannot be edited
        with self.assertRaises(ValidationError):
            run.actual_good_output = Decimal('190')
            run.save()

        # Assert AuditLog entry exists
        self.assertTrue(
            AuditLog.objects.filter(model_name='productionrun', object_id=str(run.pk)).exists()
        )

        # Assert StockLot for finished item has batch_no like MUR-YYYYMMDD-N and expiry
        lot = StockLot.objects.filter(
            item=self.murukku,
            source_type='production',
            source_id=str(run.pk),
        ).first()
        self.assertIsNotNone(lot)
        today_str = date.today().strftime('%Y%m%d')
        self.assertTrue(lot.batch_no.startswith(f"MUR-{today_str}"))
        self.assertEqual(lot.unit_cost_paise, 2133)

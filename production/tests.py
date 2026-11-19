from decimal import Decimal
from datetime import date, timedelta
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.management import call_command
from core.ledger import get_balance, receive_stock
from core.models import Item, Unit, Supplier
from production.models import Purchase, PurchaseLine

User = get_user_model()


class SeedBenchmarkTest(TestCase):
    def setUp(self):
        call_command('seed_benchmark', verbosity=0)

    def test_flour_balance(self):
        flour = Item.objects.get(name='Flour')
        self.assertEqual(get_balance(flour), Decimal('100'))

    def test_oil_balance(self):
        oil = Item.objects.get(name='Cooking oil')
        self.assertEqual(get_balance(oil), Decimal('30'))

    def test_spice_balance(self):
        spice = Item.objects.get(name='Salt and spice mix')
        self.assertEqual(get_balance(spice), Decimal('5'))

    def test_pouch_balance(self):
        pouch = Item.objects.get(name='200 g pouch')
        self.assertEqual(get_balance(pouch), Decimal('500'))

    def test_carton_balance(self):
        carton = Item.objects.get(name='Carton')
        self.assertEqual(get_balance(carton), Decimal('30'))

    def test_murukku_balance(self):
        murukku = Item.objects.get(name='Murukku 200 g')
        self.assertEqual(get_balance(murukku), Decimal('50'))

    def test_seed_idempotent(self):
        # Running seed twice should not change balances
        call_command('seed_benchmark', verbosity=0)
        flour = Item.objects.get(name='Flour')
        self.assertEqual(get_balance(flour), Decimal('100'))


class PurchaseFlourTest(TestCase):
    def setUp(self):
        call_command('seed_benchmark', verbosity=0)

    def test_flour_balance_after_purchase(self):
        """After a purchase of 60 kg flour at 4500 paise, flour balance = 160."""
        flour = Item.objects.get(name='Flour')
        supplier = Supplier.objects.get(name='Demo Flour Mills')
        user = User.objects.get(username='system')
        kg = Unit.objects.get(code='kg')

        # Create purchase
        purchase = Purchase.objects.create(
            supplier=supplier,
            date=date.today(),
            created_by=user,
        )
        qty = Decimal('60')
        unit_cost = 4500
        line_total = int(qty * unit_cost)
        line = PurchaseLine.objects.create(
            purchase=purchase,
            item=flour,
            qty_in_purchase_unit=qty,
            purchase_unit=kg,
            qty_in_stock_unit=qty,  # kg -> kg, no conversion needed
            unit_cost_paise=unit_cost,
            line_total_paise=line_total,
        )
        purchase.total_paise = line_total
        purchase.save()

        # Call receive_stock
        from core.ledger import receive_stock
        receive_stock(flour, qty, unit_cost, 'purchase', str(purchase.pk), user)

        self.assertEqual(get_balance(flour), Decimal('160'))

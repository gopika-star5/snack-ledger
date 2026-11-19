from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand

from core.ledger import receive_stock
from core.models import CompanySettings, Customer, Item, StockMovement, Supplier, Unit
from production.models import BOM, BOMLine

User = get_user_model()


class Command(BaseCommand):
    help = "Seed benchmark data: items, opening stock, customers, supplier, company settings"

    def handle(self, *args, **options):
        verbosity = options.get('verbosity', 1)

        # ── System user ──────────────────────────────────────────────────────
        system_user, _ = User.objects.get_or_create(
            username='system',
            defaults={'is_staff': True},
        )

        # ── Units ────────────────────────────────────────────────────────────
        kg = Unit.objects.get(code='kg')
        L = Unit.objects.get(code='L')
        piece = Unit.objects.get(code='piece')
        packet = Unit.objects.get(code='packet')

        # ── Items ────────────────────────────────────────────────────────────
        flour, _ = Item.objects.get_or_create(
            name='Flour',
            defaults={'item_type': 'raw', 'stock_unit': kg},
        )
        oil, _ = Item.objects.get_or_create(
            name='Cooking oil',
            defaults={'item_type': 'raw', 'stock_unit': L},
        )
        spice, _ = Item.objects.get_or_create(
            name='Salt and spice mix',
            defaults={'item_type': 'raw', 'stock_unit': kg},
        )
        pouch, _ = Item.objects.get_or_create(
            name='200 g pouch',
            defaults={'item_type': 'packaging', 'stock_unit': piece},
        )
        carton, _ = Item.objects.get_or_create(
            name='Carton',
            defaults={'item_type': 'packaging', 'stock_unit': piece},
        )
        murukku, _ = Item.objects.get_or_create(
            name='Murukku 200 g',
            defaults={
                'item_type': 'finished',
                'stock_unit': packet,
                'hsn_code': '2106',
                'gst_rate_percent': 5,
                'selling_price_paise': 5000,
                'shelf_life_days': 90,
            },
        )

        if verbosity >= 1:
            self.stdout.write('Items ready.')

        # ── Murukku BOM (idempotent) ──────────────────────────────────────────
        bom, created = BOM.objects.get_or_create(
            finished_item=murukku,
            defaults={
                'batch_yield': Decimal('200'),
                'tolerance_percent': Decimal('3'),
            },
        )
        if not created:
            bom.batch_yield = Decimal('200')
            bom.tolerance_percent = Decimal('3')
            bom.save()

        bom_lines = [
            (flour, Decimal('40'), kg),
            (oil, Decimal('10'), L),
            (spice, Decimal('1.2'), kg),
            (pouch, Decimal('200'), piece),
            (carton, Decimal('10'), piece),
        ]
        for b_item, qty, b_unit in bom_lines:
            BOMLine.objects.update_or_create(
                bom=bom,
                item=b_item,
                defaults={
                    'qty_per_batch': qty,
                    'unit': b_unit,
                },
            )

        if verbosity >= 1:
            self.stdout.write('Murukku BOM ready.')

        # ── Opening stock (idempotent) ────────────────────────────────────────
        opening_items = [
            (flour,   '100', 4200,  None),
            (oil,     '30',  14000, None),
            (spice,   '5',   20000, None),
            (pouch,   '500', 250,   None),
            (carton,  '30',  1500,  None),
            (murukku, '50',  3000,  date.today() + timedelta(days=60)),
        ]

        for item, qty_str, cost, expiry in opening_items:
            already = StockMovement.objects.filter(
                item=item,
                source_type='opening',
                source_id='opening',
            ).exists()
            if not already:
                receive_stock(
                    item=item,
                    qty=Decimal(qty_str),
                    unit_cost_paise=cost,
                    source_type='opening',
                    source_id='opening',
                    user=system_user,
                    expiry_date=expiry,
                )
                if verbosity >= 1:
                    self.stdout.write(f'  Opening stock added: {item.name} x {qty_str}')
            else:
                if verbosity >= 1:
                    self.stdout.write(f'  Opening stock already exists: {item.name}')

        # ── Customers ────────────────────────────────────────────────────────
        Customer.objects.get_or_create(
            name='Retailer A',
            defaults={
                'customer_type': 'retailer',
                'state': 'Tamil Nadu',
                'gstin': '33AAAAA0000A1Z5',
            },
        )
        Customer.objects.get_or_create(
            name='Distributor B',
            defaults={
                'customer_type': 'distributor',
                'state': 'Karnataka',
                'gstin': '29BBBBB0000B1Z5',
            },
        )

        # ── Supplier ─────────────────────────────────────────────────────────
        Supplier.objects.get_or_create(
            name='Demo Flour Mills',
            defaults={'state': 'Tamil Nadu'},
        )

        # ── Company settings ─────────────────────────────────────────────────
        settings = CompanySettings.get()
        settings.name = 'Snack Factory'
        settings.gstin = '33CCCCC0000C1Z5'
        settings.state = 'Tamil Nadu'
        settings.save()

        if verbosity >= 1:
            self.stdout.write('Company settings updated.')

        # ── Demo users ───────────────────────────────────────────────────────
        call_command('create_demo_users', verbosity=verbosity)

        self.stdout.write(self.style.SUCCESS('seed_benchmark complete.'))

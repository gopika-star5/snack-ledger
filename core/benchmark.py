from __future__ import annotations

import datetime
from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, transaction

from core.ledger import get_balance, receive_stock
from core.models import Customer, CompanySettings, Item, Supplier, Unit, StockMovement, StockLot
from production.models import BOM, ProductionRun, Purchase, PurchaseLine, RunIssue, RunOverhead, RunVarianceLine
from production.services import post_run
from sales.models import Order, OrderLine, Invoice, InvoiceLine, Dispatch, Payment, CreditNote
from sales.services import calculate_tax, create_invoice, dispatch_invoice, customer_outstanding, record_payment

User = get_user_model()


class BenchmarkCheck(tuple):
    """
    Represents a single benchmark check result.
    Supports unpacking as (name, expected, actual, passed),
    attribute access (.name, .expected, .actual, .passed, .check, .status),
    and dict-like access (['name'], ['expected'], ['actual'], ['passed'], ['check'], ['status']).
    """
    def __new__(cls, name, expected, actual, passed, status=None):
        return super().__new__(cls, (name, expected, actual, bool(passed)))

    def __init__(self, name, expected, actual, passed, status=None):
        self.name = name
        self.check = name
        self.expected = str(expected)
        self.actual = str(actual)
        self.passed = bool(passed)
        self.status = status if status is not None else ('PASS' if passed else 'FAIL')

    def __getitem__(self, item):
        if isinstance(item, str):
            mapping = {
                'name': self.name,
                'check': self.check,
                'expected': self.expected,
                'actual': self.actual,
                'passed': self.passed,
                'status': self.status,
            }
            if item in mapping:
                return mapping[item]
            raise KeyError(item)
        return super().__getitem__(item)

    def get(self, item, default=None):
        try:
            return self[item]
        except KeyError:
            return default

    def keys(self):
        return ['name', 'check', 'expected', 'actual', 'passed', 'status']

    def values(self):
        return [self.name, self.check, self.expected, self.actual, self.passed, self.status]

    def items(self):
        return [(k, self[k]) for k in self.keys()]

    def __repr__(self):
        return f"BenchmarkCheck(name={self.name!r}, expected={self.expected!r}, actual={self.actual!r}, passed={self.passed!r})"


def run_benchmark():
    """
    Executes the benchmark scenario inside a database transaction that is rolled back at the end.
    Returns a list of checks: (name, expected, actual, passed).
    """
    results = []

    with transaction.atomic():
        try:
            # 1. Clean transactional records using raw SQL within this atomic block so benchmark starts from clean state
            with connection.cursor() as cursor:
                cursor.execute("""
                    DELETE FROM sales_payment;
                    DELETE FROM sales_creditnote;
                    DELETE FROM sales_dispatch;
                    DELETE FROM sales_invoiceline;
                    DELETE FROM sales_invoice;
                    DELETE FROM sales_orderline;
                    DELETE FROM sales_order;
                    DELETE FROM production_runvarianceline;
                    DELETE FROM production_runoverhead;
                    DELETE FROM production_runissue;
                    DELETE FROM production_productionrun;
                    DELETE FROM production_purchaseline;
                    DELETE FROM production_purchase;
                    DELETE FROM core_stockmovement;
                    DELETE FROM core_stocklot;
                """)

            # Seed benchmark master data and opening stock
            call_command('seed_benchmark', verbosity=0)

            # Get master data and users
            user = User.objects.get(username='system')
            admin_user = User.objects.get(username='admin1')
            salesman_a = User.objects.get(username='salesman_a')
            salesman_b = User.objects.get(username='salesman_b')
            retailer_a = Customer.objects.get(name='Retailer A')
            distributor_b = Customer.objects.get(name='Distributor B')
            supplier = Supplier.objects.get(name='Demo Flour Mills')

            flour = Item.objects.get(name='Flour')
            oil = Item.objects.get(name='Cooking oil')
            spice = Item.objects.get(name='Salt and spice mix')
            pouch = Item.objects.get(name='200 g pouch')
            carton = Item.objects.get(name='Carton')
            murukku = Item.objects.get(name='Murukku 200 g')
            bom = BOM.objects.get(finished_item=murukku)
            kg = Unit.objects.get(code='kg')

            # ── 1. Purchase of 60 kg flour at 4500 paise ────────────────────────────
            purchase = Purchase.objects.create(
                supplier=supplier,
                date=datetime.date.today(),
                created_by=user,
            )
            qty_purchased = Decimal('60')
            rate_paise = 4500
            line_total = int(qty_purchased * rate_paise)
            PurchaseLine.objects.create(
                purchase=purchase,
                item=flour,
                qty_in_purchase_unit=qty_purchased,
                purchase_unit=kg,
                qty_in_stock_unit=qty_purchased,
                unit_cost_paise=rate_paise,
                line_total_paise=line_total,
            )
            purchase.total_paise = line_total
            purchase.save()
            receive_stock(flour, qty_purchased, rate_paise, 'purchase', str(purchase.pk), user)

            # ── 2. Production run of Murukku 200 g ──────────────────────────────────
            run = ProductionRun.objects.create(
                bom=bom,
                batches=Decimal('1'),
                run_date=datetime.date.today(),
                actual_good_output=Decimal('188'),
                damaged_pouches=Decimal('6'),
                cartons_used=Decimal('9'),
                created_by=user,
            )
            RunIssue.objects.create(run=run, item=flour, issued_qty=Decimal('40'))
            RunIssue.objects.create(run=run, item=oil, issued_qty=Decimal('10.5'))
            RunIssue.objects.create(run=run, item=spice, issued_qty=Decimal('1.2'))

            post_run(run, user)
            run.refresh_from_db()

            # Overheads: labour 120000 paise (Rs 1200), gas/power 60000 paise (Rs 600)
            RunOverhead.objects.create(run=run, description='Labour', amount_paise=120000)
            RunOverhead.objects.create(run=run, description='Gas and power', amount_paise=60000)

            # Balances after production
            flour_bal = get_balance(flour)
            oil_bal = get_balance(oil)
            spice_bal = get_balance(spice)
            pouch_bal = get_balance(pouch)
            carton_bal = get_balance(carton)
            murukku_after_prod = get_balance(murukku)

            # Variances
            oil_variance = RunVarianceLine.objects.get(run=run, item=oil)

            # ── 3. Retailer A order of 150 packets ──────────────────────────────────
            order_a = Order.objects.create(
                client_uuid=uuid.uuid4(),
                customer=retailer_a,
                salesman=salesman_a,
                status=Order.STATUS_SUBMITTED,
            )
            OrderLine.objects.create(
                order=order_a,
                item=murukku,
                qty=Decimal('150'),
                price_paise=5000,
            )

            # ── 4. Approval ─────────────────────────────────────────────────────────
            order_a.has_shortage = Decimal('150') > get_balance(murukku)
            order_a.status = Order.STATUS_APPROVED
            order_a.save()

            # ── 5. Invoice ──────────────────────────────────────────────────────────
            invoice_a = create_invoice(order_a, user=admin_user)

            # ── 6. Dispatch ─────────────────────────────────────────────────────────
            dispatch_invoice(invoice_a, user=admin_user)
            murukku_after_dispatch = get_balance(murukku)

            # ── 7. Payment of 400000 paise ──────────────────────────────────────────
            record_payment(
                invoice=invoice_a,
                amount_paise=400000,
                mode='bank_transfer',
                reference='TXN-BENCHMARK-4000',
                user=admin_user,
            )
            outstanding_a = customer_outstanding(retailer_a)

            # ── 8. Distributor B order of 120 packets ───────────────────────────────
            order_b = Order.objects.create(
                client_uuid=uuid.uuid4(),
                customer=distributor_b,
                salesman=salesman_b,
                status=Order.STATUS_SUBMITTED,
            )
            OrderLine.objects.create(
                order=order_b,
                item=murukku,
                qty=Decimal('120'),
                price_paise=5000,
            )

            dist_b_ordered = Decimal('120')
            dist_b_available = get_balance(murukku)
            dist_b_shortage = max(Decimal('0'), dist_b_ordered - dist_b_available)
            tax_b = calculate_tax(distributor_b.state, int(dist_b_ordered * 5000), 5)
            igst_applies = (
                distributor_b.state.strip().lower() != 'tamil nadu'
                and tax_b.igst_paise > 0
                and tax_b.cgst_paise == 0
                and tax_b.sgst_paise == 0
            )

            # ── Check Definitions ───────────────────────────────────────────────────
            checks = [
                (
                    "Flour closing balance",
                    "120 kg",
                    f"{flour_bal} kg",
                    flour_bal == Decimal('120'),
                ),
                (
                    "Cooking oil closing balance",
                    "19.5 L",
                    f"{oil_bal} L",
                    oil_bal == Decimal('19.5'),
                ),
                (
                    "Spice mix closing balance",
                    "3.8 kg",
                    f"{spice_bal} kg",
                    spice_bal == Decimal('3.8'),
                ),
                (
                    "Pouches closing balance",
                    "306",
                    f"{pouch_bal}",
                    pouch_bal == Decimal('306'),
                ),
                (
                    "Cartons closing balance",
                    "21",
                    f"{carton_bal}",
                    carton_bal == Decimal('21'),
                ),
                (
                    "Finished Murukku after production",
                    "238",
                    f"{murukku_after_prod}",
                    murukku_after_prod == Decimal('238'),
                ),
                (
                    "Finished Murukku after dispatch",
                    "88",
                    f"{murukku_after_dispatch}",
                    murukku_after_dispatch == Decimal('88'),
                ),
                (
                    "Yield variance",
                    "-6% (flagged)",
                    f"{run.yield_variance_percent}% ({'flagged' if run.yield_flagged else 'unflagged'})",
                    run.yield_variance_percent == Decimal('-6.00') and run.yield_flagged,
                ),
                (
                    "Oil consumption variance",
                    "+5% (flagged)",
                    f"+{oil_variance.variance_percent}% ({'flagged' if oil_variance.flagged else 'unflagged'})",
                    oil_variance.variance_percent == Decimal('5.00') and oil_variance.flagged,
                ),
                (
                    "Material cost",
                    "401000 paise",
                    f"{run.material_cost_paise} paise",
                    run.material_cost_paise == 401000,
                ),
                (
                    "Cost per packet (with overheads 120000 + 60000)",
                    "3090 paise (Rs 30.90)",
                    f"{run.cost_per_packet_paise} paise (Rs {run.cost_per_packet_rupees})",
                    run.cost_per_packet_paise == 3090,
                ),
                (
                    "Margin per packet",
                    "1910 paise",
                    f"{run.margin_paise} paise",
                    run.margin_paise == 1910,
                ),
                (
                    "Retailer A invoice taxable",
                    "750000 paise",
                    f"{invoice_a.taxable_paise} paise",
                    invoice_a.taxable_paise == 750000,
                ),
                (
                    "Retailer A invoice CGST",
                    "18750 paise",
                    f"{invoice_a.cgst_paise} paise",
                    invoice_a.cgst_paise == 18750,
                ),
                (
                    "Retailer A invoice SGST",
                    "18750 paise",
                    f"{invoice_a.sgst_paise} paise",
                    invoice_a.sgst_paise == 18750,
                ),
                (
                    "Retailer A invoice total",
                    "787500 paise",
                    f"{invoice_a.total_paise} paise",
                    invoice_a.total_paise == 787500,
                ),
                (
                    "Retailer A outstanding",
                    "387500 paise",
                    f"{outstanding_a} paise",
                    outstanding_a == 387500,
                ),
                (
                    "Distributor B order shortage",
                    "Ordered 120, Available 88, Shortage 32",
                    f"Ordered {dist_b_ordered}, Available {dist_b_available}, Shortage {dist_b_shortage}",
                    dist_b_ordered == Decimal('120') and dist_b_available == Decimal('88') and dist_b_shortage == Decimal('32'),
                ),
                (
                    "Distributor B GST tax rule",
                    "IGST applies (Karnataka)",
                    f"IGST applies ({distributor_b.state})" if igst_applies else "Intra-state GST",
                    bool(igst_applies),
                ),
            ]

            for check_name, exp, act, is_passed in checks:
                results.append(BenchmarkCheck(
                    name=check_name,
                    expected=exp,
                    actual=act,
                    passed=bool(is_passed),
                ))

        finally:
            # Always roll back so benchmark execution never alters real data
            transaction.set_rollback(True)

    return results


execute_benchmark = run_benchmark

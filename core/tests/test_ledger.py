"""
Tests for core/ledger.py
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.ledger import (
    InsufficientStock,
    get_balance,
    get_lot_balances,
    issue_stock,
    receive_stock,
    reverse_movements,
)
from core.models import ImmutableStockMovementError, Item, StockMovement, Unit

User = get_user_model()

_user_counter = 0


def make_unit():
    unit, _ = Unit.objects.get_or_create(
        code="kg", defaults={"kind": "mass", "factor_to_base": Decimal("1.000")}
    )
    return unit


def make_item(name):
    return Item.objects.create(name=name, item_type="raw", stock_unit=make_unit())


def make_user(suffix=""):
    global _user_counter
    _user_counter += 1
    return User.objects.create_user(
        username=f"tester{_user_counter}", password="pass"
    )


class TestFifoIssue(TestCase):
    """Test 1: Receive 100 kg @ 4200 and 60 kg @ 4500; issue 40 kg FIFO."""

    def test_fifo_cost_and_balance(self):
        item = make_item("FifoItem1")
        user = make_user()
        receive_stock(item, Decimal("100"), 4200, "purchase", "PO-1", user)
        receive_stock(item, Decimal("60"), 4500, "purchase", "PO-2", user)

        movements, total_cost = issue_stock(
            item, Decimal("40"), "sale", "SO-1", user, method="fifo"
        )
        # 40 kg × 4200 = 168000 paise
        self.assertEqual(total_cost, 168000)
        # Balance: 160 − 40 = 120
        self.assertEqual(get_balance(item), Decimal("120"))

    def test_fifo_all_from_first_lot(self):
        item = make_item("FifoItem2")
        user = make_user()
        lot1_mv = receive_stock(item, Decimal("100"), 4200, "purchase", "PO-1", user)
        receive_stock(item, Decimal("60"), 4500, "purchase", "PO-2", user)

        movements, _ = issue_stock(
            item, Decimal("40"), "sale", "SO-1", user, method="fifo"
        )
        self.assertEqual(len(movements), 1)
        self.assertEqual(movements[0].lot, lot1_mv.lot)
        self.assertEqual(movements[0].qty, Decimal("-40"))


class TestInsufficientStock(TestCase):
    """Test 2: Issue more than available raises InsufficientStock and changes nothing."""

    def test_raises_insufficient_stock(self):
        item = make_item("InsufficientItem1")
        user = make_user()
        receive_stock(item, Decimal("10"), 1000, "purchase", "PO-1", user)

        with self.assertRaises(InsufficientStock) as ctx:
            issue_stock(item, Decimal("50"), "sale", "SO-1", user)
        exc = ctx.exception
        self.assertEqual(exc.needed, Decimal("50"))
        self.assertEqual(exc.available, Decimal("10"))

    def test_no_change_on_insufficient(self):
        item = make_item("InsufficientItem2")
        user = make_user()
        receive_stock(item, Decimal("10"), 1000, "purchase", "PO-1", user)

        balance_before = get_balance(item)
        movements_count_before = StockMovement.objects.filter(item=item).count()
        try:
            issue_stock(item, Decimal("50"), "sale", "SO-1", user)
        except InsufficientStock:
            pass
        self.assertEqual(get_balance(item), balance_before)
        self.assertEqual(
            StockMovement.objects.filter(item=item).count(),
            movements_count_before,
        )


class TestFefo(TestCase):
    """Test 3: FEFO picks the lot with earlier expiry even if received later."""

    def test_fefo_picks_earlier_expiry(self):
        item = make_item("FefoItem")
        user = make_user()
        today = date.today()
        # Lot A received first, expires later
        receive_stock(
            item, Decimal("50"), 3000, "purchase", "PO-A", user,
            expiry_date=today + timedelta(days=30),
        )
        # Lot B received second, expires sooner
        lot_b_mv = receive_stock(
            item, Decimal("50"), 3500, "purchase", "PO-B", user,
            expiry_date=today + timedelta(days=10),
        )

        movements, _ = issue_stock(
            item, Decimal("30"), "sale", "SO-1", user, method="fefo"
        )
        self.assertEqual(len(movements), 1)
        # Should use lot B (earlier expiry)
        self.assertEqual(movements[0].lot, lot_b_mv.lot)


class TestReverseMovements(TestCase):
    """Test 4: reverse_movements brings balance back to original."""

    def test_reverse_restores_balance(self):
        item = make_item("ReverseItem")
        user = make_user()
        receive_stock(item, Decimal("100"), 2000, "purchase", "PO-1", user)
        balance_after_receive = get_balance(item)

        issue_stock(item, Decimal("40"), "sale", "SO-1", user)
        balance_after_issue = get_balance(item)
        self.assertEqual(balance_after_issue, Decimal("60"))

        # Reverse the sale
        reverse_movements("sale", "SO-1", user)
        balance_after_reversal = get_balance(item)
        self.assertEqual(balance_after_reversal, balance_after_receive)


class TestImmutableMovements(TestCase):
    """Test 5: Editing or deleting a StockMovement raises an error."""

    def setUp(self):
        item = make_item("ImmutableItem")
        user = make_user()
        self.mv = receive_stock(item, Decimal("10"), 1000, "purchase", "PO-1", user)

    def test_update_via_queryset_raises(self):
        with self.assertRaises(ImmutableStockMovementError):
            StockMovement.objects.filter(pk=self.mv.pk).update(qty=Decimal("99"))

    def test_save_existing_raises(self):
        movement = StockMovement.objects.get(pk=self.mv.pk)
        movement.qty = Decimal("99")
        with self.assertRaises(ImmutableStockMovementError):
            movement.save()

    def test_delete_instance_raises(self):
        movement = StockMovement.objects.get(pk=self.mv.pk)
        with self.assertRaises(ImmutableStockMovementError):
            movement.delete()

    def test_delete_via_queryset_raises(self):
        with self.assertRaises(ImmutableStockMovementError):
            StockMovement.objects.filter(pk=self.mv.pk).delete()

"""
Stock ledger functions. All writes are atomic and use select_for_update on lots.
Money is always integer paise. Quantities are always Decimal.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum

from .models import Item, StockLot, StockMovement


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class InsufficientStock(Exception):
    """Raised when issue_stock cannot fulfil the requested quantity."""

    def __init__(self, item: Item, needed: Decimal, available: Decimal):
        self.item = item
        self.needed = needed
        self.available = available
        super().__init__(
            f"Insufficient stock for {item}: needed {needed}, available {available}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_name(name: str) -> str:
    """Strip non-alphanumeric characters for use in a batch number."""
    return re.sub(r"[^A-Za-z0-9]", "", name).upper()


def _auto_batch(item: Item, today: date) -> str:
    """Generate a unique batch number like ITEMNAME-YYYYMMDD-N."""
    prefix = f"{_safe_name(item.name)}-{today.strftime('%Y%m%d')}"
    existing = (
        StockLot.objects.filter(item=item, batch_no__startswith=prefix)
        .values_list("batch_no", flat=True)
    )
    n = len(existing) + 1
    return f"{prefix}-{n}"


def _round_paise(qty: Decimal, unit_cost_paise: int) -> int:
    """Round qty × unit_cost_paise to nearest integer (half-up)."""
    result = (qty * Decimal(unit_cost_paise)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(result)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@transaction.atomic
def receive_stock(
    item: Item,
    qty: Decimal,
    unit_cost_paise: int,
    source_type: str,
    source_id: str,
    user,
    batch_no: str | None = None,
    expiry_date=None,
) -> StockMovement:
    """Create a StockLot and a positive StockMovement."""
    today = date.today()
    if not batch_no:
        batch_no = _auto_batch(item, today)

    lot = StockLot.objects.create(
        item=item,
        batch_no=batch_no,
        received_date=today,
        expiry_date=expiry_date,
        unit_cost_paise=unit_cost_paise,
        qty_received=qty,
        source_type=source_type,
        source_id=str(source_id),
    )

    movement = StockMovement(
        item=item,
        lot=lot,
        qty=qty,
        unit_cost_paise=unit_cost_paise,
        source_type=source_type,
        source_id=str(source_id),
        created_by=user,
    )
    movement.save()
    return movement


@transaction.atomic
def issue_stock(
    item: Item,
    qty: Decimal,
    source_type: str,
    source_id: str,
    user,
    method: str = "fifo",
) -> tuple[list[StockMovement], int]:
    """
    Consume stock lots and create negative StockMovements.

    Returns (movements, total_cost_paise).
    Raises InsufficientStock if stock is not enough (no changes made).
    """
    qty = Decimal(qty)

    # Lock all lots for this item
    lots_qs = StockLot.objects.select_for_update().filter(item=item)

    # Compute remaining qty per lot (sum of all movements: +receive, -issue)
    lot_balances: list[tuple[StockLot, Decimal]] = []
    for lot in lots_qs:
        total = StockMovement.objects.filter(lot=lot).aggregate(
            total=Sum("qty")
        )["total"] or Decimal("0")
        balance = Decimal(total)
        if balance > 0:
            lot_balances.append((lot, balance))

    # Sort lots according to method
    if method == "fifo":
        lot_balances.sort(key=lambda x: x[0].received_date)
    elif method == "fefo":
        # lots with no expiry go last; among those with expiry, earliest first
        lot_balances.sort(
            key=lambda x: (
                x[0].expiry_date is None,
                x[0].expiry_date if x[0].expiry_date is not None else date.max,
            )
        )
    else:
        raise ValueError(f"Unknown method: {method}")

    available = sum((b for _, b in lot_balances), Decimal("0"))
    if available < qty:
        raise InsufficientStock(item=item, needed=qty, available=available)

    movements: list[StockMovement] = []
    total_cost = 0
    remaining = qty

    for lot, balance in lot_balances:
        if remaining <= 0:
            break
        take = min(balance, remaining)
        cost = _round_paise(take, lot.unit_cost_paise)
        total_cost += cost
        m = StockMovement(
            item=item,
            lot=lot,
            qty=-take,
            unit_cost_paise=lot.unit_cost_paise,
            source_type=source_type,
            source_id=str(source_id),
            created_by=user,
        )
        m.save()
        movements.append(m)
        remaining -= take

    return movements, total_cost


@transaction.atomic
def reverse_movements(source_type: str, source_id: str, user) -> list[StockMovement]:
    """
    Create opposite movements for every movement with the given source.
    Used for cancellations.
    """
    originals = list(
        StockMovement.objects.filter(source_type=source_type, source_id=str(source_id))
    )
    reversal_source_type = f"reversal_{source_type}"
    reversed_movements: list[StockMovement] = []

    for orig in originals:
        m = StockMovement(
            item=orig.item,
            lot=orig.lot,
            qty=-orig.qty,
            unit_cost_paise=orig.unit_cost_paise,
            source_type=reversal_source_type,
            source_id=str(source_id),
            note=f"Reversal of movement {orig.pk}",
            created_by=user,
        )
        m.save()
        reversed_movements.append(m)

    return reversed_movements


def get_balance(item: Item, as_of=None) -> Decimal:
    """Return current stock balance (sum of all movement qty)."""
    qs = StockMovement.objects.filter(item=item)
    if as_of is not None:
        qs = qs.filter(created_at__date__lte=as_of)
    result = qs.aggregate(total=Sum("qty"))["total"]
    return Decimal(result) if result is not None else Decimal("0")


def get_lot_balances(item: Item) -> dict[int, Decimal]:
    """Return remaining qty per lot id for the given item."""
    lots = StockLot.objects.filter(item=item)
    balances: dict[int, Decimal] = {}
    for lot in lots:
        total = StockMovement.objects.filter(lot=lot).aggregate(
            total=Sum("qty")
        )["total"] or Decimal("0")
        balances[lot.pk] = Decimal(total)
    return balances

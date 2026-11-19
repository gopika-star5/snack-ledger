from __future__ import annotations

import re
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from core.ledger import issue_stock, receive_stock
from core.models import AuditLog, StockLot
from .models import ProductionRun, RunVarianceLine


@transaction.atomic
def post_run(run: ProductionRun, user) -> ProductionRun:
    """
    Post a production run atomically:
    - Issue stock for raw materials and packaging (FIFO)
    - Receive finished goods stock with calculated unit cost
    - Calculate variances per BOM line and yield variance
    - Mark run as posted (cannot be posted again or edited)
    - Create AuditLog entry
    """
    if run.pk:
        orig = ProductionRun.objects.filter(pk=run.pk).values('status').first()
        if orig and orig['status'] == 'posted':
            raise ValidationError("A posted production run cannot be posted again.")
    if run.status == 'posted':
        raise ValidationError("A posted production run cannot be posted again.")

    # 1. Issue raw materials from RunIssue
    material_cost = 0
    for issue in run.issues.select_related('item').all():
        movements, cost = issue_stock(
            item=issue.item,
            qty=issue.issued_qty,
            source_type="production",
            source_id=str(run.pk),
            user=user,
            method="fifo",
        )
        material_cost += cost

    # 2. Packaging consumed
    pouch_line = None
    carton_line = None
    for line in run.bom.lines.select_related('item').all():
        name_lower = line.item.name.lower()
        if 'pouch' in name_lower:
            pouch_line = line
        elif 'carton' in name_lower:
            carton_line = line

    if not pouch_line:
        for line in run.bom.lines.select_related('item').all():
            if line.item.item_type == 'packaging' and line != carton_line:
                pouch_line = line
                break

    pouches_consumed = Decimal(str(run.actual_good_output)) + Decimal(str(run.damaged_pouches))
    cartons_consumed = Decimal(str(run.cartons_used))

    if pouch_line and pouches_consumed > Decimal('0'):
        if not run.issues.filter(item=pouch_line.item).exists():
            movements, cost = issue_stock(
                item=pouch_line.item,
                qty=pouches_consumed,
                source_type="production",
                source_id=str(run.pk),
                user=user,
                method="fifo",
            )
            material_cost += cost

    if carton_line and cartons_consumed > Decimal('0'):
        if not run.issues.filter(item=carton_line.item).exists():
            movements, cost = issue_stock(
                item=carton_line.item,
                qty=cartons_consumed,
                source_type="production",
                source_id=str(run.pk),
                user=user,
                method="fifo",
            )
            material_cost += cost

    # 3. Finished item receive stock
    finished_item = run.bom.finished_item
    good_output = Decimal(str(run.actual_good_output))
    if good_output > Decimal('0'):
        unit_cost_paise = int(
            (Decimal(material_cost) / good_output).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
    else:
        unit_cost_paise = 0

    clean_name = re.sub(r'[^A-Za-z0-9]', '', finished_item.name).upper()
    prefix_code = clean_name[:3] if len(clean_name) >= 3 else clean_name
    date_str = run.run_date.strftime("%Y%m%d")
    batch_prefix = f"{prefix_code}-{date_str}"
    existing_count = StockLot.objects.filter(item=finished_item, batch_no__startswith=batch_prefix).count()
    batch_no = f"{batch_prefix}-{existing_count + 1}"

    expiry_date = None
    if finished_item.shelf_life_days:
        expiry_date = run.run_date + timedelta(days=finished_item.shelf_life_days)

    receive_stock(
        item=finished_item,
        qty=good_output,
        unit_cost_paise=unit_cost_paise,
        source_type="production",
        source_id=str(run.pk),
        user=user,
        batch_no=batch_no,
        expiry_date=expiry_date,
    )

    # 4. Variances per BOM line
    RunVarianceLine.objects.filter(run=run).delete()
    for line in run.bom.lines.select_related('item').all():
        planned = line.qty_per_batch * Decimal(str(run.batches))
        if pouch_line and line.id == pouch_line.id:
            actual = pouches_consumed
        elif carton_line and line.id == carton_line.id:
            actual = cartons_consumed
        else:
            issue = run.issues.filter(item=line.item).first()
            actual = issue.issued_qty if issue else Decimal('0')

        variance_qty = actual - planned
        if planned != Decimal('0'):
            variance_percent = ((actual - planned) / planned * Decimal('100')).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
        else:
            variance_percent = Decimal('0.00')

        flagged = abs(variance_percent) > run.bom.tolerance_percent
        direction = 'adverse' if actual > planned else 'favourable'

        RunVarianceLine.objects.create(
            run=run,
            item=line.item,
            planned_qty=planned,
            actual_qty=actual,
            variance_qty=variance_qty,
            variance_percent=variance_percent,
            flagged=flagged,
            direction=direction,
        )

    # 5. Yield variance
    expected_yield = run.bom.batch_yield * Decimal(str(run.batches))
    actual_yield = Decimal(str(run.actual_good_output))
    if expected_yield != Decimal('0'):
        yield_variance_percent = ((actual_yield - expected_yield) / expected_yield * Decimal('100')).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
    else:
        yield_variance_percent = Decimal('0.00')

    yield_flagged = abs(yield_variance_percent) > run.bom.tolerance_percent

    # 6. Save run as posted
    run.yield_variance_percent = yield_variance_percent
    run.yield_flagged = yield_flagged
    run.material_cost_paise = material_cost
    run.unit_cost_paise = unit_cost_paise
    run.status = 'posted'
    run.save(_allow_post=True)

    # 7. Audit log
    AuditLog.objects.create(
        user=user if getattr(user, 'is_authenticated', True) else None,
        action="post",
        model_name="productionrun",
        object_id=str(run.pk),
        details={
            "actual_good_output": str(run.actual_good_output),
            "material_cost_paise": material_cost,
            "unit_cost_paise": unit_cost_paise,
            "yield_variance_percent": str(yield_variance_percent),
            "yield_flagged": yield_flagged,
        },
    )

    return run

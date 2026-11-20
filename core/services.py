import datetime
from decimal import Decimal
from django.db.models import Q
from core.models import Item, Customer
from core.ledger import get_balance
from sales.models import Order, OrderLine
from sales.utils import calculate_customer_ageing, format_inr
from production.models import ProductionRun


def get_owner_summary_data(today=None):
    """
    Computes real summary metrics for the owner overview / owner dashboard.
    - Operations Health: Green 'Healthy' only when no recent production run has a
      flagged variance and no stock is below its reorder level; otherwise amber
      'Needs attention' with alert count.
    - Card sub-labels computed from real data.
    """
    if today is None:
        today = datetime.date.today()
    elif isinstance(today, str):
        today = datetime.date.fromisoformat(today)

    # 1. Operations Health
    # Flagged production runs (yield variance or ingredient line variance)
    flagged_runs = ProductionRun.objects.filter(
        status='posted'
    ).filter(
        Q(yield_flagged=True) | Q(variance_lines__flagged=True)
    ).distinct().order_by('-run_date', '-id')[:10]
    flagged_variance_count = flagged_runs.count()

    # Low stock items (stock below or equal to reorder level)
    low_stock_items = []
    for item in Item.objects.filter(active=True):
        if item.reorder_level is not None:
            bal = get_balance(item)
            if bal <= item.reorder_level:
                low_stock_items.append(item)
    low_stock_count = len(low_stock_items)

    alert_count = flagged_variance_count + low_stock_count
    operations_healthy = (alert_count == 0)

    # 2. Today's Orders & Sub-label
    todays_orders = Order.objects.filter(
        created_at__date=today
    ).select_related('customer', 'salesman').order_by('-created_at')
    todays_orders_count = todays_orders.count()
    todays_orders_qty = sum(
        line.qty for line in OrderLine.objects.filter(order__created_at__date=today)
    )

    customer_names = list(todays_orders.values_list('customer__name', flat=True).distinct())
    if todays_orders_count == 0:
        todays_orders_sublabel = "No orders today"
    elif len(customer_names) == 1:
        if todays_orders_count == 1:
            todays_orders_sublabel = f"1 order from {customer_names[0]}"
        else:
            todays_orders_sublabel = f"{todays_orders_count} orders from {customer_names[0]}"
    else:
        todays_orders_sublabel = f"{todays_orders_count} orders from {len(customer_names)} customers"

    # 3. Total Receivables & Sub-label
    all_customers = Customer.objects.filter(invoices__isnull=False).distinct()
    customers_owing = []
    total_outstanding_paise = 0
    for customer in all_customers:
        ageing = calculate_customer_ageing(customer, today=today)
        cust_outstanding = (ageing['bucket_0_30'] + ageing['bucket_31_60'] +
                            ageing['bucket_61_90'] + ageing['bucket_90_plus'])
        if cust_outstanding > 0:
            customers_owing.append((customer.name, cust_outstanding))
        total_outstanding_paise += cust_outstanding

    if len(customers_owing) == 0:
        receivables_sublabel = "No outstanding dues"
    elif len(customers_owing) == 1:
        receivables_sublabel = f"{customers_owing[0][0]} outstanding"
    else:
        receivables_sublabel = f"{len(customers_owing)} customers owe money"

    # 4. Finished Goods Stock (Murukku)
    murukku = Item.objects.filter(item_type='finished', active=True).first()
    murukku_stock = int(get_balance(murukku)) if murukku else 0

    # 5. Yield Variance (Latest posted run)
    latest_run = ProductionRun.objects.filter(status='posted').order_by('-run_date', '-id').first()
    if latest_run and latest_run.yield_variance_percent is not None:
        yield_variance_val = f"{latest_run.yield_variance_percent}%"
        expected_output = int(latest_run.bom.batch_yield * latest_run.batches)
        actual_output = int(latest_run.actual_good_output)
        yield_sublabel = f"{actual_output} vs {expected_output} expected"
    else:
        yield_variance_val = "0%"
        yield_sublabel = "No production runs"

    return {
        'operations_healthy': operations_healthy,
        'alert_count': alert_count,
        'flagged_variance_count': flagged_variance_count,
        'low_stock_count': low_stock_count,
        'flagged_runs': flagged_runs,
        'todays_orders': todays_orders,
        'todays_orders_count': todays_orders_count,
        'todays_orders_qty': todays_orders_qty,
        'todays_orders_sublabel': todays_orders_sublabel,
        'total_outstanding_paise': total_outstanding_paise,
        'total_receivables_formatted': format_inr(total_outstanding_paise),
        'receivables_sublabel': receivables_sublabel,
        'murukku_stock': murukku_stock,
        'latest_run': latest_run,
        'yield_variance_val': yield_variance_val,
        'yield_sublabel': yield_sublabel,
    }

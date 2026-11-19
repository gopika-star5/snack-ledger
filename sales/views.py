import re
import uuid
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render

from core.decorators import role_required
from core.models import Customer, Item
from .models import Order, OrderLine


@role_required('salesman')
def salesman_home(request):
    """Salesman role home page with quick action cards and summary."""
    my_orders = Order.objects.filter(salesman=request.user)
    orders_count = my_orders.count()
    recent_orders = my_orders.prefetch_related('lines__item', 'customer').order_by('-created_at')[:5]

    total_value_paise = sum(order.total_paise for order in my_orders)
    total_value_rupees = f"{total_value_paise / 100:.2f}"

    context = {
        'orders_count': orders_count,
        'recent_orders': recent_orders,
        'total_value_rupees': total_value_rupees,
    }
    return render(request, 'sales/salesman_home.html', context)


@role_required('salesman')
def order_create(request):
    """Create a new retail/distributor order."""
    errors = []

    if request.method == 'POST':
        # Resolve client_uuid
        raw_uuid = request.POST.get('client_uuid')
        if raw_uuid:
            try:
                client_uuid = uuid.UUID(str(raw_uuid).strip())
            except (ValueError, AttributeError):
                client_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, str(raw_uuid).strip())
        else:
            client_uuid = uuid.uuid4()

        # Idempotency check: if client_uuid already exists, do not recreate
        existing_order = Order.objects.filter(client_uuid=client_uuid).first()
        if existing_order:
            messages.info(request, f"Order #{existing_order.id} was already submitted.")
            return redirect('order_list')

        # Customer lookup
        customer_id = request.POST.get('customer') or request.POST.get('customer_id')
        customer = None
        if customer_id:
            try:
                customer = Customer.objects.get(pk=customer_id)
            except Customer.DoesNotExist:
                errors.append("Selected customer was not found.")
        else:
            errors.append("Please select a customer.")

        # Extract line items from various possible POST shapes
        parsed_lines = []

        # List shapes: item_id[], qty[], price_paise[] or item, qty, price_paise
        item_ids = (
            request.POST.getlist('item_id')
            or request.POST.getlist('item')
            or request.POST.getlist('items')
            or request.POST.getlist('items[]')
        )
        qtys = (
            request.POST.getlist('qty')
            or request.POST.getlist('quantity')
            or request.POST.getlist('quantities')
            or request.POST.getlist('quantities[]')
        )
        prices = (
            request.POST.getlist('price_paise')
            or request.POST.getlist('price')
            or request.POST.getlist('prices')
            or request.POST.getlist('prices[]')
        )

        if item_ids and qtys:
            for idx, i_id in enumerate(item_ids):
                q_val = qtys[idx] if idx < len(qtys) else None
                p_val = prices[idx] if idx < len(prices) else None
                if i_id and q_val:
                    parsed_lines.append((i_id, q_val, p_val))
        else:
            # Check formset / indexed keys like form-0-item, line_0_item, etc.
            indexed_items = {}
            for key, val in request.POST.items():
                m = re.match(r'^(?:form|line|lines)[-_](\d+)[-_](item|item_id|qty|quantity|price_paise|price)$', key)
                if m:
                    idx, field = int(m.group(1)), m.group(2)
                    if idx not in indexed_items:
                        indexed_items[idx] = {}
                    indexed_items[idx][field] = val

            for idx in sorted(indexed_items.keys()):
                d = indexed_items[idx]
                i_id = d.get('item') or d.get('item_id')
                q_val = d.get('qty') or d.get('quantity')
                p_val = d.get('price_paise') or d.get('price')
                if i_id and q_val:
                    parsed_lines.append((i_id, q_val, p_val))

        valid_lines = []
        for i_id, q_val, p_val in parsed_lines:
            try:
                item_obj = Item.objects.get(pk=i_id)
            except Item.DoesNotExist:
                continue

            try:
                qty_dec = Decimal(str(q_val))
                if qty_dec <= Decimal('0'):
                    continue
            except Exception:
                continue

            # Price pre-filled from item selling_price_paise if not provided
            if p_val is not None and str(p_val).strip() != '':
                try:
                    price_int = int(p_val)
                except ValueError:
                    price_int = item_obj.selling_price_paise or 0
            else:
                price_int = item_obj.selling_price_paise or 0

            valid_lines.append((item_obj, qty_dec, price_int))

        if not valid_lines:
            errors.append("Please add at least one product with a valid quantity.")

        if not errors and customer:
            try:
                with transaction.atomic():
                    order = Order.objects.create(
                        client_uuid=client_uuid,
                        customer=customer,
                        salesman=request.user,
                        status=Order.STATUS_SUBMITTED,
                        note=request.POST.get('note', '').strip(),
                    )
                    for item_obj, qty_dec, price_int in valid_lines:
                        OrderLine.objects.create(
                            order=order,
                            item=item_obj,
                            qty=qty_dec,
                            price_paise=price_int,
                        )
                messages.success(request, f"Order #{order.id} submitted successfully!")
                return redirect('order_list')
            except IntegrityError:
                existing_order = Order.objects.get(client_uuid=client_uuid)
                messages.info(request, f"Order #{existing_order.id} was already submitted.")
                return redirect('order_list')

    # GET request or form validation error
    client_uuid = uuid.uuid4()
    customers = Customer.objects.all().order_by('name')

    # Prefer finished goods if available, otherwise all active items
    finished_items = Item.objects.filter(active=True, item_type='finished').order_by('name')
    if finished_items.exists():
        items = finished_items
    else:
        items = Item.objects.filter(active=True).order_by('name')

    context = {
        'client_uuid': str(client_uuid),
        'customers': customers,
        'items': items,
        'errors': errors,
    }
    return render(request, 'sales/order_form.html', context)


@role_required('salesman')
def order_list(request):
    """List orders for the logged-in salesman only."""
    orders = (
        Order.objects.filter(salesman=request.user)
        .prefetch_related('lines__item', 'customer')
        .order_by('-created_at')
    )
    context = {
        'orders': orders,
    }
    return render(request, 'sales/order_list.html', context)


@role_required('salesman')
def order_detail(request, pk):
    """View details of a single order. Strictly scoped to the order owner."""
    order = get_object_or_404(
        Order.objects.prefetch_related('lines__item', 'customer'),
        pk=pk,
    )
    if order.salesman != request.user:
        raise PermissionDenied("You do not have permission to view another salesman's order.")

    context = {
        'order': order,
    }
    return render(request, 'sales/order_detail.html', context)

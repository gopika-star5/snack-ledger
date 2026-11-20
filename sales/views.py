import datetime
import re
import uuid
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from weasyprint import HTML

from core.decorators import role_required
from core.ledger import InsufficientStock, get_balance
from core.models import Customer, Item, AuditLog, CompanySettings
from .models import Order, OrderLine, Invoice, InvoiceLine, Dispatch, CreditNote, Payment
from .services import create_invoice, dispatch_invoice, cancel_invoice, record_payment, customer_outstanding
from .utils import format_inr, amount_to_words_inr, get_whatsapp_invoice_url, calculate_customer_ageing


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


def order_detail(request, pk):
    """View details of a single order. Strictly scoped to the order owner or admin/owner."""
    if not request.user.is_authenticated:
        return redirect('login')
    if request.user.is_superuser or request.user.groups.filter(name__in=['admin', 'owner']).exists():
        return redirect('admin_order_detail', pk=pk)
    if not request.user.groups.filter(name='salesman').exists():
        raise PermissionDenied("Access forbidden: User is not in 'salesman' role.")

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


@role_required('admin', 'owner')
def admin_pending_orders(request):
    """
    Pending orders list for admin & owner roles:
    All orders with status 'submitted', showing customer, salesman, date, and total.
    """
    orders = (
        Order.objects.filter(status=Order.STATUS_SUBMITTED)
        .select_related('customer', 'salesman')
        .prefetch_related('lines__item', 'lines__item__stock_unit')
        .order_by('-created_at')
    )
    context = {
        'orders': orders,
    }
    return render(request, 'sales/admin_pending_orders.html', context)


@role_required('admin', 'owner')
def admin_order_detail(request, pk):
    """
    Admin order review and verification page:
    Shows line items, ordered qty, finished goods stock balance from ledger,
    shortage = max(0, ordered - available), and red alert if shortage exists.
    Admin can edit each line's qty and price, then Approve or Reject with a reason.
    Approving sets status='approved' and saves has_shortage flag on order.
    AuditLog entry is created for every edit, approval, and rejection.
    """
    order = get_object_or_404(
        Order.objects.select_related('customer', 'salesman')
        .prefetch_related('lines__item', 'lines__item__stock_unit'),
        pk=pk,
    )

    if request.method == 'POST':
        action = request.POST.get('action', '').strip().lower()
        if not action:
            if 'approve' in request.POST or request.POST.get('status') == 'approved':
                action = 'approve'
            elif 'reject' in request.POST or request.POST.get('status') == 'rejected':
                action = 'reject'
            elif 'edit' in request.POST or 'save' in request.POST:
                action = 'edit'

        # 1. Parse and apply line edits (quantity & price)
        lines = list(order.lines.all())
        line_edits = _parse_admin_line_edits(request, lines)
        changes = []

        with transaction.atomic():
            for line in lines:
                edit_data = line_edits.get(line.pk)
                if not edit_data:
                    continue
                changed = False
                old_qty = line.qty
                old_price = line.price_paise

                new_qty = edit_data.get('qty')
                if new_qty is not None and new_qty != old_qty:
                    line.qty = new_qty
                    changed = True

                new_price = edit_data.get('price_paise')
                if new_price is not None and new_price != old_price:
                    line.price_paise = new_price
                    changed = True

                if changed:
                    line.save()
                    changes.append({
                        'line_id': line.pk,
                        'item': line.item.name,
                        'old_qty': str(old_qty),
                        'new_qty': str(line.qty),
                        'old_price_paise': old_price,
                        'new_price_paise': line.price_paise,
                    })

            if changes:
                AuditLog.objects.create(
                    user=request.user,
                    action='edit',
                    model_name='order',
                    object_id=str(order.pk),
                    details={'changes': changes},
                )

            # 2. Handle Action (Approve / Reject / Edit)
            if action in ['approve', 'approved']:
                # Recalculate shortage based on current lines
                has_shortage = False
                for line in order.lines.all():
                    avail = get_balance(line.item)
                    if line.qty > avail:
                        has_shortage = True
                        break

                order.has_shortage = has_shortage
                order.status = Order.STATUS_APPROVED
                order.save()

                AuditLog.objects.create(
                    user=request.user,
                    action='approve',
                    model_name='order',
                    object_id=str(order.pk),
                    details={
                        'status': 'approved',
                        'has_shortage': has_shortage,
                    },
                )
                messages.success(request, f"Order #{order.pk} approved successfully.")
                return redirect('admin_order_detail', pk=order.pk)

            elif action in ['reject', 'rejected']:
                reason = (
                    request.POST.get('rejection_reason')
                    or request.POST.get('reason')
                    or request.POST.get('note', '')
                ).strip()

                order.status = Order.STATUS_REJECTED
                order.rejection_reason = reason
                order.save()

                AuditLog.objects.create(
                    user=request.user,
                    action='reject',
                    model_name='order',
                    object_id=str(order.pk),
                    details={
                        'status': 'rejected',
                        'reason': reason,
                    },
                )
                messages.warning(request, f"Order #{order.pk} rejected.")
                return redirect('admin_order_detail', pk=order.pk)

            elif action in ['create_invoice', 'invoice']:
                try:
                    invoice = create_invoice(order, request.user)
                    messages.success(request, f"Invoice {invoice.number} created successfully.")
                    return redirect('invoice_detail', pk=invoice.pk)
                except Exception as e:
                    messages.error(request, str(e))
                    return redirect('admin_order_detail', pk=order.pk)

            elif changes or action in ['edit', 'save', 'update']:
                messages.success(request, f"Order #{order.pk} updated successfully.")
                return redirect('admin_order_detail', pk=order.pk)

    # GET request: Calculate stock balance and shortages for each line
    lines_info = []
    order_shortage = Decimal('0')
    lines = list(order.lines.all())

    for line in lines:
        available = get_balance(line.item)
        shortage = max(Decimal('0'), line.qty - available)
        has_shortage = shortage > Decimal('0')
        order_shortage += shortage

        # Format display numbers
        if shortage == int(shortage):
            shortage_display = str(int(shortage))
        else:
            shortage_display = str(shortage.normalize())

        if available == int(available):
            available_display = str(int(available))
        else:
            available_display = str(available.normalize())

        unit_code = line.item.stock_unit.code if line.item and line.item.stock_unit else 'packet'
        if unit_code in ['packet', 'pouch', 'carton', 'piece']:
            unit_display = f"{unit_code}s" if shortage != 1 else unit_code
        else:
            unit_display = unit_code

        # Attach helper attributes to line
        line.available = available
        line.available_display = available_display
        line.shortage = shortage
        line.shortage_display = shortage_display
        line.has_shortage = has_shortage
        line.unit_display = unit_display

        lines_info.append({
            'line': line,
            'item': line.item,
            'ordered': line.qty,
            'available': available,
            'available_display': available_display,
            'shortage': shortage,
            'shortage_display': shortage_display,
            'has_shortage': has_shortage,
            'unit_display': unit_display,
        })

    order_has_shortage = order_shortage > Decimal('0')
    if order_shortage == int(order_shortage):
        order_shortage_display = str(int(order_shortage))
    else:
        order_shortage_display = str(order_shortage.normalize())

    order.shortage = order_shortage
    order.shortage_display = order_shortage_display
    order.has_shortage_now = order_has_shortage

    context = {
        'order': order,
        'lines': lines,
        'lines_info': lines_info,
        'order_shortage': order_shortage,
        'order_shortage_display': order_shortage_display,
        'order_has_shortage': order_has_shortage,
    }
    return render(request, 'sales/admin_order_detail.html', context)


def _parse_admin_line_edits(request, lines):
    """
    Parse line quantity and price edits from request.POST with support for
    multiple key conventions.
    """
    edits = {}

    # Parallel lists: line_id, qty, price_paise
    line_ids = (
        request.POST.getlist('line_id')
        or request.POST.getlist('line_ids')
        or request.POST.getlist('lines')
    )
    qtys = (
        request.POST.getlist('qty')
        or request.POST.getlist('quantity')
        or request.POST.getlist('quantities')
    )
    prices = (
        request.POST.getlist('price_paise')
        or request.POST.getlist('price')
        or request.POST.getlist('prices')
    )

    if line_ids and qtys:
        for idx, lid_str in enumerate(line_ids):
            try:
                lid = int(lid_str)
            except (ValueError, TypeError):
                continue
            q_val = qtys[idx] if idx < len(qtys) else None
            p_val = prices[idx] if idx < len(prices) else None
            edits[lid] = {'qty_raw': q_val, 'price_raw': p_val}

    for line in lines:
        lid = line.pk
        q_val = None
        p_val = None

        qty_keys = [
            f'line_{lid}_qty',
            f'qty_{lid}',
            f'line-{lid}-qty',
            f'lines[{lid}][qty]',
            f'line_{lid}_quantity',
            f'quantity_{lid}',
        ]
        for k in qty_keys:
            if k in request.POST:
                q_val = request.POST[k]
                break

        price_keys = [
            f'line_{lid}_price_paise',
            f'price_paise_{lid}',
            f'line-{lid}-price_paise',
            f'lines[{lid}][price_paise]',
            f'line_{lid}_price',
            f'price_{lid}',
            f'line-{lid}-price',
            f'lines[{lid}][price]',
        ]
        for k in price_keys:
            if k in request.POST:
                p_val = request.POST[k]
                break

        # Fallback for single-line orders
        if len(lines) == 1:
            if q_val is None:
                q_val = request.POST.get('qty') or request.POST.get('quantity') or request.POST.get('new_qty')
            if p_val is None:
                p_val = request.POST.get('price_paise') or request.POST.get('price') or request.POST.get('new_price')

        if q_val is not None or p_val is not None:
            if lid not in edits:
                edits[lid] = {}
            if q_val is not None:
                edits[lid]['qty_raw'] = q_val
            if p_val is not None:
                edits[lid]['price_raw'] = p_val

    results = {}
    for lid, vals in edits.items():
        q_dec = None
        p_int = None
        if 'qty_raw' in vals and vals['qty_raw'] is not None and str(vals['qty_raw']).strip() != '':
            try:
                q_dec = Decimal(str(vals['qty_raw']).strip())
            except Exception:
                pass
        if 'price_raw' in vals and vals['price_raw'] is not None and str(vals['price_raw']).strip() != '':
            raw_p = str(vals['price_raw']).strip()
            try:
                if '.' in raw_p:
                    p_int = int(Decimal(raw_p) * 100)
                else:
                    p_int = int(raw_p)
            except Exception:
                pass
        results[lid] = {'qty': q_dec, 'price_paise': p_int}

    return results


# ---------------------------------------------------------------------------
# Invoice & Dispatch Admin Views (Role: admin, owner)
# ---------------------------------------------------------------------------

@role_required('admin', 'owner')
def invoice_list(request):
    """
    Invoices list page showing number, customer, date, total, and status.
    """
    invoices = (
        Invoice.objects.select_related('customer', 'order')
        .prefetch_related('dispatches', 'credit_notes')
        .order_by('-date', '-id')
    )
    context = {
        'invoices': invoices,
    }
    return render(request, 'sales/invoice_list.html', context)


@role_required('admin', 'owner')
def create_invoice_from_order(request, pk):
    """
    Create an invoice from an approved order and open the invoice page.
    """
    order = get_object_or_404(Order, pk=pk)
    if order.status != Order.STATUS_APPROVED:
        if order.invoices.exists():
            return redirect('invoice_detail', pk=order.invoices.first().pk)
        messages.error(
            request,
            f"Cannot create invoice: Order #{order.pk} status is '{order.get_status_display()}'. Only approved orders can be invoiced."
        )
        return redirect('admin_order_detail', pk=order.pk)

    try:
        invoice = create_invoice(order, request.user)
        messages.success(request, f"Invoice {invoice.number} created successfully.")
        return redirect('invoice_detail', pk=invoice.pk)
    except Exception as e:
        messages.error(request, f"Could not create invoice: {e}")
        return redirect('admin_order_detail', pk=order.pk)


@role_required('admin', 'owner')
def invoice_detail(request, pk):
    """
    Invoice detail page: shows invoice number, date, customer with GSTIN,
    lines (item, HSN, qty, rate, taxable), CGST+SGST or IGST, total in rupees,
    and buttons: Download PDF, Dispatch, Cancel Invoice.
    """
    invoice = get_object_or_404(
        Invoice.objects.select_related('customer', 'order')
        .prefetch_related('lines__item', 'lines__item__stock_unit', 'dispatches', 'credit_notes', 'payments'),
        pk=pk,
    )

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'dispatch':
            return _execute_dispatch(request, invoice)
        elif action == 'cancel':
            return _execute_cancel(request, invoice)

    company = CompanySettings.get()
    is_intra = (invoice.place_of_supply or "").strip().lower() == (company.state or "Tamil Nadu").strip().lower()
    wa_url = get_whatsapp_invoice_url(invoice)

    context = {
        'invoice': invoice,
        'company': company,
        'lines': invoice.lines.select_related('item', 'item__stock_unit', 'lot').all(),
        'is_intra_state': is_intra,
        'amount_in_words': invoice.amount_in_words,
        'wa_url': wa_url,
    }
    return render(request, 'sales/invoice_detail.html', context)


def _execute_dispatch(request, invoice):
    try:
        dispatch = dispatch_invoice(invoice, request.user)
        messages.success(request, f"Invoice {invoice.number} dispatched successfully. Finished goods inventory updated.")
    except InsufficientStock as e:
        messages.error(
            request,
            f"Stock Shortage Error: Insufficient finished goods stock to dispatch {invoice.number} ({e}). Stock and invoice remain unchanged."
        )
    except Exception as e:
        msg = e.message if hasattr(e, 'message') else str(e)
        messages.error(request, f"Cannot dispatch invoice: {msg}")
    return redirect('invoice_detail', pk=invoice.pk)


def _execute_cancel(request, invoice):
    reason = (
        request.POST.get('reason')
        or request.POST.get('rejection_reason')
        or 'Cancelled by admin'
    ).strip()
    try:
        cn = cancel_invoice(invoice, reason=reason, user=request.user)
        messages.warning(request, f"Invoice {invoice.number} has been cancelled. Credit Note {cn.number} issued.")
    except Exception as e:
        msg = e.message if hasattr(e, 'message') else str(e)
        messages.error(request, f"Cannot cancel invoice: {msg}")
    return redirect('invoice_detail', pk=invoice.pk)


@role_required('admin', 'owner')
def invoice_dispatch(request, pk):
    """
    Dispatch button endpoint: calls dispatch_invoice;
    shows clear red message if stock is not enough and changes nothing.
    """
    invoice = get_object_or_404(Invoice, pk=pk)
    return _execute_dispatch(request, invoice)


@role_required('admin', 'owner')
def invoice_cancel(request, pk):
    """
    Cancel invoice endpoint: calls cancel_invoice; issues credit note.
    """
    invoice = get_object_or_404(Invoice, pk=pk)
    return _execute_cancel(request, invoice)


@role_required('admin', 'owner')
def invoice_pdf(request, pk):
    """
    Invoice PDF via WeasyPrint from HTML template.
    Shows company name and GSTIN, customer GSTIN, invoice number and date,
    HSN, taxable value, CGST/SGST or IGST, total, and amount in words.
    """
    invoice = get_object_or_404(
        Invoice.objects.select_related('customer', 'order')
        .prefetch_related('lines__item', 'lines__item__stock_unit'),
        pk=pk,
    )
    company = CompanySettings.get()
    is_intra = (invoice.place_of_supply or "").strip().lower() == (company.state or "Tamil Nadu").strip().lower()
    wa_url = get_whatsapp_invoice_url(invoice)

    context = {
        'invoice': invoice,
        'company': company,
        'lines': invoice.lines.select_related('item', 'item__stock_unit', 'lot').all(),
        'is_intra_state': is_intra,
        'amount_in_words': invoice.amount_in_words,
        'wa_url': wa_url,
    }

    html_string = render_to_string('sales/invoice_pdf.html', context, request=request)
    pdf_bytes = HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf()

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    clean_num = invoice.number.replace('/', '-')
    response['Content-Disposition'] = f'inline; filename="Invoice-{clean_num}.pdf"'
    return response


# ---------------------------------------------------------------------------
# Payment & Receivables Views (Role: admin, owner; accountant read-only for outstanding)
# ---------------------------------------------------------------------------

@role_required('admin', 'owner')
def record_payment_view(request):
    """
    Record Payment page for admin and owner:
    choose a customer, see their unpaid invoices, enter amount, date,
    mode (cash/UPI/bank/cheque) and reference, then save with record_payment.
    """
    errors = []

    if request.method == 'POST':
        customer_id = request.POST.get('customer') or request.POST.get('customer_id')
        invoice_id = request.POST.get('invoice') or request.POST.get('invoice_id')
        amount_raw = request.POST.get('amount_paise') or request.POST.get('amount')
        date_raw = request.POST.get('date')
        mode = (request.POST.get('mode') or 'bank_transfer').strip()
        reference = (request.POST.get('reference') or '').strip()

        customer = None
        invoice = None

        if invoice_id and str(invoice_id).strip():
            try:
                invoice = Invoice.objects.get(pk=invoice_id)
                customer = invoice.customer
            except Invoice.DoesNotExist:
                errors.append("Selected invoice does not exist.")
        elif customer_id and str(customer_id).strip():
            try:
                customer = Customer.objects.get(pk=customer_id)
            except Customer.DoesNotExist:
                errors.append("Selected customer does not exist.")
        else:
            errors.append("Please select a customer.")

        amount_paise = None
        if not amount_raw or str(amount_raw).strip() == '':
            errors.append("Please enter an amount.")
        else:
            val_clean = str(amount_raw).strip().replace('₹', '').replace(',', '')
            try:
                if '.' in val_clean:
                    amount_paise = int((Decimal(val_clean) * Decimal('100')).quantize(Decimal('1')))
                else:
                    amount_paise = int(val_clean)
                if amount_paise <= 0:
                    errors.append("Payment amount must be greater than zero.")
            except Exception:
                errors.append("Invalid payment amount entered.")

        pay_date = datetime.date.today()
        if date_raw and str(date_raw).strip():
            try:
                pay_date = datetime.date.fromisoformat(str(date_raw).strip())
            except Exception:
                pay_date = datetime.date.today()

        if not errors and (invoice or customer) and amount_paise:
            payment = record_payment(
                invoice=invoice,
                amount_paise=amount_paise,
                mode=mode,
                reference=reference,
                user=request.user,
                customer=customer,
                date=pay_date,
            )
            messages.success(request, f"Payment of {payment.amount_rupees} recorded successfully.")
            if invoice:
                return redirect('invoice_detail', pk=invoice.pk)
            return redirect('customer_outstanding')

    # GET handling / Fallback on errors
    customer_id = (
        request.GET.get('customer')
        or request.GET.get('customer_id')
        or request.POST.get('customer')
        or request.POST.get('customer_id')
    )
    invoice_id = (
        request.GET.get('invoice')
        or request.GET.get('invoice_id')
        or request.POST.get('invoice')
        or request.POST.get('invoice_id')
    )

    selected_customer = None
    selected_invoice = None

    if invoice_id:
        try:
            selected_invoice = Invoice.objects.get(pk=invoice_id)
            selected_customer = selected_invoice.customer
        except (Invoice.DoesNotExist, ValueError):
            pass

    if not selected_customer and customer_id:
        try:
            selected_customer = Customer.objects.get(pk=customer_id)
        except (Customer.DoesNotExist, ValueError):
            pass

    unpaid_invoices = []
    if selected_customer:
        candidate_invoices = (
            selected_customer.invoices.exclude(status=Invoice.STATUS_CANCELLED)
            .prefetch_related('payments')
            .order_by('date', 'id')
        )
        for inv in candidate_invoices:
            if inv.balance_paise > 0:
                unpaid_invoices.append(inv)

    customers = Customer.objects.all().order_by('name')
    today_str = datetime.date.today().isoformat()

    context = {
        'customers': customers,
        'selected_customer': selected_customer,
        'selected_invoice': selected_invoice,
        'unpaid_invoices': unpaid_invoices,
        'today_str': today_str,
        'errors': errors,
    }
    return render(request, 'sales/record_payment.html', context)


@role_required('admin', 'owner', 'accountant')
def customer_outstanding_list(request):
    """
    Customer Outstanding page: each customer with total invoiced, total paid,
    credit notes and outstanding, plus ageing buckets 0-30, 31-60, 61-90, 90+ days.
    Show amounts as rupees like ₹3,875.00.
    Accountant can view read-only; admin and owner can also record payments.
    """
    today = datetime.date.today()
    customers = Customer.objects.all().order_by('name')

    rows = []
    grand_totals = {
        'invoiced': 0,
        'paid': 0,
        'credit_notes': 0,
        'outstanding': 0,
        'bucket_0_30': 0,
        'bucket_31_60': 0,
        'bucket_61_90': 0,
        'bucket_90_plus': 0,
    }

    for c in customers:
        invoiced_paise = Invoice.objects.filter(customer=c).aggregate(total=Sum('total_paise'))['total'] or 0
        paid_paise = Payment.objects.filter(customer=c).aggregate(total=Sum('amount_paise'))['total'] or 0
        cn_paise = CreditNote.objects.filter(invoice__customer=c).aggregate(total=Sum('total_paise'))['total'] or 0
        outstanding_paise = customer_outstanding(c)
        ageing = calculate_customer_ageing(c, today=today)

        grand_totals['invoiced'] += invoiced_paise
        grand_totals['paid'] += paid_paise
        grand_totals['credit_notes'] += cn_paise
        grand_totals['outstanding'] += outstanding_paise
        grand_totals['bucket_0_30'] += ageing['bucket_0_30']
        grand_totals['bucket_31_60'] += ageing['bucket_31_60']
        grand_totals['bucket_61_90'] += ageing['bucket_61_90']
        grand_totals['bucket_90_plus'] += ageing['bucket_90_plus']

        rows.append({
            'customer': c,
            'invoiced_paise': invoiced_paise,
            'invoiced_formatted': format_inr(invoiced_paise),
            'paid_paise': paid_paise,
            'paid_formatted': format_inr(paid_paise),
            'credit_notes_paise': cn_paise,
            'credit_notes_formatted': format_inr(cn_paise),
            'outstanding_paise': outstanding_paise,
            'outstanding_formatted': format_inr(outstanding_paise),
            'bucket_0_30': ageing['bucket_0_30'],
            'bucket_0_30_formatted': ageing['bucket_0_30_formatted'],
            'bucket_31_60': ageing['bucket_31_60'],
            'bucket_31_60_formatted': ageing['bucket_31_60_formatted'],
            'bucket_61_90': ageing['bucket_61_90'],
            'bucket_61_90_formatted': ageing['bucket_61_90_formatted'],
            'bucket_90_plus': ageing['bucket_90_plus'],
            'bucket_90_plus_formatted': ageing['bucket_90_plus_formatted'],
        })

    is_accountant = (
        request.user.groups.filter(name='accountant').exists()
        and not (request.user.is_superuser or request.user.groups.filter(name__in=['admin', 'owner']).exists())
    )

    grand_totals_formatted = {
        'invoiced': format_inr(grand_totals['invoiced']),
        'paid': format_inr(grand_totals['paid']),
        'credit_notes': format_inr(grand_totals['credit_notes']),
        'outstanding': format_inr(grand_totals['outstanding']),
        'bucket_0_30': format_inr(grand_totals['bucket_0_30']),
        'bucket_31_60': format_inr(grand_totals['bucket_31_60']),
        'bucket_61_90': format_inr(grand_totals['bucket_61_90']),
        'bucket_90_plus': format_inr(grand_totals['bucket_90_plus']),
    }

    context = {
        'rows': rows,
        'grand_totals': grand_totals,
        'grand_totals_formatted': grand_totals_formatted,
        'is_accountant': is_accountant,
        'today': today,
    }
    return render(request, 'sales/customer_outstanding.html', context)

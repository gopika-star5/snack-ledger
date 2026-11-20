from __future__ import annotations

import datetime
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from core.ledger import InsufficientStock, issue_stock, reverse_movements
from core.models import CompanySettings, log_action
from .models import CreditNote, Dispatch, Invoice, InvoiceLine, InvoiceSequence, Order, Payment


class TaxResult(dict):
    """
    Structured GST calculation result supporting both dictionary and attribute access.
    """
    def __init__(self, cgst_paise: int, sgst_paise: int, igst_paise: int, total_paise: int, taxable_paise: int):
        super().__init__(
            cgst_paise=cgst_paise,
            sgst_paise=sgst_paise,
            igst_paise=igst_paise,
            total_paise=total_paise,
            taxable_paise=taxable_paise,
            cgst=cgst_paise,
            sgst=sgst_paise,
            igst=igst_paise,
            total=total_paise,
            taxable=taxable_paise,
        )

    def __getattr__(self, name: str):
        if name in self:
            return self[name]
        raise AttributeError(f"'TaxResult' object has no attribute '{name}'")


@transaction.atomic
def next_invoice_number(d: datetime.date | datetime.datetime | str | None = None) -> str:
    """
    Generate sequential invoice number:
    Indian financial year April to March, format "2026-27/0001",
    sequence resets each year, no gaps, created inside a transaction with select_for_update.
    """
    if d is None:
        d = datetime.date.today()
    elif isinstance(d, datetime.datetime):
        d = d.date()
    elif isinstance(d, str):
        d = datetime.date.fromisoformat(d)

    # Indian Financial Year: April 1 to March 31
    if d.month >= 4:
        start_year = d.year
        end_year = d.year + 1
    else:
        start_year = d.year - 1
        end_year = d.year

    fy_str = f"{start_year}-{str(end_year)[-2:]}"

    seq, created = InvoiceSequence.objects.select_for_update().get_or_create(
        financial_year=fy_str,
        defaults={'last_number': 0}
    )

    if created or seq.last_number == 0:
        max_num = 0
        for inv in Invoice.objects.filter(financial_year=fy_str):
            try:
                num_part = int(inv.number.split('/')[-1])
                if num_part > max_num:
                    max_num = num_part
            except (ValueError, IndexError):
                pass
        if max_num > seq.last_number:
            seq.last_number = max_num

    seq.last_number += 1
    seq.save(update_fields=['last_number'])

    return f"{fy_str}/{seq.last_number:04d}"


def calculate_tax(customer_state: str, taxable_paise: int, gst_rate: Decimal | int | float | str) -> TaxResult:
    """
    if customer_state equals the company state (Tamil Nadu from CompanySettings)
    then CGST + SGST (cgst = tax // 2 rounded half up, sgst = tax - cgst),
    otherwise IGST. Integer paise only.
    """
    company = CompanySettings.get()
    company_state = company.state if company and company.state else "Tamil Nadu"

    is_intra_state = (customer_state or "").strip().lower() == company_state.strip().lower()

    taxable_paise = int(taxable_paise)
    rate = Decimal(str(gst_rate))

    total_tax_paise = int(
        (Decimal(taxable_paise) * rate / Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )

    if is_intra_state:
        # cgst = tax // 2 rounded half up, sgst = tax - cgst
        cgst_paise = int(
            (Decimal(total_tax_paise) / Decimal("2")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        sgst_paise = total_tax_paise - cgst_paise
        igst_paise = 0
    else:
        cgst_paise = 0
        sgst_paise = 0
        igst_paise = total_tax_paise

    total_paise = taxable_paise + total_tax_paise

    return TaxResult(
        cgst_paise=cgst_paise,
        sgst_paise=sgst_paise,
        igst_paise=igst_paise,
        total_paise=total_paise,
        taxable_paise=taxable_paise,
    )


@transaction.atomic
def create_invoice(order: Order, user) -> Invoice:
    """
    only for orders with status approved; builds invoice lines from the order lines,
    totals, sets order status to invoiced, writes an AuditLog entry.
    """
    if order.status != Order.STATUS_APPROVED:
        raise ValidationError(f"Cannot invoice order with status '{order.status}'. Order must be approved.")

    today = datetime.date.today()
    inv_number = next_invoice_number(today)
    financial_year = inv_number.split('/')[0]
    place_of_supply = order.customer.state

    order_lines = list(order.lines.select_related('item').all())
    if not order_lines:
        raise ValidationError("Cannot create an invoice for an order with no lines.")

    total_taxable = 0
    total_cgst = 0
    total_sgst = 0
    total_igst = 0

    invoice_lines_data = []
    for o_line in order_lines:
        line_taxable = o_line.total_paise
        total_taxable += line_taxable
        line_tax = calculate_tax(
            customer_state=place_of_supply,
            taxable_paise=line_taxable,
            gst_rate=o_line.item.gst_rate_percent,
        )
        total_cgst += line_tax.cgst_paise
        total_sgst += line_tax.sgst_paise
        total_igst += line_tax.igst_paise

        invoice_lines_data.append({
            'item': o_line.item,
            'hsn': o_line.item.hsn_code,
            'qty': o_line.qty,
            'rate_paise': o_line.price_paise,
            'taxable_paise': line_taxable,
            'gst_rate': o_line.item.gst_rate_percent,
        })

    total_paise = total_taxable + total_cgst + total_sgst + total_igst

    invoice = Invoice.objects.create(
        number=inv_number,
        financial_year=financial_year,
        order=order,
        customer=order.customer,
        date=today,
        place_of_supply=place_of_supply,
        taxable_paise=total_taxable,
        cgst_paise=total_cgst,
        sgst_paise=total_sgst,
        igst_paise=total_igst,
        total_paise=total_paise,
        status=Invoice.STATUS_POSTED,
    )

    for l_data in invoice_lines_data:
        InvoiceLine.objects.create(
            invoice=invoice,
            item=l_data['item'],
            hsn=l_data['hsn'],
            qty=l_data['qty'],
            rate_paise=l_data['rate_paise'],
            taxable_paise=l_data['taxable_paise'],
            gst_rate=l_data['gst_rate'],
            lot=None,
        )

    order.status = Order.STATUS_INVOICED
    order.save(update_fields=['status'])

    log_action(
        user=user,
        action='create_invoice',
        instance=invoice,
        details={
            'order_id': order.pk,
            'invoice_number': invoice.number,
            'total_paise': invoice.total_paise,
        }
    )

    return invoice


@transaction.atomic
def dispatch_invoice(invoice: Invoice, user) -> Dispatch:
    """
    calls issue_stock with method "fefo" for each line, source_type "invoice",
    saves which lot was used on each line, creates a Dispatch, sets order status to dispatched.
    If stock is not enough, raise InsufficientStock and change nothing.
    """
    if invoice.status == Invoice.STATUS_CANCELLED:
        raise ValidationError("Cannot dispatch a cancelled invoice.")
    if invoice.dispatches.filter(status=Dispatch.STATUS_DISPATCHED).exists():
        raise ValidationError("Invoice has already been dispatched.")

    for line in invoice.lines.select_related('item').all():
        movements, total_cost = issue_stock(
            item=line.item,
            qty=line.qty,
            source_type="invoice",
            source_id=str(invoice.pk),
            user=user,
            method="fefo",
        )
        if movements:
            line.lot = movements[0].lot
            line.save(update_fields=['lot'])

    dispatch = Dispatch.objects.create(
        invoice=invoice,
        date=datetime.date.today(),
        status=Dispatch.STATUS_DISPATCHED,
    )

    if invoice.order:
        invoice.order.status = Order.STATUS_DISPATCHED
        invoice.order.save(update_fields=['status'])

    log_action(
        user=user,
        action='dispatch_invoice',
        instance=invoice,
        details={'dispatch_id': dispatch.pk}
    )

    return dispatch


@transaction.atomic
def record_payment(
    invoice: Invoice | None,
    amount_paise: int,
    mode: str,
    reference: str,
    user,
    customer=None,
) -> Payment:
    """
    Record payment against an invoice or customer.
    """
    if invoice is not None:
        customer = invoice.customer
    elif customer is None:
        raise ValueError("Either invoice or customer must be provided.")

    payment = Payment.objects.create(
        invoice=invoice,
        customer=customer,
        amount_paise=amount_paise,
        date=datetime.date.today(),
        mode=mode or 'bank_transfer',
        reference=reference or '',
    )

    log_action(
        user=user,
        action='record_payment',
        instance=payment,
        details={
            'invoice_id': invoice.pk if invoice else None,
            'customer_id': customer.pk,
            'amount_paise': amount_paise,
        }
    )

    return payment


def customer_outstanding(customer) -> int:
    """
    customer_outstanding(customer) = sum of invoice totals - payments - credit notes.
    """
    customer_id = customer.pk if hasattr(customer, 'pk') else customer

    invoices_total = Invoice.objects.filter(customer_id=customer_id).aggregate(
        total=Sum('total_paise')
    )['total'] or 0

    payments_total = Payment.objects.filter(customer_id=customer_id).aggregate(
        total=Sum('amount_paise')
    )['total'] or 0

    credit_notes_total = CreditNote.objects.filter(invoice__customer_id=customer_id).aggregate(
        total=Sum('total_paise')
    )['total'] or 0

    return int(invoices_total - payments_total - credit_notes_total)


@transaction.atomic
def cancel_invoice(invoice: Invoice, reason: str, user) -> CreditNote:
    """
    creates a CreditNote, calls reverse_movements for the dispatch,
    sets status cancelled. Never delete anything.
    """
    if invoice.status == Invoice.STATUS_CANCELLED:
        raise ValidationError("Invoice is already cancelled.")

    cn_number = f"CN-{invoice.number}"
    credit_note = CreditNote.objects.create(
        invoice=invoice,
        number=cn_number,
        reason=reason or '',
        date=datetime.date.today(),
        taxable_paise=invoice.taxable_paise,
        cgst_paise=invoice.cgst_paise,
        sgst_paise=invoice.sgst_paise,
        igst_paise=invoice.igst_paise,
        total_paise=invoice.total_paise,
    )

    # Reverse movements for dispatch
    reverse_movements(source_type="invoice", source_id=str(invoice.pk), user=user)

    for dispatch in invoice.dispatches.all():
        dispatch.status = Dispatch.STATUS_CANCELLED
        dispatch.save(update_fields=['status'])
        reverse_movements(source_type="dispatch", source_id=str(dispatch.pk), user=user)

    invoice.status = Invoice.STATUS_CANCELLED
    invoice.save(update_fields=['status'])

    log_action(
        user=user,
        action='cancel_invoice',
        instance=invoice,
        details={'credit_note_id': credit_note.pk, 'reason': reason}
    )

    return credit_note

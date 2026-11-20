import csv
import datetime
from decimal import Decimal, ROUND_HALF_UP
import io

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from django.core.exceptions import PermissionDenied
from django.db.models import Sum, Count, Q
from django.http import HttpResponse
from django.shortcuts import render

from core.decorators import role_required
from core.ledger import get_balance
from core.models import CompanySettings, Customer, Item, StockMovement
from production.models import Purchase, PurchaseLine, ProductionRun
from sales.models import Invoice, InvoiceLine, Payment
from sales.utils import format_inr, calculate_customer_ageing


def _parse_date(date_str: str | None) -> datetime.date | None:
    if not date_str:
        return None
    try:
        return datetime.date.fromisoformat(date_str.strip())
    except (ValueError, TypeError):
        return None


def _get_date_filters(request):
    from_str = request.GET.get('from_date') or request.GET.get('start_date') or ''
    to_str = request.GET.get('to_date') or request.GET.get('end_date') or ''
    from_date = _parse_date(from_str)
    to_date = _parse_date(to_str)
    return from_date, to_date, from_str, to_str


def _style_excel_sheet(ws, title, headers, rows, total_row=None):
    ws.views.sheetView[0].showGridLines = True
    num_cols = len(headers)

    # Title row
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=num_cols)
    title_cell = ws.cell(row=1, column=1, value=title)
    title_cell.font = Font(name='Calibri', size=14, bold=True, color='1E3A8A')
    title_cell.alignment = Alignment(horizontal='left', vertical='center')
    ws.row_dimensions[1].height = 25

    # Header row
    header_fill = PatternFill(start_color='1E40AF', end_color='1E40AF', fill_type='solid')
    header_font = Font(name='Calibri', size=11, bold=True, color='FFFFFF')
    for col_num, h_text in enumerate(headers, 1):
        cell = ws.cell(row=2, column=col_num, value=h_text)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[2].height = 22

    # Data rows
    thin_border = Border(
        left=Side(style='thin', color='E2E8F0'),
        right=Side(style='thin', color='E2E8F0'),
        top=Side(style='thin', color='E2E8F0'),
        bottom=Side(style='thin', color='E2E8F0'),
    )

    current_row = 3
    for row_data in rows:
        for col_num, val in enumerate(row_data, 1):
            cell = ws.cell(row=current_row, column=col_num, value=val)
            cell.border = thin_border
            cell.alignment = Alignment(vertical='center')
        current_row += 1

    # Total row
    if total_row:
        total_fill = PatternFill(start_color='F1F5F9', end_color='F1F5F9', fill_type='solid')
        total_font = Font(name='Calibri', size=11, bold=True)
        for col_num, val in enumerate(total_row, 1):
            cell = ws.cell(row=current_row, column=col_num, value=val)
            cell.font = total_font
            cell.fill = total_fill
            cell.border = thin_border
            cell.alignment = Alignment(vertical='center')
        ws.row_dimensions[current_row].height = 20

    # Auto column width
    for col_idx, col in enumerate(ws.columns, 1):
        max_len = 0
        col_letter = get_column_letter(col_idx)
        for cell in col:
            val_str = str(cell.value or '')
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Reports Index & Accountant Home
# ─────────────────────────────────────────────────────────────────────────────

@role_required('accountant', 'admin', 'owner')
def reports_index(request):
    """Reports hub page listing all available financial and audit reports."""
    reports_list = [
        {
            'title': 'Sales Register',
            'icon': '📋',
            'description': 'Invoice-by-invoice sales register showing date, customer, taxable value, CGST, SGST, IGST, total, and status.',
            'url_name': 'sales_register',
        },
        {
            'title': 'Customer & Product Sales Summary',
            'icon': '📊',
            'description': 'Aggregated sales breakdown by customer accounts and manufactured product categories.',
            'url_name': 'sales_summary',
        },
        {
            'title': 'GST Summary of Outward Supplies',
            'icon': '📑',
            'description': 'GSTR-1 outward tax liability breakdown by GST rate, split by registered (B2B) and unregistered (B2C) buyers.',
            'url_name': 'gst_summary',
        },
        {
            'title': 'Purchase Register',
            'icon': '🛒',
            'description': 'Itemized raw-material and packaging purchase transactions, supplier details, and procurement totals.',
            'url_name': 'purchase_register',
        },
        {
            'title': 'Stock Summary',
            'icon': '📦',
            'description': 'Per-item opening, receipts, issues, adjustments and closing balance — closing verified against the live ledger.',
            'url_name': 'stock_summary',
        },
        {
            'title': 'Stock Ledger',
            'icon': '📒',
            'description': 'Every stock movement with date, item, batch, qty in/out, source document link and user. Filterable by item.',
            'url_name': 'stock_ledger',
        },
        {
            'title': 'Production Report',
            'icon': '🏭',
            'description': 'Planned vs actual consumption, yield, wastage and variance flags per production run.',
            'url_name': 'production_report',
        },
        {
            'title': 'Collections & Outstanding',
            'icon': '💰',
            'description': 'Payments collected in period and outstanding receivables by customer with 0–30, 31–60, 61–90 and 90+ day ageing buckets.',
            'url_name': 'collections_outstanding',
        },
        {
            'title': 'Owner Dashboard',
            'icon': '🎯',
            'description': 'Today\'s orders, stock levels with low-stock alerts, variance alerts, latest cost per packet and total outstanding dues.',
            'url_name': 'owner_dashboard',
        },
    ]
    return render(request, 'reports/reports_index.html', {'reports': reports_list})


@role_required('accountant')
def accountant_home(request):
    """Accountant / Auditor home page with quick links to financial reports."""
    return render(request, 'reports/accountant_home.html')


# ─────────────────────────────────────────────────────────────────────────────
# 2. Sales Register
# ─────────────────────────────────────────────────────────────────────────────

@role_required('accountant', 'admin', 'owner')
def sales_register(request):
    """
    Sales Register report:
    Invoice number, date, customer, taxable, CGST, SGST, IGST, total, status.
    Filterable by date range; exportable to CSV and Excel (openpyxl).
    """
    from_date, to_date, from_str, to_str = _get_date_filters(request)

    qs = Invoice.objects.select_related('customer').order_by('-date', '-id')
    if from_date:
        qs = qs.filter(date__gte=from_date)
    if to_date:
        qs = qs.filter(date__lte=to_date)

    invoices = list(qs)

    totals = {
        'taxable_paise': sum(inv.taxable_paise for inv in invoices),
        'cgst_paise': sum(inv.cgst_paise for inv in invoices),
        'sgst_paise': sum(inv.sgst_paise for inv in invoices),
        'igst_paise': sum(inv.igst_paise for inv in invoices),
        'total_paise': sum(inv.total_paise for inv in invoices),
        'count': len(invoices),
    }

    export_format = request.GET.get('export', '').lower()

    if export_format == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="sales_register_{datetime.date.today()}.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'Invoice Number', 'Date', 'Customer', 'Taxable (₹)',
            'CGST (₹)', 'SGST (₹)', 'IGST (₹)', 'Total (₹)', 'Status'
        ])
        for inv in invoices:
            writer.writerow([
                inv.number,
                inv.date.isoformat(),
                inv.customer.name,
                f"{inv.taxable_paise / 100:.2f}",
                f"{inv.cgst_paise / 100:.2f}",
                f"{inv.sgst_paise / 100:.2f}",
                f"{inv.igst_paise / 100:.2f}",
                f"{inv.total_paise / 100:.2f}",
                inv.get_status_display(),
            ])
        writer.writerow([
            'Total', '', f"{totals['count']} Invoices",
            f"{totals['taxable_paise'] / 100:.2f}",
            f"{totals['cgst_paise'] / 100:.2f}",
            f"{totals['sgst_paise'] / 100:.2f}",
            f"{totals['igst_paise'] / 100:.2f}",
            f"{totals['total_paise'] / 100:.2f}",
            ''
        ])
        return response

    if export_format == 'excel':
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sales Register"

        headers = [
            'Invoice Number', 'Date', 'Customer', 'Taxable (₹)',
            'CGST (₹)', 'SGST (₹)', 'IGST (₹)', 'Total (₹)', 'Status'
        ]
        rows = [
            [
                inv.number,
                inv.date.isoformat(),
                inv.customer.name,
                float(Decimal(inv.taxable_paise) / Decimal(100)),
                float(Decimal(inv.cgst_paise) / Decimal(100)),
                float(Decimal(inv.sgst_paise) / Decimal(100)),
                float(Decimal(inv.igst_paise) / Decimal(100)),
                float(Decimal(inv.total_paise) / Decimal(100)),
                inv.get_status_display(),
            ]
            for inv in invoices
        ]
        total_row = [
            'Total', '', f"{totals['count']} Invoices",
            float(Decimal(totals['taxable_paise']) / Decimal(100)),
            float(Decimal(totals['cgst_paise']) / Decimal(100)),
            float(Decimal(totals['sgst_paise']) / Decimal(100)),
            float(Decimal(totals['igst_paise']) / Decimal(100)),
            float(Decimal(totals['total_paise']) / Decimal(100)),
            ''
        ]
        _style_excel_sheet(ws, "Sales Register", headers, rows, total_row)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        response = HttpResponse(
            output.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="sales_register_{datetime.date.today()}.xlsx"'
        return response

    context = {
        'invoices': invoices,
        'totals': totals,
        'from_date': from_str,
        'to_date': to_str,
    }
    return render(request, 'reports/sales_register.html', context)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Customer-wise & Product-wise Sales Summary
# ─────────────────────────────────────────────────────────────────────────────

@role_required('accountant', 'admin', 'owner')
def sales_summary(request):
    """
    Customer-wise and product-wise sales summary report.
    Filterable by date range; exportable to CSV and Excel (openpyxl).
    """
    from_date, to_date, from_str, to_str = _get_date_filters(request)

    inv_qs = Invoice.objects.filter(status=Invoice.STATUS_POSTED).select_related('customer')
    if from_date:
        inv_qs = inv_qs.filter(date__gte=from_date)
    if to_date:
        inv_qs = inv_qs.filter(date__lte=to_date)

    invoices = list(inv_qs)

    # ── Customer-wise Aggregation ──
    customer_map = {}
    for inv in invoices:
        cid = inv.customer_id
        if cid not in customer_map:
            customer_map[cid] = {
                'customer': inv.customer,
                'invoices_count': 0,
                'taxable_paise': 0,
                'cgst_paise': 0,
                'sgst_paise': 0,
                'igst_paise': 0,
                'total_tax_paise': 0,
                'total_paise': 0,
            }
        c = customer_map[cid]
        c['invoices_count'] += 1
        c['taxable_paise'] += inv.taxable_paise
        c['cgst_paise'] += inv.cgst_paise
        c['sgst_paise'] += inv.sgst_paise
        c['igst_paise'] += inv.igst_paise
        c['total_tax_paise'] += (inv.cgst_paise + inv.sgst_paise + inv.igst_paise)
        c['total_paise'] += inv.total_paise

    customer_summaries = sorted(customer_map.values(), key=lambda x: x['total_paise'], reverse=True)

    customer_totals = {
        'invoices_count': sum(c['invoices_count'] for c in customer_summaries),
        'taxable_paise': sum(c['taxable_paise'] for c in customer_summaries),
        'cgst_paise': sum(c['cgst_paise'] for c in customer_summaries),
        'sgst_paise': sum(c['sgst_paise'] for c in customer_summaries),
        'igst_paise': sum(c['igst_paise'] for c in customer_summaries),
        'total_tax_paise': sum(c['total_tax_paise'] for c in customer_summaries),
        'total_paise': sum(c['total_paise'] for c in customer_summaries),
    }

    # ── Product-wise Aggregation ──
    lines_qs = InvoiceLine.objects.filter(invoice__in=invoices).select_related('item', 'item__stock_unit', 'invoice')
    product_map = {}
    for line in lines_qs:
        item = line.item
        iid = item.id
        if iid not in product_map:
            product_map[iid] = {
                'item': item,
                'qty': Decimal('0'),
                'taxable_paise': 0,
                'total_tax_paise': 0,
                'total_paise': 0,
            }
        p = product_map[iid]
        p['qty'] += line.qty
        p['taxable_paise'] += line.taxable_paise

        # Line tax calculation
        rate = Decimal(str(line.gst_rate))
        tax = int((Decimal(line.taxable_paise) * rate / Decimal('100')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        p['total_tax_paise'] += tax
        p['total_paise'] += (line.taxable_paise + tax)

    product_summaries = sorted(product_map.values(), key=lambda x: x['total_paise'], reverse=True)

    product_totals = {
        'qty': sum((p['qty'] for p in product_summaries), Decimal('0')),
        'taxable_paise': sum(p['taxable_paise'] for p in product_summaries),
        'total_tax_paise': sum(p['total_tax_paise'] for p in product_summaries),
        'total_paise': sum(p['total_paise'] for p in product_summaries),
    }

    export_format = request.GET.get('export', '').lower()

    if export_format == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="sales_summary_{datetime.date.today()}.csv"'
        writer = csv.writer(response)

        # Customer Section
        writer.writerow(['CUSTOMER-WISE SALES SUMMARY'])
        writer.writerow([
            'Customer', 'Type', 'GSTIN', 'State', 'Invoices',
            'Taxable (₹)', 'CGST (₹)', 'SGST (₹)', 'IGST (₹)', 'Total Tax (₹)', 'Total (₹)'
        ])
        for c in customer_summaries:
            cust = c['customer']
            writer.writerow([
                cust.name,
                cust.get_customer_type_display(),
                cust.gstin or '—',
                cust.state,
                c['invoices_count'],
                f"{c['taxable_paise'] / 100:.2f}",
                f"{c['cgst_paise'] / 100:.2f}",
                f"{c['sgst_paise'] / 100:.2f}",
                f"{c['igst_paise'] / 100:.2f}",
                f"{c['total_tax_paise'] / 100:.2f}",
                f"{c['total_paise'] / 100:.2f}",
            ])
        writer.writerow([
            'Total', '', '', '', customer_totals['invoices_count'],
            f"{customer_totals['taxable_paise'] / 100:.2f}",
            f"{customer_totals['cgst_paise'] / 100:.2f}",
            f"{customer_totals['sgst_paise'] / 100:.2f}",
            f"{customer_totals['igst_paise'] / 100:.2f}",
            f"{customer_totals['total_tax_paise'] / 100:.2f}",
            f"{customer_totals['total_paise'] / 100:.2f}",
        ])

        writer.writerow([])
        writer.writerow([])

        # Product Section
        writer.writerow(['PRODUCT-WISE SALES SUMMARY'])
        writer.writerow(['Product', 'Type', 'HSN', 'Qty Sold', 'Unit', 'Taxable (₹)', 'Tax (₹)', 'Total (₹)'])
        for p in product_summaries:
            item = p['item']
            writer.writerow([
                item.name,
                item.get_item_type_display(),
                item.hsn_code or '—',
                f"{p['qty']}",
                item.stock_unit.code,
                f"{p['taxable_paise'] / 100:.2f}",
                f"{p['total_tax_paise'] / 100:.2f}",
                f"{p['total_paise'] / 100:.2f}",
            ])
        writer.writerow([
            'Total', '', '', f"{product_totals['qty']}", '',
            f"{product_totals['taxable_paise'] / 100:.2f}",
            f"{product_totals['total_tax_paise'] / 100:.2f}",
            f"{product_totals['total_paise'] / 100:.2f}",
        ])
        return response

    if export_format == 'excel':
        wb = openpyxl.Workbook()

        # Sheet 1: Customer Summary
        ws1 = wb.active
        ws1.title = "Customer Sales Summary"
        c_headers = [
            'Customer', 'Type', 'GSTIN', 'State', 'Invoices',
            'Taxable (₹)', 'CGST (₹)', 'SGST (₹)', 'IGST (₹)', 'Total Tax (₹)', 'Total (₹)'
        ]
        c_rows = [
            [
                c['customer'].name,
                c['customer'].get_customer_type_display(),
                c['customer'].gstin or '—',
                c['customer'].state,
                c['invoices_count'],
                float(Decimal(c['taxable_paise']) / Decimal(100)),
                float(Decimal(c['cgst_paise']) / Decimal(100)),
                float(Decimal(c['sgst_paise']) / Decimal(100)),
                float(Decimal(c['igst_paise']) / Decimal(100)),
                float(Decimal(c['total_tax_paise']) / Decimal(100)),
                float(Decimal(c['total_paise']) / Decimal(100)),
            ]
            for c in customer_summaries
        ]
        c_total_row = [
            'Total', '', '', '', customer_totals['invoices_count'],
            float(Decimal(customer_totals['taxable_paise']) / Decimal(100)),
            float(Decimal(customer_totals['cgst_paise']) / Decimal(100)),
            float(Decimal(customer_totals['sgst_paise']) / Decimal(100)),
            float(Decimal(customer_totals['igst_paise']) / Decimal(100)),
            float(Decimal(customer_totals['total_tax_paise']) / Decimal(100)),
            float(Decimal(customer_totals['total_paise']) / Decimal(100)),
        ]
        _style_excel_sheet(ws1, "Customer-wise Sales Summary", c_headers, c_rows, c_total_row)

        # Sheet 2: Product Summary
        ws2 = wb.create_sheet(title="Product Sales Summary")
        p_headers = ['Product', 'Type', 'HSN', 'Qty Sold', 'Unit', 'Taxable (₹)', 'Tax (₹)', 'Total (₹)']
        p_rows = [
            [
                p['item'].name,
                p['item'].get_item_type_display(),
                p['item'].hsn_code or '—',
                float(p['qty']),
                p['item'].stock_unit.code,
                float(Decimal(p['taxable_paise']) / Decimal(100)),
                float(Decimal(p['total_tax_paise']) / Decimal(100)),
                float(Decimal(p['total_paise']) / Decimal(100)),
            ]
            for p in product_summaries
        ]
        p_total_row = [
            'Total', '', '', float(product_totals['qty']), '',
            float(Decimal(product_totals['taxable_paise']) / Decimal(100)),
            float(Decimal(product_totals['total_tax_paise']) / Decimal(100)),
            float(Decimal(product_totals['total_paise']) / Decimal(100)),
        ]
        _style_excel_sheet(ws2, "Product-wise Sales Summary", p_headers, p_rows, p_total_row)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        response = HttpResponse(
            output.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="sales_summary_{datetime.date.today()}.xlsx"'
        return response

    context = {
        'customer_summaries': customer_summaries,
        'customer_totals': customer_totals,
        'product_summaries': product_summaries,
        'product_totals': product_totals,
        'from_date': from_str,
        'to_date': to_str,
    }
    return render(request, 'reports/sales_summary.html', context)


# ─────────────────────────────────────────────────────────────────────────────
# 4. GST Summary of Outward Supplies
# ─────────────────────────────────────────────────────────────────────────────

@role_required('accountant', 'admin', 'owner')
def gst_summary(request):
    """
    GST summary of outward supplies:
    By GST rate, taxable value, CGST, SGST, IGST,
    split by customers with GSTIN (Registered / B2B) and without (Unregistered / B2C).
    Filterable by date range; exportable to CSV and Excel (openpyxl).
    """
    from_date, to_date, from_str, to_str = _get_date_filters(request)

    inv_qs = Invoice.objects.filter(status=Invoice.STATUS_POSTED).select_related('customer')
    if from_date:
        inv_qs = inv_qs.filter(date__gte=from_date)
    if to_date:
        inv_qs = inv_qs.filter(date__lte=to_date)

    invoices = list(inv_qs)

    company = CompanySettings.get()
    company_state = (company.state or 'Tamil Nadu').strip().lower()

    lines = list(InvoiceLine.objects.filter(invoice__in=invoices).select_related('invoice', 'invoice__customer'))

    b2b_groups = {}
    b2c_groups = {}

    def _init_rate_entry(rate_decimal):
        return {
            'rate': rate_decimal,
            'rate_display': f"{rate_decimal:.2f}%",
            'taxable_paise': 0,
            'cgst_paise': 0,
            'sgst_paise': 0,
            'igst_paise': 0,
            'total_tax_paise': 0,
            'total_paise': 0,
        }

    # Aggregate lines
    for line in lines:
        cust = line.invoice.customer
        has_gstin = bool(cust.gstin and cust.gstin.strip())
        target_map = b2b_groups if has_gstin else b2c_groups

        rate = Decimal(str(line.gst_rate))
        if rate not in target_map:
            target_map[rate] = _init_rate_entry(rate)

        entry = target_map[rate]
        taxable = int(line.taxable_paise)
        entry['taxable_paise'] += taxable

        # Tax calculation
        place_of_supply = (line.invoice.place_of_supply or cust.state or '').strip().lower()
        is_intra_state = (place_of_supply == company_state)

        tax = int((Decimal(taxable) * rate / Decimal('100')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        if is_intra_state:
            cgst = int((Decimal(tax) / Decimal('2')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            sgst = tax - cgst
            igst = 0
        else:
            cgst = 0
            sgst = 0
            igst = tax

        entry['cgst_paise'] += cgst
        entry['sgst_paise'] += sgst
        entry['igst_paise'] += igst
        entry['total_tax_paise'] += tax
        entry['total_paise'] += (taxable + tax)

    # Invoices without lines fallback
    invoices_with_lines = {l.invoice_id for l in lines}
    for inv in invoices:
        if inv.id not in invoices_with_lines:
            cust = inv.customer
            has_gstin = bool(cust.gstin and cust.gstin.strip())
            target_map = b2b_groups if has_gstin else b2c_groups

            # Default to 5.00%
            rate = Decimal('5.00')
            if rate not in target_map:
                target_map[rate] = _init_rate_entry(rate)

            entry = target_map[rate]
            entry['taxable_paise'] += inv.taxable_paise
            entry['cgst_paise'] += inv.cgst_paise
            entry['sgst_paise'] += inv.sgst_paise
            entry['igst_paise'] += inv.igst_paise
            entry['total_tax_paise'] += (inv.cgst_paise + inv.sgst_paise + inv.igst_paise)
            entry['total_paise'] += inv.total_paise

    b2b_rows = sorted(b2b_groups.values(), key=lambda x: x['rate'])
    b2c_rows = sorted(b2c_groups.values(), key=lambda x: x['rate'])

    def _calc_totals(rows):
        return {
            'taxable_paise': sum(r['taxable_paise'] for r in rows),
            'cgst_paise': sum(r['cgst_paise'] for r in rows),
            'sgst_paise': sum(r['sgst_paise'] for r in rows),
            'igst_paise': sum(r['igst_paise'] for r in rows),
            'total_tax_paise': sum(r['total_tax_paise'] for r in rows),
            'total_paise': sum(r['total_paise'] for r in rows),
        }

    b2b_totals = _calc_totals(b2b_rows)
    b2c_totals = _calc_totals(b2c_rows)

    grand_totals = {
        'taxable_paise': b2b_totals['taxable_paise'] + b2c_totals['taxable_paise'],
        'cgst_paise': b2b_totals['cgst_paise'] + b2c_totals['cgst_paise'],
        'sgst_paise': b2b_totals['sgst_paise'] + b2c_totals['sgst_paise'],
        'igst_paise': b2b_totals['igst_paise'] + b2c_totals['igst_paise'],
        'total_tax_paise': b2b_totals['total_tax_paise'] + b2c_totals['total_tax_paise'],
        'total_paise': b2b_totals['total_paise'] + b2c_totals['total_paise'],
    }

    export_format = request.GET.get('export', '').lower()

    if export_format == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="gst_summary_{datetime.date.today()}.csv"'
        writer = csv.writer(response)

        # B2B Section
        writer.writerow(['GST OUTWARD SUPPLIES - REGISTERED PERSONS (WITH GSTIN / B2B)'])
        writer.writerow(['GST Rate', 'Taxable Value (₹)', 'CGST (₹)', 'SGST (₹)', 'IGST (₹)', 'Total Tax (₹)', 'Total Value (₹)'])
        for r in b2b_rows:
            writer.writerow([
                r['rate_display'],
                f"{r['taxable_paise'] / 100:.2f}",
                f"{r['cgst_paise'] / 100:.2f}",
                f"{r['sgst_paise'] / 100:.2f}",
                f"{r['igst_paise'] / 100:.2f}",
                f"{r['total_tax_paise'] / 100:.2f}",
                f"{r['total_paise'] / 100:.2f}",
            ])
        writer.writerow([
            'Subtotal B2B',
            f"{b2b_totals['taxable_paise'] / 100:.2f}",
            f"{b2b_totals['cgst_paise'] / 100:.2f}",
            f"{b2b_totals['sgst_paise'] / 100:.2f}",
            f"{b2b_totals['igst_paise'] / 100:.2f}",
            f"{b2b_totals['total_tax_paise'] / 100:.2f}",
            f"{b2b_totals['total_paise'] / 100:.2f}",
        ])

        writer.writerow([])

        # B2C Section
        writer.writerow(['GST OUTWARD SUPPLIES - UNREGISTERED PERSONS (WITHOUT GSTIN / B2C)'])
        writer.writerow(['GST Rate', 'Taxable Value (₹)', 'CGST (₹)', 'SGST (₹)', 'IGST (₹)', 'Total Tax (₹)', 'Total Value (₹)'])
        for r in b2c_rows:
            writer.writerow([
                r['rate_display'],
                f"{r['taxable_paise'] / 100:.2f}",
                f"{r['cgst_paise'] / 100:.2f}",
                f"{r['sgst_paise'] / 100:.2f}",
                f"{r['igst_paise'] / 100:.2f}",
                f"{r['total_tax_paise'] / 100:.2f}",
                f"{r['total_paise'] / 100:.2f}",
            ])
        writer.writerow([
            'Subtotal B2C',
            f"{b2c_totals['taxable_paise'] / 100:.2f}",
            f"{b2c_totals['cgst_paise'] / 100:.2f}",
            f"{b2c_totals['sgst_paise'] / 100:.2f}",
            f"{b2c_totals['igst_paise'] / 100:.2f}",
            f"{b2c_totals['total_tax_paise'] / 100:.2f}",
            f"{b2c_totals['total_paise'] / 100:.2f}",
        ])

        writer.writerow([])

        # Grand Total
        writer.writerow([
            'GRAND TOTAL',
            f"{grand_totals['taxable_paise'] / 100:.2f}",
            f"{grand_totals['cgst_paise'] / 100:.2f}",
            f"{grand_totals['sgst_paise'] / 100:.2f}",
            f"{grand_totals['igst_paise'] / 100:.2f}",
            f"{grand_totals['total_tax_paise'] / 100:.2f}",
            f"{grand_totals['total_paise'] / 100:.2f}",
        ])
        return response

    if export_format == 'excel':
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "GST Summary"

        headers = ['Category / GST Rate', 'Taxable Value (₹)', 'CGST (₹)', 'SGST (₹)', 'IGST (₹)', 'Total Tax (₹)', 'Total Value (₹)']
        rows = []

        # B2B
        rows.append(['── Registered (With GSTIN / B2B) ──', '', '', '', '', '', ''])
        for r in b2b_rows:
            rows.append([
                r['rate_display'],
                float(Decimal(r['taxable_paise']) / Decimal(100)),
                float(Decimal(r['cgst_paise']) / Decimal(100)),
                float(Decimal(r['sgst_paise']) / Decimal(100)),
                float(Decimal(r['igst_paise']) / Decimal(100)),
                float(Decimal(r['total_tax_paise']) / Decimal(100)),
                float(Decimal(r['total_paise']) / Decimal(100)),
            ])
        rows.append([
            'Subtotal (With GSTIN)',
            float(Decimal(b2b_totals['taxable_paise']) / Decimal(100)),
            float(Decimal(b2b_totals['cgst_paise']) / Decimal(100)),
            float(Decimal(b2b_totals['sgst_paise']) / Decimal(100)),
            float(Decimal(b2b_totals['igst_paise']) / Decimal(100)),
            float(Decimal(b2b_totals['total_tax_paise']) / Decimal(100)),
            float(Decimal(b2b_totals['total_paise']) / Decimal(100)),
        ])

        # B2C
        rows.append(['── Unregistered (Without GSTIN / B2C) ──', '', '', '', '', '', ''])
        for r in b2c_rows:
            rows.append([
                r['rate_display'],
                float(Decimal(r['taxable_paise']) / Decimal(100)),
                float(Decimal(r['cgst_paise']) / Decimal(100)),
                float(Decimal(r['sgst_paise']) / Decimal(100)),
                float(Decimal(r['igst_paise']) / Decimal(100)),
                float(Decimal(r['total_tax_paise']) / Decimal(100)),
                float(Decimal(r['total_paise']) / Decimal(100)),
            ])
        rows.append([
            'Subtotal (Without GSTIN)',
            float(Decimal(b2c_totals['taxable_paise']) / Decimal(100)),
            float(Decimal(b2c_totals['cgst_paise']) / Decimal(100)),
            float(Decimal(b2c_totals['sgst_paise']) / Decimal(100)),
            float(Decimal(b2c_totals['igst_paise']) / Decimal(100)),
            float(Decimal(b2c_totals['total_tax_paise']) / Decimal(100)),
            float(Decimal(b2c_totals['total_paise']) / Decimal(100)),
        ])

        total_row = [
            'GRAND TOTAL',
            float(Decimal(grand_totals['taxable_paise']) / Decimal(100)),
            float(Decimal(grand_totals['cgst_paise']) / Decimal(100)),
            float(Decimal(grand_totals['sgst_paise']) / Decimal(100)),
            float(Decimal(grand_totals['igst_paise']) / Decimal(100)),
            float(Decimal(grand_totals['total_tax_paise']) / Decimal(100)),
            float(Decimal(grand_totals['total_paise']) / Decimal(100)),
        ]
        _style_excel_sheet(ws, "GST Summary of Outward Supplies (GSTR-1 Format)", headers, rows, total_row)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        response = HttpResponse(
            output.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="gst_summary_{datetime.date.today()}.xlsx"'
        return response

    context = {
        'b2b_rows': b2b_rows,
        'b2b_totals': b2b_totals,
        'b2c_rows': b2c_rows,
        'b2c_totals': b2c_totals,
        'grand_totals': grand_totals,
        'from_date': from_str,
        'to_date': to_str,
    }
    return render(request, 'reports/gst_summary.html', context)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Purchase Register
# ─────────────────────────────────────────────────────────────────────────────

@role_required('accountant', 'admin', 'owner')
def purchase_register(request):
    """
    Purchase Register report:
    Purchase ID, date, supplier, supplier GSTIN, items purchased, total amount, note.
    Filterable by date range; exportable to CSV and Excel (openpyxl).
    """
    from_date, to_date, from_str, to_str = _get_date_filters(request)

    qs = Purchase.objects.select_related('supplier', 'created_by').prefetch_related('lines__item', 'lines__item__stock_unit').order_by('-date', '-id')
    if from_date:
        qs = qs.filter(date__gte=from_date)
    if to_date:
        qs = qs.filter(date__lte=to_date)

    purchases = list(qs)

    totals = {
        'count': len(purchases),
        'total_paise': sum(p.total_paise for p in purchases),
    }

    export_format = request.GET.get('export', '').lower()

    if export_format == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="purchase_register_{datetime.date.today()}.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'Purchase #', 'Date', 'Supplier', 'Supplier GSTIN', 'State',
            'Items Purchased', 'Total (₹)', 'Note', 'Created By'
        ])
        for p in purchases:
            items_str = ", ".join(f"{l.item.name} ({l.qty_in_stock_unit} {l.item.stock_unit.code})" for l in p.lines.all())
            writer.writerow([
                f"#{p.id}",
                p.date.isoformat(),
                p.supplier.name,
                p.supplier.gstin or '—',
                p.supplier.state,
                items_str,
                f"{p.total_paise / 100:.2f}",
                p.note or '—',
                p.created_by.username if p.created_by else '—',
            ])
        writer.writerow([
            'Total', '', f"{totals['count']} Purchases", '', '', '',
            f"{totals['total_paise'] / 100:.2f}", '', ''
        ])
        return response

    if export_format == 'excel':
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Purchase Register"

        headers = [
            'Purchase #', 'Date', 'Supplier', 'Supplier GSTIN', 'State',
            'Items Purchased', 'Total (₹)', 'Note', 'Created By'
        ]
        rows = []
        for p in purchases:
            items_str = ", ".join(f"{l.item.name} ({l.qty_in_stock_unit} {l.item.stock_unit.code})" for l in p.lines.all())
            rows.append([
                f"#{p.id}",
                p.date.isoformat(),
                p.supplier.name,
                p.supplier.gstin or '—',
                p.supplier.state,
                items_str,
                float(Decimal(p.total_paise) / Decimal(100)),
                p.note or '—',
                p.created_by.username if p.created_by else '—',
            ])
        total_row = [
            'Total', '', f"{totals['count']} Purchases", '', '', '',
            float(Decimal(totals['total_paise']) / Decimal(100)), '', ''
        ]
        _style_excel_sheet(ws, "Purchase Register", headers, rows, total_row)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        response = HttpResponse(
            output.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="purchase_register_{datetime.date.today()}.xlsx"'
        return response

    context = {
        'purchases': purchases,
        'totals': totals,
        'from_date': from_str,
        'to_date': to_str,
    }
    return render(request, 'reports/purchase_register.html', context)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Stock Summary
# ─────────────────────────────────────────────────────────────────────────────

@role_required('accountant', 'admin', 'owner')
def stock_summary(request):
    from_date, to_date, from_str, to_str = _get_date_filters(request)

    items = Item.objects.select_related('stock_unit').filter(active=True).order_by('item_type', 'name')
    rows = []
    for item in items:
        if from_date:
            prev = from_date - datetime.timedelta(days=1)
            opening = get_balance(item, as_of=prev)
        else:
            opening = Decimal('0')

        qs = StockMovement.objects.filter(item=item)
        if from_date:
            qs = qs.filter(created_at__date__gte=from_date)
        if to_date:
            qs = qs.filter(created_at__date__lte=to_date)

        receipts = Decimal(str(qs.filter(qty__gt=0).aggregate(total=Sum('qty'))['total'] or Decimal('0')))
        issues_neg = Decimal(str(
            qs.filter(qty__lt=0, source_type__in=['production', 'dispatch', 'invoice_dispatch'])
            .aggregate(total=Sum('qty'))['total'] or Decimal('0')
        ))
        adj_neg = Decimal(str(
            qs.exclude(source_type__in=['opening', 'production', 'dispatch', 'invoice_dispatch', 'purchase'])
            .filter(qty__lt=0).aggregate(total=Sum('qty'))['total'] or Decimal('0')
        ))

        if from_date:
            closing_ledger = get_balance(item, as_of=to_date) if to_date else get_balance(item)
        else:
            closing_ledger = get_balance(item)

        rows.append({
            'item': item,
            'opening': opening,
            'receipts': receipts,
            'issues': abs(issues_neg),
            'adjustments': abs(adj_neg),
            'closing': closing_ledger,
            'unit': item.stock_unit.code,
        })

    export_format = request.GET.get('export', '').lower()

    if export_format == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="stock_summary_{datetime.date.today()}.csv"'
        writer = csv.writer(response)
        writer.writerow(['Item', 'Type', 'Unit', 'Opening', 'Receipts', 'Issues', 'Adjustments', 'Closing'])
        for r in rows:
            writer.writerow([
                r['item'].name, r['item'].get_item_type_display(), r['unit'],
                f"{r['opening']:.3f}", f"{r['receipts']:.3f}",
                f"{r['issues']:.3f}", f"{r['adjustments']:.3f}", f"{r['closing']:.3f}",
            ])
        return response

    if export_format == 'excel':
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Stock Summary"
        headers = ['Item', 'Type', 'Unit', 'Opening', 'Receipts', 'Issues', 'Adjustments', 'Closing']
        data_rows = [[r['item'].name, r['item'].get_item_type_display(), r['unit'],
                      float(r['opening']), float(r['receipts']), float(r['issues']),
                      float(r['adjustments']), float(r['closing'])] for r in rows]
        _style_excel_sheet(ws, "Stock Summary", headers, data_rows)
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        response = HttpResponse(output.getvalue(),
                                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="stock_summary_{datetime.date.today()}.xlsx"'
        return response

    return render(request, 'reports/stock_summary.html', {
        'rows': rows, 'from_date': from_str, 'to_date': to_str,
    })


# ─────────────────────────────────────────────────────────────────────────────
# 7. Stock Ledger
# ─────────────────────────────────────────────────────────────────────────────

def _source_url(source_type, source_id):
    try:
        if source_type == 'production':
            from django.urls import reverse
            return reverse('run_detail', args=[source_id])
        elif source_type in ('dispatch', 'invoice_dispatch'):
            from sales.models import Dispatch
            d = Dispatch.objects.filter(pk=source_id).first()
            if d:
                from django.urls import reverse
                return reverse('invoice_detail', args=[d.invoice_id])
        elif source_type == 'purchase':
            from django.urls import reverse
            return reverse('purchase_list')
    except Exception:
        pass
    return None


@role_required('accountant', 'admin', 'owner')
def stock_ledger(request):
    from_date, to_date, from_str, to_str = _get_date_filters(request)
    item_id = request.GET.get('item_id', '')

    items = Item.objects.select_related('stock_unit').filter(active=True).order_by('name')

    qs = StockMovement.objects.select_related(
        'item', 'item__stock_unit', 'lot', 'created_by'
    ).order_by('created_at', 'id')

    if item_id:
        qs = qs.filter(item_id=item_id)
    if from_date:
        qs = qs.filter(created_at__date__gte=from_date)
    if to_date:
        qs = qs.filter(created_at__date__lte=to_date)

    movements = list(qs)
    for m in movements:
        m.source_url = _source_url(m.source_type, m.source_id)

    export_format = request.GET.get('export', '').lower()

    if export_format == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="stock_ledger_{datetime.date.today()}.csv"'
        writer = csv.writer(response)
        writer.writerow(['Date', 'Item', 'Batch', 'Qty In', 'Qty Out', 'Source Type', 'Source ID', 'User'])
        for m in movements:
            writer.writerow([
                m.created_at.date().isoformat(), m.item.name,
                m.lot.batch_no if m.lot else '',
                f"{m.qty:.3f}" if m.qty > 0 else '',
                f"{abs(m.qty):.3f}" if m.qty < 0 else '',
                m.source_type, m.source_id or '',
                m.created_by.username if m.created_by else '',
            ])
        return response

    if export_format == 'excel':
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Stock Ledger"
        headers = ['Date', 'Item', 'Unit', 'Batch', 'Qty In', 'Qty Out', 'Source Type', 'User']
        data_rows = [[
            m.created_at.date().isoformat(), m.item.name, m.item.stock_unit.code,
            m.lot.batch_no if m.lot else '',
            float(m.qty) if m.qty > 0 else '',
            float(abs(m.qty)) if m.qty < 0 else '',
            m.source_type, m.created_by.username if m.created_by else '',
        ] for m in movements]
        _style_excel_sheet(ws, "Stock Ledger", headers, data_rows)
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        response = HttpResponse(output.getvalue(),
                                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="stock_ledger_{datetime.date.today()}.xlsx"'
        return response

    return render(request, 'reports/stock_ledger.html', {
        'movements': movements, 'items': items,
        'selected_item_id': item_id,
        'from_date': from_str, 'to_date': to_str, 'count': len(movements),
    })


# ─────────────────────────────────────────────────────────────────────────────
# 8. Production Report
# ─────────────────────────────────────────────────────────────────────────────

@role_required('accountant', 'admin', 'owner')
def production_report(request):
    from_date, to_date, from_str, to_str = _get_date_filters(request)

    from production.models import ProductionRun
    qs = ProductionRun.objects.filter(status='posted').select_related(
        'bom__finished_item', 'created_by'
    ).prefetch_related('issues__item', 'variance_lines__item').order_by('-run_date', '-id')

    if from_date:
        qs = qs.filter(run_date__gte=from_date)
    if to_date:
        qs = qs.filter(run_date__lte=to_date)

    rows = []
    for run in qs:
        expected_yield = run.bom.batch_yield * Decimal(str(run.batches))
        actual_yield = Decimal(str(run.actual_good_output))
        wastage = Decimal(str(run.damaged_pouches))
        variance_lines = list(run.variance_lines.select_related('item').all())
        rows.append({
            'run': run,
            'product': run.bom.finished_item.name,
            'run_date': run.run_date,
            'batches': run.batches,
            'expected_yield': expected_yield,
            'actual_yield': actual_yield,
            'wastage': wastage,
            'yield_variance_percent': run.yield_variance_percent,
            'yield_flagged': run.yield_flagged,
            'variance_lines': variance_lines,
            'material_cost_formatted': format_inr(run.material_cost_paise),
            'total_cost_formatted': format_inr(run.total_cost_paise),
            'cost_per_packet_formatted': format_inr(run.cost_per_packet_paise),
        })

    export_format = request.GET.get('export', '').lower()

    if export_format == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="production_report_{datetime.date.today()}.csv"'
        writer = csv.writer(response)
        writer.writerow(['Run #', 'Date', 'Product', 'Batches', 'Expected Yield', 'Actual Yield',
                         'Wastage', 'Yield Variance %', 'Yield Flagged',
                         'Material Cost (Rs)', 'Total Cost (Rs)', 'Cost/Packet (Rs)'])
        for r in rows:
            run = r['run']
            writer.writerow([
                f"#{run.pk}", r['run_date'].isoformat(), r['product'], f"{r['batches']:.2f}",
                f"{r['expected_yield']:.3f}", f"{r['actual_yield']:.3f}", f"{r['wastage']:.3f}",
                f"{r['yield_variance_percent']:.2f}" if r['yield_variance_percent'] is not None else '',
                'Yes' if r['yield_flagged'] else 'No',
                f"{run.material_cost_paise / 100:.2f}", f"{run.total_cost_paise / 100:.2f}",
                f"{run.cost_per_packet_paise / 100:.2f}",
            ])
        return response

    if export_format == 'excel':
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Production Report"
        headers = ['Run #', 'Date', 'Product', 'Batches', 'Expected Yield', 'Actual Yield',
                   'Wastage', 'Yield Variance %', 'Yield Flagged',
                   'Material Cost (Rs)', 'Total Cost (Rs)', 'Cost/Packet (Rs)']
        data_rows = []
        for r in rows:
            run = r['run']
            data_rows.append([
                f"#{run.pk}", r['run_date'].isoformat(), r['product'], float(r['batches']),
                float(r['expected_yield']), float(r['actual_yield']), float(r['wastage']),
                float(r['yield_variance_percent']) if r['yield_variance_percent'] is not None else '',
                'Yes' if r['yield_flagged'] else 'No',
                float(Decimal(run.material_cost_paise) / Decimal(100)),
                float(Decimal(run.total_cost_paise) / Decimal(100)),
                float(Decimal(run.cost_per_packet_paise) / Decimal(100)),
            ])
        _style_excel_sheet(ws, "Production Report", headers, data_rows)
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        response = HttpResponse(output.getvalue(),
                                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="production_report_{datetime.date.today()}.xlsx"'
        return response

    return render(request, 'reports/production_report.html', {
        'rows': rows, 'from_date': from_str, 'to_date': to_str,
    })


# ─────────────────────────────────────────────────────────────────────────────
# 9. Collections & Outstanding
# ─────────────────────────────────────────────────────────────────────────────

@role_required('accountant', 'admin', 'owner')
def collections_outstanding(request):
    from_date, to_date, from_str, to_str = _get_date_filters(request)
    today = datetime.date.today()

    payments_qs = Payment.objects.select_related('customer', 'invoice').order_by('-date', '-id')
    if from_date:
        payments_qs = payments_qs.filter(date__gte=from_date)
    if to_date:
        payments_qs = payments_qs.filter(date__lte=to_date)
    payments = list(payments_qs)
    total_collected_paise = sum(p.amount_paise for p in payments)

    customers = Customer.objects.filter(invoices__isnull=False).distinct().order_by('name')
    customer_rows = []
    grand_total_outstanding = grand_b0_30 = grand_b31_60 = grand_b61_90 = grand_b90_plus = 0

    for customer in customers:
        ageing = calculate_customer_ageing(customer, today=today)
        total_outstanding = (ageing['bucket_0_30'] + ageing['bucket_31_60'] +
                             ageing['bucket_61_90'] + ageing['bucket_90_plus'])
        if total_outstanding <= 0:
            continue
        grand_total_outstanding += total_outstanding
        grand_b0_30 += ageing['bucket_0_30']
        grand_b31_60 += ageing['bucket_31_60']
        grand_b61_90 += ageing['bucket_61_90']
        grand_b90_plus += ageing['bucket_90_plus']
        customer_rows.append({
            'customer': customer,
            'total_outstanding': total_outstanding,
            'total_outstanding_formatted': format_inr(total_outstanding),
            'bucket_0_30': ageing['bucket_0_30'],
            'bucket_31_60': ageing['bucket_31_60'],
            'bucket_61_90': ageing['bucket_61_90'],
            'bucket_90_plus': ageing['bucket_90_plus'],
            'bucket_0_30_formatted': ageing['bucket_0_30_formatted'],
            'bucket_31_60_formatted': ageing['bucket_31_60_formatted'],
            'bucket_61_90_formatted': ageing['bucket_61_90_formatted'],
            'bucket_90_plus_formatted': ageing['bucket_90_plus_formatted'],
        })

    export_format = request.GET.get('export', '').lower()

    if export_format == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="collections_outstanding_{datetime.date.today()}.csv"'
        writer = csv.writer(response)
        writer.writerow(['Collections in Period'])
        writer.writerow(['Date', 'Customer', 'Invoice', 'Amount (Rs)', 'Mode', 'Reference'])
        for p in payments:
            writer.writerow([
                p.date.isoformat(), p.customer.name,
                p.invoice.number if p.invoice else '',
                f"{p.amount_paise / 100:.2f}", p.mode or '', p.reference or '',
            ])
        writer.writerow(['Total', '', '', f"{total_collected_paise / 100:.2f}", '', ''])
        writer.writerow([])
        writer.writerow(['Outstanding by Customer'])
        writer.writerow(['Customer', 'Total (Rs)', '0-30 Days', '31-60 Days', '61-90 Days', '90+ Days'])
        for r in customer_rows:
            writer.writerow([
                r['customer'].name,
                f"{r['total_outstanding'] / 100:.2f}",
                f"{r['bucket_0_30'] / 100:.2f}", f"{r['bucket_31_60'] / 100:.2f}",
                f"{r['bucket_61_90'] / 100:.2f}", f"{r['bucket_90_plus'] / 100:.2f}",
            ])
        writer.writerow(['Grand Total', f"{grand_total_outstanding / 100:.2f}",
                         f"{grand_b0_30 / 100:.2f}", f"{grand_b31_60 / 100:.2f}",
                         f"{grand_b61_90 / 100:.2f}", f"{grand_b90_plus / 100:.2f}"])
        return response

    if export_format == 'excel':
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "Collections"
        c_headers = ['Date', 'Customer', 'Invoice', 'Amount (Rs)', 'Mode', 'Reference']
        c_rows = [[p.date.isoformat(), p.customer.name, p.invoice.number if p.invoice else '',
                   float(Decimal(p.amount_paise) / Decimal(100)), p.mode or '', p.reference or '']
                  for p in payments]
        _style_excel_sheet(ws1, "Collections", c_headers, c_rows,
                           ['Total', '', '', float(Decimal(total_collected_paise) / Decimal(100)), '', ''])
        ws2 = wb.create_sheet("Outstanding")
        o_headers = ['Customer', 'Total Outstanding (Rs)', '0-30 Days', '31-60 Days', '61-90 Days', '90+ Days']
        o_rows = [[r['customer'].name,
                   float(Decimal(r['total_outstanding']) / Decimal(100)),
                   float(Decimal(r['bucket_0_30']) / Decimal(100)),
                   float(Decimal(r['bucket_31_60']) / Decimal(100)),
                   float(Decimal(r['bucket_61_90']) / Decimal(100)),
                   float(Decimal(r['bucket_90_plus']) / Decimal(100))] for r in customer_rows]
        _style_excel_sheet(ws2, "Outstanding by Customer", o_headers, o_rows,
                           ['Grand Total',
                            float(Decimal(grand_total_outstanding) / Decimal(100)),
                            float(Decimal(grand_b0_30) / Decimal(100)),
                            float(Decimal(grand_b31_60) / Decimal(100)),
                            float(Decimal(grand_b61_90) / Decimal(100)),
                            float(Decimal(grand_b90_plus) / Decimal(100))])
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        response = HttpResponse(output.getvalue(),
                                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="collections_outstanding_{datetime.date.today()}.xlsx"'
        return response

    return render(request, 'reports/collections_outstanding.html', {
        'payments': payments,
        'total_collected_paise': total_collected_paise,
        'total_collected_formatted': format_inr(total_collected_paise),
        'customer_rows': customer_rows,
        'grand_total_outstanding': grand_total_outstanding,
        'grand_total_outstanding_formatted': format_inr(grand_total_outstanding),
        'grand_b0_30': grand_b0_30, 'grand_b31_60': grand_b31_60,
        'grand_b61_90': grand_b61_90, 'grand_b90_plus': grand_b90_plus,
        'grand_b0_30_formatted': format_inr(grand_b0_30),
        'grand_b31_60_formatted': format_inr(grand_b31_60),
        'grand_b61_90_formatted': format_inr(grand_b61_90),
        'grand_b90_plus_formatted': format_inr(grand_b90_plus),
        'from_date': from_str, 'to_date': to_str,
    })


# ─────────────────────────────────────────────────────────────────────────────
# 10. Owner Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@role_required('owner', 'admin')
def owner_dashboard(request):
    today = datetime.date.today()

    from sales.models import Order
    todays_orders = Order.objects.filter(
        created_at__date=today
    ).select_related('customer', 'salesman').order_by('-created_at')
    todays_orders_count = todays_orders.count()

    finished_items = Item.objects.filter(item_type='finished', active=True).select_related('stock_unit')
    finished_stock = []
    for item in finished_items:
        balance = get_balance(item)
        low = item.reorder_level is not None and balance <= item.reorder_level
        finished_stock.append({'item': item, 'balance': balance, 'low_stock': low, 'unit': item.stock_unit.code})

    raw_items = Item.objects.filter(item_type__in=['raw', 'packaging'], active=True).select_related('stock_unit')
    raw_stock = []
    for item in raw_items:
        balance = get_balance(item)
        low = item.reorder_level is not None and balance <= item.reorder_level
        raw_stock.append({'item': item, 'balance': balance, 'low_stock': low, 'unit': item.stock_unit.code})

    from production.models import ProductionRun
    flagged_runs = ProductionRun.objects.filter(
        status='posted', yield_flagged=True
    ).select_related('bom__finished_item').order_by('-run_date', '-id')[:10]

    latest_costs = []
    seen_items = set()
    for run in ProductionRun.objects.filter(status='posted').select_related(
        'bom__finished_item'
    ).order_by('-run_date', '-id'):
        iid = run.bom.finished_item_id
        if iid not in seen_items:
            seen_items.add(iid)
            latest_costs.append({
                'product': run.bom.finished_item.name,
                'cost_per_packet_formatted': format_inr(run.cost_per_packet_paise),
                'selling_price_formatted': format_inr(run.selling_price_paise),
                'margin_formatted': format_inr(run.margin_paise),
                'margin_percent': run.margin_percent,
                'run_date': run.run_date,
            })

    all_customers = Customer.objects.filter(invoices__isnull=False).distinct()
    total_outstanding_paise = 0
    for customer in all_customers:
        ageing = calculate_customer_ageing(customer, today=today)
        total_outstanding_paise += (ageing['bucket_0_30'] + ageing['bucket_31_60'] +
                                    ageing['bucket_61_90'] + ageing['bucket_90_plus'])

    return render(request, 'reports/owner_dashboard.html', {
        'today': today,
        'todays_orders': todays_orders,
        'todays_orders_count': todays_orders_count,
        'finished_stock': finished_stock,
        'raw_stock': raw_stock,
        'flagged_runs': flagged_runs,
        'latest_costs': latest_costs,
        'total_outstanding_paise': total_outstanding_paise,
        'total_outstanding_formatted': format_inr(total_outstanding_paise),
    })

import csv
import datetime
from decimal import Decimal, ROUND_HALF_UP
import io

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from django.core.exceptions import PermissionDenied
from django.db.models import Sum, Count
from django.http import HttpResponse
from django.shortcuts import render

from core.decorators import role_required
from core.models import CompanySettings
from production.models import Purchase, PurchaseLine
from sales.models import Invoice, InvoiceLine
from sales.utils import format_inr


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

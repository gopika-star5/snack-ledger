from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.decorators import role_required
from core.ledger import InsufficientStock, receive_stock
from core.models import Item, Supplier, Unit, StockMovement
from core.units import convert
from sales.utils import format_inr

from .models import BOM, BOMLine, ProductionRun, Purchase, PurchaseLine, RunIssue, RunVarianceLine, RunOverhead
from .services import post_run


# ── Access control ────────────────────────────────────────────────────────────

def admin_or_production_required(view_func):
    @login_required
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if request.user.is_superuser or request.user.groups.filter(name__in=['admin', 'production']).exists():
            return view_func(request, *args, **kwargs)
        raise PermissionDenied("Access forbidden.")
    return _wrapped_view


# ── Forms ─────────────────────────────────────────────────────────────────────

class PurchaseHeaderForm(forms.Form):
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.all())
    date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))


class PurchaseLineForm(forms.Form):
    item = forms.ModelChoiceField(queryset=Item.objects.all(), required=False)
    qty = forms.DecimalField(max_digits=14, decimal_places=3, required=False)
    purchase_unit = forms.ModelChoiceField(queryset=Unit.objects.all(), required=False)
    rate = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        required=False,
        label="Rate (₹ per unit)",
        widget=forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'placeholder': '0.00'}),
    )

    def clean(self):
        cleaned_data = super().clean()
        rate = cleaned_data.get('rate')
        # Support fallback if rate_paise was passed directly in form data
        if rate is None:
            raw_rate_paise = self.data.get(self.add_prefix('rate_paise'))
            if raw_rate_paise is not None and str(raw_rate_paise).strip() != '':
                try:
                    rate_paise_val = Decimal(str(raw_rate_paise))
                    rate = (rate_paise_val / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                    cleaned_data['rate'] = rate
                except Exception:
                    pass
        if rate is not None:
            cleaned_data['rate_paise'] = int((Decimal(str(rate)) * Decimal('100')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        return cleaned_data

    def is_filled(self):
        """Return True if at least item and qty are provided."""
        return bool(self.cleaned_data.get('item') and self.cleaned_data.get('qty'))


PurchaseLineFormSet = forms.formset_factory(PurchaseLineForm, extra=5, max_num=10)


# ── Views ─────────────────────────────────────────────────────────────────────

@role_required('production')
def production_home(request):
    """Production supervisor role home page."""
    return render(request, 'production/production_home.html')


@admin_or_production_required
def purchase_list(request):
    purchases = Purchase.objects.select_related('supplier', 'created_by').order_by('-date', '-id')
    return render(request, 'production/purchase_list.html', {'purchases': purchases})


@admin_or_production_required
def purchase_create(request):
    if request.method == 'POST':
        header_form = PurchaseHeaderForm(request.POST)
        formset = PurchaseLineFormSet(request.POST)

        if header_form.is_valid() and formset.is_valid():
            # Collect valid lines first (validate before saving anything)
            valid_lines = []
            errors = []
            for i, line_form in enumerate(formset):
                if not line_form.cleaned_data:
                    continue
                if not line_form.is_filled():
                    continue
                item = line_form.cleaned_data['item']
                qty = line_form.cleaned_data['qty']
                purchase_unit = line_form.cleaned_data.get('purchase_unit')
                rate = line_form.cleaned_data.get('rate')

                if not purchase_unit:
                    errors.append(f"Line {i + 1}: purchase unit is required.")
                    continue
                if rate is None:
                    errors.append(f"Line {i + 1}: rate is required.")
                    continue

                # Convert rate in rupees to integer paise using Decimal with round half up
                rate_paise = int((Decimal(str(rate)) * Decimal('100')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

                # Convert qty to stock unit
                try:
                    qty_stock = convert(qty, purchase_unit, item.stock_unit)
                except ValueError as e:
                    errors.append(f"Line {i + 1}: {e}")
                    continue

                # Convert rate from per-purchase-unit to per-stock-unit
                factor_from = Decimal(str(purchase_unit.factor_to_base))
                factor_to = Decimal(str(item.stock_unit.factor_to_base))
                unit_cost_stock = int((Decimal(rate_paise) * factor_from / factor_to).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
                line_total = int((qty_stock * Decimal(unit_cost_stock)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
                valid_lines.append({
                    'item': item,
                    'qty_in_purchase_unit': qty,
                    'purchase_unit': purchase_unit,
                    'qty_in_stock_unit': qty_stock,
                    'unit_cost_paise': unit_cost_stock,
                    'line_total_paise': line_total,
                })

            if errors:
                return render(request, 'production/purchase_form.html', {
                    'header_form': header_form,
                    'formset': formset,
                    'errors': errors,
                })

            if not valid_lines:
                return render(request, 'production/purchase_form.html', {
                    'header_form': header_form,
                    'formset': formset,
                    'errors': ['Please add at least one line item.'],
                })

            # Save purchase
            purchase = Purchase.objects.create(
                supplier=header_form.cleaned_data['supplier'],
                date=header_form.cleaned_data['date'],
                note=header_form.cleaned_data.get('note', ''),
                created_by=request.user,
            )

            total_paise = 0
            for ld in valid_lines:
                PurchaseLine.objects.create(
                    purchase=purchase,
                    item=ld['item'],
                    qty_in_purchase_unit=ld['qty_in_purchase_unit'],
                    purchase_unit=ld['purchase_unit'],
                    qty_in_stock_unit=ld['qty_in_stock_unit'],
                    unit_cost_paise=ld['unit_cost_paise'],
                    line_total_paise=ld['line_total_paise'],
                )
                receive_stock(
                    item=ld['item'],
                    qty=ld['qty_in_stock_unit'],
                    unit_cost_paise=ld['unit_cost_paise'],
                    source_type='purchase',
                    source_id=str(purchase.pk),
                    user=request.user,
                )
                total_paise += ld['line_total_paise']

            purchase.total_paise = total_paise
            purchase.save()

            return redirect('purchase_list')
    else:
        header_form = PurchaseHeaderForm(initial={'date': timezone.localdate()})
        formset = PurchaseLineFormSet()

    return render(request, 'production/purchase_form.html', {
        'header_form': header_form,
        'formset': formset,
        'errors': [],
    })


# ── Production Run Views ──────────────────────────────────────────────────────

@admin_or_production_required
def run_list(request):
    """Run list page showing past production runs with status."""
    runs = ProductionRun.objects.select_related('bom__finished_item', 'created_by').order_by('-run_date', '-id')
    return render(request, 'production/run_list.html', {'runs': runs})


@admin_or_production_required
def run_create(request):
    """
    New Run page: shows the Murukku BOM, pre-fills issued quantities from the recipe,
    and has fields for batches, actual good output, damaged pouches, cartons used and run date.
    A Post button calls post_run and redirects to the result page.
    """
    murukku_bom = (
        BOM.objects.filter(finished_item__name__icontains='Murukku')
        .prefetch_related('lines__item__stock_unit', 'lines__unit')
        .first()
    )
    if not murukku_bom:
        murukku_bom = BOM.objects.prefetch_related('lines__item__stock_unit', 'lines__unit').first()

    pouch_line = None
    carton_line = None
    if murukku_bom:
        for line in murukku_bom.lines.select_related('item').all():
            name_lower = line.item.name.lower()
            if 'pouch' in name_lower:
                pouch_line = line
            elif 'carton' in name_lower:
                carton_line = line

        if not pouch_line:
            for line in murukku_bom.lines.select_related('item').all():
                if line.item.item_type == 'packaging' and line != carton_line:
                    pouch_line = line
                    break

        raw_lines = [
            line for line in murukku_bom.lines.select_related('item', 'unit').all()
            if line != pouch_line and line != carton_line
        ]
    else:
        raw_lines = []

    errors = []
    if request.method == 'POST':
        if not murukku_bom:
            errors.append("No Murukku BOM found in the system.")
        else:
            try:
                batches_val = request.POST.get('batches', '1').strip()
                batches = Decimal(batches_val) if batches_val else Decimal('1')
                if batches <= Decimal('0'):
                    raise ValueError("Batches must be greater than zero.")

                run_date_val = request.POST.get('run_date', '').strip()
                if run_date_val:
                    try:
                        run_date = datetime.strptime(run_date_val, '%Y-%m-%d').date()
                    except ValueError:
                        run_date = timezone.localdate()
                else:
                    run_date = timezone.localdate()

                actual_good_output_val = request.POST.get('actual_good_output', '0').strip()
                actual_good_output = Decimal(actual_good_output_val) if actual_good_output_val else Decimal('0')

                damaged_pouches_val = request.POST.get('damaged_pouches', '0').strip()
                damaged_pouches = Decimal(damaged_pouches_val) if damaged_pouches_val else Decimal('0')

                cartons_used_val = request.POST.get('cartons_used', '0').strip()
                cartons_used = Decimal(cartons_used_val) if cartons_used_val else Decimal('0')

                # Create production run and raw material issues in an atomic transaction
                with transaction.atomic():
                    run = ProductionRun.objects.create(
                        bom=murukku_bom,
                        batches=batches,
                        run_date=run_date,
                        actual_good_output=actual_good_output,
                        damaged_pouches=damaged_pouches,
                        cartons_used=cartons_used,
                        created_by=request.user,
                        status='draft',
                    )

                    for line in raw_lines:
                        # Find issued quantity in post data
                        key_id = f'issue_{line.item.id}'
                        key_name = f'issue_{line.item.name.lower().replace(" ", "_")}'
                        val = request.POST.get(key_id)
                        if val is None:
                            val = request.POST.get(key_name)
                        if val is None:
                            val = request.POST.get(str(line.item.id))
                        if val is not None and str(val).strip() != '':
                            issued_qty = Decimal(str(val).strip())
                        else:
                            issued_qty = line.qty_per_batch * batches

                        RunIssue.objects.create(
                            run=run,
                            item=line.item,
                            issued_qty=issued_qty,
                        )

                    # Post run
                    post_run(run, request.user)

                return redirect('run_result', pk=run.pk)
            except (ValidationError, InsufficientStock, ValueError) as e:
                errors.append(str(e))
            except Exception as e:
                errors.append(f"An unexpected error occurred: {e}")

    context = {
        'bom': murukku_bom,
        'raw_lines': raw_lines,
        'pouch_line': pouch_line,
        'carton_line': carton_line,
        'default_date': timezone.localdate().isoformat(),
        'errors': errors,
    }
    return render(request, 'production/run_form.html', context)


@admin_or_production_required
def run_result(request, pk):
    """
    Run result page: summary and a variance table (item, planned, actual, variance %, status badge).
    Flagged rows are red; favourable variances are green. Show the yield variance row.
    Cost card with material cost (FIFO), overheads, total cost, good packets, cost per packet,
    selling price, margin in rupees and percent, and weighted-average material cost.
    Overhead entry: add RunOverhead rows (description, amount in rupees saved as paise).
    """
    run = get_object_or_404(
        ProductionRun.objects.select_related('bom__finished_item__stock_unit', 'created_by')
        .prefetch_related('variance_lines__item__stock_unit', 'overheads'),
        pk=pk,
    )

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'delete_overhead':
            overhead_id = request.POST.get('overhead_id')
            if overhead_id:
                RunOverhead.objects.filter(pk=overhead_id, run=run).delete()
            return redirect('run_result', pk=run.pk)

        # Handle adding overheads
        # Support single or multiple overhead rows
        descriptions = request.POST.getlist('description') or request.POST.getlist('descriptions')
        amounts = request.POST.getlist('amount') or request.POST.getlist('amount_rupees') or request.POST.getlist('amounts')

        if not descriptions and 'description' in request.POST:
            descriptions = [request.POST['description']]
            amounts = [
                request.POST.get('amount_rupees')
                or request.POST.get('amount')
                or request.POST.get('amount_paise')
            ]

        if descriptions:
            for idx, desc in enumerate(descriptions):
                desc_str = (desc or '').strip()
                if not desc_str:
                    continue
                amt_val = amounts[idx] if idx < len(amounts) else None
                if amt_val is None or str(amt_val).strip() == '':
                    continue

                raw_amt = str(amt_val).strip().replace('₹', '').replace(',', '')
                try:
                    if 'amount_paise' in request.POST and not request.POST.get('amount_rupees') and not request.POST.get('amount'):
                        amt_paise = int(raw_amt)
                    elif '.' in raw_amt:
                        amt_paise = int((Decimal(raw_amt) * Decimal('100')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
                    else:
                        val_int = int(raw_amt)
                        # Amount in rupees saved as paise: e.g. 1200 -> 120000
                        amt_paise = val_int * 100

                    if amt_paise > 0:
                        RunOverhead.objects.create(
                            run=run,
                            description=desc_str,
                            amount_paise=amt_paise,
                        )
                except Exception:
                    pass

        return redirect('run_result', pk=run.pk)

    expected_yield = run.bom.batch_yield * run.batches

    # 1. Material cost (sum of FIFO costs from the run's consumption movements)
    consumption_movements = list(
        StockMovement.objects.filter(
            source_type="production",
            source_id=str(run.pk),
            qty__lt=0,
        )
    )
    if consumption_movements:
        material_cost_paise = sum(
            int((abs(m.qty) * Decimal(m.unit_cost_paise)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            for m in consumption_movements
        )
    else:
        material_cost_paise = run.material_cost_paise or 0

    material_cost_rupees = f"{material_cost_paise / 100:.2f}"
    material_cost_formatted = format_inr(material_cost_paise)
    unit_cost_rupees = f"{(run.unit_cost_paise or 0) / 100:.2f}"

    # 2. Overheads
    overheads_list = list(run.overheads.all())
    overhead_cost_paise = sum(o.amount_paise for o in overheads_list)
    overhead_cost_rupees = f"{overhead_cost_paise / 100:.2f}"
    overhead_cost_formatted = format_inr(overhead_cost_paise)

    # 3. Total cost
    total_cost_paise = material_cost_paise + overhead_cost_paise
    total_cost_rupees = f"{total_cost_paise / 100:.2f}"
    total_cost_formatted = format_inr(total_cost_paise)

    # 4. Good packets
    good_packets = Decimal(str(run.actual_good_output or 0))

    # Rule: Overhead is allocated equally per good packet.
    if good_packets > Decimal('0'):
        cost_per_packet_paise = int(
            (Decimal(total_cost_paise) / good_packets).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        )
        fifo_material_per_pkt_paise = int(
            (Decimal(material_cost_paise) / good_packets).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        )
    else:
        cost_per_packet_paise = 0
        fifo_material_per_pkt_paise = 0

    cost_per_packet_rupees = f"{cost_per_packet_paise / 100:.2f}"
    cost_per_packet_formatted = format_inr(cost_per_packet_paise)

    # 5. Selling price
    selling_price_paise = int(run.bom.finished_item.selling_price_paise or 0)
    selling_price_rupees = f"{selling_price_paise / 100:.2f}"
    selling_price_formatted = format_inr(selling_price_paise)

    # 6. Margin in rupees and percent
    margin_paise = selling_price_paise - cost_per_packet_paise
    margin_rupees = f"{margin_paise / 100:.2f}"
    margin_formatted = format_inr(margin_paise)
    if selling_price_paise > 0:
        margin_percent = ((Decimal(margin_paise) / Decimal(selling_price_paise)) * Decimal('100')).quantize(
            Decimal('0.1'), rounding=ROUND_HALF_UP
        )
    else:
        margin_percent = Decimal('0.0')

    # 7. Weighted-average material cost per packet for comparison
    item_consumed_qtys = {}
    for m in consumption_movements:
        item_consumed_qtys[m.item_id] = item_consumed_qtys.get(m.item_id, Decimal('0')) + abs(m.qty)

    wa_material_cost_paise = 0
    for item_id, consumed_qty in item_consumed_qtys.items():
        receipts = StockMovement.objects.filter(item_id=item_id, qty__gt=0)
        tot_qty = sum(r.qty for r in receipts)
        tot_val = sum(r.qty * r.unit_cost_paise for r in receipts)
        if tot_qty > Decimal('0'):
            wa_rate = tot_val / tot_qty
        else:
            try:
                item_obj = Item.objects.get(pk=item_id)
                wa_rate = Decimal(item_obj.cost_price_paise or 0)
            except Item.DoesNotExist:
                wa_rate = Decimal('0')
        wa_material_cost_paise += int((consumed_qty * wa_rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

    if not consumption_movements and run.material_cost_paise:
        wa_material_cost_paise = run.material_cost_paise

    if good_packets > Decimal('0'):
        wa_material_cost_per_pkt_paise = int(
            (Decimal(wa_material_cost_paise) / good_packets).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        )
        wa_total_cost_per_pkt_paise = int(
            ((Decimal(wa_material_cost_paise) + Decimal(overhead_cost_paise)) / good_packets).quantize(
                Decimal('1'), rounding=ROUND_HALF_UP
            )
        )
    else:
        wa_material_cost_per_pkt_paise = 0
        wa_total_cost_per_pkt_paise = 0

    wa_material_cost_per_pkt_rupees = f"{wa_material_cost_per_pkt_paise / 100:.2f}"
    wa_material_cost_per_pkt_formatted = format_inr(wa_material_cost_per_pkt_paise)
    wa_total_cost_per_pkt_rupees = f"{wa_total_cost_per_pkt_paise / 100:.2f}"
    wa_total_cost_per_pkt_formatted = format_inr(wa_total_cost_per_pkt_paise)

    variance_lines = []
    for vl in run.variance_lines.all():
        if vl.flagged and vl.direction == 'adverse':
            row_type = 'flagged'
            badge_text = 'Flagged'
            badge_class = 'bg-rose-100 text-rose-800 border-rose-300'
            row_class = 'bg-rose-50 text-rose-900 font-medium'
        elif vl.direction == 'favourable' and vl.variance_qty < 0:
            row_type = 'favourable'
            badge_text = 'Favourable'
            badge_class = 'bg-emerald-100 text-emerald-800 border-emerald-300'
            row_class = 'bg-emerald-50 text-emerald-900 font-medium'
        elif vl.flagged:
            row_type = 'flagged'
            badge_text = 'Flagged'
            badge_class = 'bg-rose-100 text-rose-800 border-rose-300'
            row_class = 'bg-rose-50 text-rose-900 font-medium'
        else:
            row_type = 'normal'
            badge_text = 'OK'
            badge_class = 'bg-slate-100 text-slate-700 border-slate-300'
            row_class = 'bg-white text-slate-700'

        variance_lines.append({
            'item': vl.item,
            'planned_qty': vl.planned_qty,
            'actual_qty': vl.actual_qty,
            'variance_qty': vl.variance_qty,
            'variance_percent': vl.variance_percent,
            'flagged': vl.flagged,
            'direction': vl.direction,
            'row_class': row_class,
            'badge_class': badge_class,
            'badge_text': badge_text,
            'row_type': row_type,
        })

    # Yield variance row details
    yield_row_class = (
        'bg-rose-50 text-rose-900 font-bold'
        if run.yield_flagged
        else ('bg-emerald-50 text-emerald-900 font-bold' if (run.yield_variance_percent or 0) >= 0 else 'bg-white text-slate-700 font-bold')
    )
    yield_badge_class = (
        'bg-rose-100 text-rose-800 border-rose-300'
        if run.yield_flagged
        else ('bg-emerald-100 text-emerald-800 border-emerald-300' if (run.yield_variance_percent or 0) >= 0 else 'bg-slate-100 text-slate-700 border-slate-300')
    )
    yield_badge_text = (
        'Flagged'
        if run.yield_flagged
        else ('Favourable' if (run.yield_variance_percent or 0) >= 0 else 'OK')
    )

    context = {
        'run': run,
        'expected_yield': expected_yield,
        'material_cost_rupees': material_cost_rupees,
        'material_cost_paise': material_cost_paise,
        'material_cost_formatted': material_cost_formatted,
        'unit_cost_rupees': unit_cost_rupees,
        'overheads': overheads_list,
        'overhead_cost_paise': overhead_cost_paise,
        'overhead_cost_rupees': overhead_cost_rupees,
        'overhead_cost_formatted': overhead_cost_formatted,
        'total_cost_paise': total_cost_paise,
        'total_cost_rupees': total_cost_rupees,
        'total_cost_formatted': total_cost_formatted,
        'good_packets': good_packets,
        'cost_per_packet_paise': cost_per_packet_paise,
        'cost_per_packet_rupees': cost_per_packet_rupees,
        'cost_per_packet_formatted': cost_per_packet_formatted,
        'fifo_material_per_pkt_paise': fifo_material_per_pkt_paise,
        'selling_price_paise': selling_price_paise,
        'selling_price_rupees': selling_price_rupees,
        'selling_price_formatted': selling_price_formatted,
        'margin_paise': margin_paise,
        'margin_rupees': margin_rupees,
        'margin_formatted': margin_formatted,
        'margin_percent': margin_percent,
        'wa_material_cost_paise': wa_material_cost_paise,
        'wa_material_cost_per_pkt_paise': wa_material_cost_per_pkt_paise,
        'wa_material_cost_per_pkt_rupees': wa_material_cost_per_pkt_rupees,
        'wa_material_cost_per_pkt_formatted': wa_material_cost_per_pkt_formatted,
        'wa_total_cost_per_pkt_paise': wa_total_cost_per_pkt_paise,
        'wa_total_cost_per_pkt_rupees': wa_total_cost_per_pkt_rupees,
        'wa_total_cost_per_pkt_formatted': wa_total_cost_per_pkt_formatted,
        'variance_lines': variance_lines,
        'yield_row_class': yield_row_class,
        'yield_badge_class': yield_badge_class,
        'yield_badge_text': yield_badge_text,
    }
    return render(request, 'production/run_result.html', context)


@role_required('admin', 'owner', 'accountant')
def product_costing_list(request):
    """
    Product Costing page: listing every posted run with cost per packet and margin.
    Add it as a card on the admin and owner home pages.
    Only admin, owner and accountant can open it; others get 403.
    """
    runs = (
        ProductionRun.objects.filter(status='posted')
        .select_related('bom__finished_item__stock_unit', 'created_by')
        .prefetch_related('overheads')
        .order_by('-run_date', '-id')
    )

    costing_rows = []
    for run in runs:
        # Rule: Overhead is allocated equally per good packet.
        good = Decimal(str(run.actual_good_output or 0))
        mat_cost = int(run.material_cost_paise or 0)
        overheads = sum(o.amount_paise for o in run.overheads.all())
        total_cost = mat_cost + overheads

        if good > Decimal('0'):
            cost_per_pkt = int((Decimal(total_cost) / good).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        else:
            cost_per_pkt = 0

        sp = int(run.bom.finished_item.selling_price_paise or 0)
        margin = sp - cost_per_pkt
        if sp > 0:
            margin_pct = ((Decimal(margin) / Decimal(sp)) * Decimal('100')).quantize(
                Decimal('0.1'), rounding=ROUND_HALF_UP
            )
        else:
            margin_pct = Decimal('0.0')

        costing_rows.append({
            'run': run,
            'finished_item': run.bom.finished_item,
            'good_output': good,
            'material_cost_paise': mat_cost,
            'material_cost_rupees': f"{mat_cost / 100:.2f}",
            'material_cost_formatted': format_inr(mat_cost),
            'overhead_paise': overheads,
            'overhead_rupees': f"{overheads / 100:.2f}",
            'overhead_formatted': format_inr(overheads),
            'total_cost_paise': total_cost,
            'total_cost_rupees': f"{total_cost / 100:.2f}",
            'total_cost_formatted': format_inr(total_cost),
            'cost_per_packet_paise': cost_per_pkt,
            'cost_per_packet_rupees': f"{cost_per_pkt / 100:.2f}",
            'cost_per_packet_formatted': format_inr(cost_per_pkt),
            'selling_price_paise': sp,
            'selling_price_rupees': f"{sp / 100:.2f}",
            'selling_price_formatted': format_inr(sp),
            'margin_paise': margin,
            'margin_rupees': f"{margin / 100:.2f}",
            'margin_formatted': format_inr(margin),
            'margin_percent': margin_pct,
        })

    context = {
        'costing_rows': costing_rows,
    }
    return render(request, 'production/product_costing.html', context)

from datetime import datetime
from decimal import Decimal
from functools import wraps

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.decorators import role_required
from core.ledger import InsufficientStock, receive_stock
from core.models import Item, Supplier, Unit
from core.units import convert

from .models import BOM, BOMLine, ProductionRun, Purchase, PurchaseLine, RunIssue, RunVarianceLine
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
    rate_paise = forms.IntegerField(
        required=False,
        help_text="Rate per purchase unit in paise",
    )

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
                rate_paise = line_form.cleaned_data.get('rate_paise')

                if not purchase_unit:
                    errors.append(f"Line {i + 1}: purchase unit is required.")
                    continue
                if not rate_paise:
                    errors.append(f"Line {i + 1}: rate is required.")
                    continue

                # Convert qty to stock unit
                try:
                    qty_stock = convert(qty, purchase_unit, item.stock_unit)
                except ValueError as e:
                    errors.append(f"Line {i + 1}: {e}")
                    continue

                # Convert rate from per-purchase-unit to per-stock-unit
                factor_from = Decimal(str(purchase_unit.factor_to_base))
                factor_to = Decimal(str(item.stock_unit.factor_to_base))
                unit_cost_stock = int(Decimal(rate_paise) * factor_from / factor_to)
                line_total = int(qty_stock * unit_cost_stock)
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
    """
    run = get_object_or_404(
        ProductionRun.objects.select_related('bom__finished_item__stock_unit', 'created_by')
        .prefetch_related('variance_lines__item__stock_unit'),
        pk=pk,
    )

    expected_yield = run.bom.batch_yield * run.batches
    material_cost_rupees = f"{(run.material_cost_paise or 0) / 100:.2f}"
    unit_cost_rupees = f"{(run.unit_cost_paise or 0) / 100:.2f}"

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
        'unit_cost_rupees': unit_cost_rupees,
        'variance_lines': variance_lines,
        'yield_row_class': yield_row_class,
        'yield_badge_class': yield_badge_class,
        'yield_badge_text': yield_badge_text,
    }
    return render(request, 'production/run_result.html', context)

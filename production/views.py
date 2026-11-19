from decimal import Decimal
from functools import wraps

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.utils import timezone

from core.decorators import role_required
from core.ledger import receive_stock
from core.models import Item, Supplier, Unit
from core.units import convert

from .models import Purchase, PurchaseLine


# ── Access control ────────────────────────────────────────────────────────────

def admin_or_production_required(view_func):
    @login_required
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if request.user.groups.filter(name__in=['admin', 'production']).exists():
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

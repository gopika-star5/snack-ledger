from django.contrib import admin
from .models import Purchase, PurchaseLine


class PurchaseLineInline(admin.TabularInline):
    model = PurchaseLine
    extra = 1
    readonly_fields = ('qty_in_stock_unit', 'line_total_paise')


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ('id', 'supplier', 'date', 'total_paise', 'created_by', 'created_at')
    inlines = [PurchaseLineInline]
    readonly_fields = ('total_paise', 'created_by', 'created_at')

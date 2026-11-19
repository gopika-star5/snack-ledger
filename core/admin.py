from django.contrib import admin
from .models import Unit, Item, Customer, Supplier, StockLot, StockMovement, AuditLog


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ('code', 'kind', 'factor_to_base')
    list_filter = ('kind',)
    search_fields = ('code',)


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'item_type', 'stock_unit', 'hsn_code', 'gst_rate_percent', 'selling_price_paise', 'active')
    list_filter = ('item_type', 'active')
    search_fields = ('name', 'hsn_code')


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('name', 'customer_type', 'gstin', 'state', 'phone')
    list_filter = ('customer_type', 'state')
    search_fields = ('name', 'gstin', 'phone')


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'gstin', 'state')
    list_filter = ('state',)
    search_fields = ('name', 'gstin')


@admin.register(StockLot)
class StockLotAdmin(admin.ModelAdmin):
    list_display = ('batch_no', 'item', 'received_date', 'expiry_date', 'unit_cost_paise', 'qty_received', 'source_type')
    list_filter = ('source_type', 'item')
    search_fields = ('batch_no', 'item__name', 'source_type', 'source_id')


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ('id', 'item', 'lot', 'qty', 'unit_cost_paise', 'source_type', 'source_id', 'created_by', 'created_at')
    list_filter = ('source_type', 'item')
    search_fields = ('item__name', 'source_type', 'source_id', 'note')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'action', 'model_name', 'object_id', 'created_at')
    list_filter = ('action', 'model_name')
    search_fields = ('model_name', 'object_id', 'action')
    readonly_fields = ('user', 'action', 'model_name', 'object_id', 'details', 'created_at')

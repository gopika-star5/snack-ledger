from django.contrib import admin
from .models import BOM, BOMLine, ProductionRun, Purchase, PurchaseLine, RunIssue, RunOverhead, RunVarianceLine


class PurchaseLineInline(admin.TabularInline):
    model = PurchaseLine
    extra = 1
    readonly_fields = ('qty_in_stock_unit', 'line_total_paise')


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ('id', 'supplier', 'date', 'total_paise', 'created_by', 'created_at')
    inlines = [PurchaseLineInline]
    readonly_fields = ('total_paise', 'created_by', 'created_at')


class BOMLineInline(admin.TabularInline):
    model = BOMLine
    extra = 1


@admin.register(BOM)
class BOMAdmin(admin.ModelAdmin):
    list_display = ('id', 'finished_item', 'batch_yield', 'tolerance_percent')
    inlines = [BOMLineInline]


class RunIssueInline(admin.TabularInline):
    model = RunIssue
    extra = 1


class RunVarianceLineInline(admin.TabularInline):
    model = RunVarianceLine
    extra = 0
    readonly_fields = ('item', 'planned_qty', 'actual_qty', 'variance_qty', 'variance_percent', 'flagged', 'direction')


class RunOverheadInline(admin.TabularInline):
    model = RunOverhead
    extra = 1


@admin.register(ProductionRun)
class ProductionRunAdmin(admin.ModelAdmin):
    list_display = ('id', 'bom', 'batches', 'run_date', 'status', 'actual_good_output', 'yield_variance_percent', 'yield_flagged')
    inlines = [RunIssueInline, RunVarianceLineInline, RunOverheadInline]

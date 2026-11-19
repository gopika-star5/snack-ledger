from django.contrib import admin
from .models import Order, OrderLine


class OrderLineInline(admin.TabularInline):
    model = OrderLine
    extra = 1


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'client_uuid', 'customer', 'salesman', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('client_uuid', 'customer__name', 'salesman__username')
    inlines = [OrderLineInline]

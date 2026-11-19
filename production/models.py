from decimal import Decimal
from django.conf import settings
from django.db import models
from core.models import Item, Supplier, Unit


class Purchase(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='purchases')
    date = models.DateField()
    note = models.TextField(blank=True, default='')
    total_paise = models.BigIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='purchases',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Purchase #{self.pk} from {self.supplier} on {self.date}"


class PurchaseLine(models.Model):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name='lines')
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name='purchase_lines')
    qty_in_purchase_unit = models.DecimalField(max_digits=14, decimal_places=3)
    purchase_unit = models.ForeignKey(Unit, on_delete=models.PROTECT)
    qty_in_stock_unit = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost_paise = models.IntegerField(help_text="Rate per stock unit in paise")
    line_total_paise = models.BigIntegerField(default=0)

    def __str__(self):
        return f"Line {self.pk}: {self.item} {self.qty_in_purchase_unit} -> {self.qty_in_stock_unit}"

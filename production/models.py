from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
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


class BOM(models.Model):
    finished_item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name='boms')
    batch_yield = models.DecimalField(max_digits=14, decimal_places=3, help_text="Packets per batch")
    tolerance_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('3.00'))

    def __str__(self):
        return f"BOM for {self.finished_item.name}"


class BOMLine(models.Model):
    bom = models.ForeignKey(BOM, on_delete=models.CASCADE, related_name='lines')
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name='bom_lines')
    qty_per_batch = models.DecimalField(max_digits=14, decimal_places=3)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT)

    def __str__(self):
        return f"{self.item.name} ({self.qty_per_batch} {self.unit.code}) for {self.bom}"


class ProductionRun(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('posted', 'Posted'),
    ]

    bom = models.ForeignKey(BOM, on_delete=models.PROTECT, related_name='runs')
    batches = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('1'))
    run_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    actual_good_output = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal('0'))
    damaged_pouches = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal('0'))
    cartons_used = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal('0'))
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='production_runs',
    )
    yield_variance_percent = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    yield_flagged = models.BooleanField(default=False)
    material_cost_paise = models.BigIntegerField(default=0)
    unit_cost_paise = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        allow_post = kwargs.pop('_allow_post', False)
        if self.pk:
            orig = ProductionRun.objects.filter(pk=self.pk).values('status').first()
            if orig and orig['status'] == 'posted' and not allow_post:
                raise ValidationError("A posted production run cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status == 'posted':
            raise ValidationError("A posted production run cannot be deleted.")
        super().delete(*args, **kwargs)

    def __str__(self):
        return f"Production Run #{self.pk} - {self.bom.finished_item.name} ({self.status})"


class RunIssue(models.Model):
    run = models.ForeignKey(ProductionRun, on_delete=models.CASCADE, related_name='issues')
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name='run_issues')
    issued_qty = models.DecimalField(max_digits=14, decimal_places=3)

    def __str__(self):
        return f"Issue for Run #{self.run_id}: {self.item.name} {self.issued_qty}"


class RunVarianceLine(models.Model):
    DIRECTION_CHOICES = [
        ('adverse', 'Adverse'),
        ('favourable', 'Favourable'),
    ]

    run = models.ForeignKey(ProductionRun, on_delete=models.CASCADE, related_name='variance_lines')
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name='run_variance_lines')
    planned_qty = models.DecimalField(max_digits=14, decimal_places=3)
    actual_qty = models.DecimalField(max_digits=14, decimal_places=3)
    variance_qty = models.DecimalField(max_digits=14, decimal_places=3)
    variance_percent = models.DecimalField(max_digits=7, decimal_places=2)
    flagged = models.BooleanField(default=False)
    direction = models.CharField(max_length=20, choices=DIRECTION_CHOICES)

    def __str__(self):
        return f"Variance for Run #{self.run_id} - {self.item.name}: {self.variance_percent}% ({self.direction})"


class RunOverhead(models.Model):
    run = models.ForeignKey(ProductionRun, on_delete=models.CASCADE, related_name='overheads')
    description = models.CharField(max_length=255)
    amount_paise = models.BigIntegerField()

    def __str__(self):
        return f"Overhead for Run #{self.run_id}: {self.description} ({self.amount_paise} paise)"

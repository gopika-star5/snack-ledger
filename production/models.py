from decimal import Decimal, ROUND_HALF_UP
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

    @property
    def overhead_cost_paise(self) -> int:
        from django.db.models import Sum
        return int(self.overheads.aggregate(total=Sum('amount_paise'))['total'] or 0)

    @property
    def overhead_cost_rupees(self) -> str:
        return f"{self.overhead_cost_paise / 100:.2f}"

    @property
    def total_cost_paise(self) -> int:
        return int(self.material_cost_paise or 0) + self.overhead_cost_paise

    @property
    def total_cost_rupees(self) -> str:
        return f"{self.total_cost_paise / 100:.2f}"

    @property
    def cost_per_packet_paise(self) -> int:
        # Rule: Overhead is allocated equally per good packet.
        good = Decimal(str(self.actual_good_output or 0))
        if good <= Decimal('0'):
            return 0
        total = Decimal(str(self.total_cost_paise))
        return int((total / good).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

    @property
    def cost_per_packet_rupees(self) -> str:
        return f"{self.cost_per_packet_paise / 100:.2f}"

    @property
    def cost_per_packet_formatted(self) -> str:
        from sales.utils import format_inr
        return format_inr(self.cost_per_packet_paise)

    @property
    def selling_price_paise(self) -> int:
        return int(self.bom.finished_item.selling_price_paise or 0)

    @property
    def selling_price_rupees(self) -> str:
        return f"{self.selling_price_paise / 100:.2f}"

    @property
    def margin_paise(self) -> int:
        return self.selling_price_paise - self.cost_per_packet_paise

    @property
    def margin_rupees(self) -> str:
        return f"{self.margin_paise / 100:.2f}"

    @property
    def margin_formatted(self) -> str:
        from sales.utils import format_inr
        return format_inr(self.margin_paise)

    @property
    def margin_percent(self) -> Decimal:
        sp = self.selling_price_paise
        if sp <= 0:
            return Decimal('0.0')
        return ((Decimal(self.margin_paise) / Decimal(sp)) * Decimal('100')).quantize(
            Decimal('0.1'), rounding=ROUND_HALF_UP
        )


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

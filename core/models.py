from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import models


class ImmutableStockMovementError(ValidationError, ValueError, PermissionDenied):
    """Raised when an attempt is made to edit or delete a StockMovement record."""
    pass


class Unit(models.Model):
    KIND_CHOICES = [
        ('mass', 'Mass'),
        ('volume', 'Volume'),
        ('count', 'Count'),
    ]

    code = models.CharField(max_length=20, unique=True)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    factor_to_base = models.DecimalField(max_digits=12, decimal_places=4)

    def __str__(self):
        return self.code


class Item(models.Model):
    ITEM_TYPE_CHOICES = [
        ('raw', 'Raw Material'),
        ('packaging', 'Packaging'),
        ('finished', 'Finished Goods'),
    ]

    name = models.CharField(max_length=255)
    item_type = models.CharField(max_length=20, choices=ITEM_TYPE_CHOICES)
    stock_unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name='items')
    hsn_code = models.CharField(max_length=20, blank=True, default='')
    gst_rate_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    selling_price_paise = models.IntegerField(null=True, blank=True)
    shelf_life_days = models.IntegerField(null=True, blank=True)
    reorder_level = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Customer(models.Model):
    CUSTOMER_TYPE_CHOICES = [
        ('retailer', 'Retailer'),
        ('distributor', 'Distributor'),
    ]

    name = models.CharField(max_length=255)
    customer_type = models.CharField(max_length=20, choices=CUSTOMER_TYPE_CHOICES)
    gstin = models.CharField(max_length=15, blank=True, default='')
    state = models.CharField(max_length=100)
    address = models.TextField(blank=True, default='')
    phone = models.CharField(max_length=20, blank=True, default='')
    credit_limit_paise = models.BigIntegerField(null=True, blank=True)

    def __str__(self):
        return self.name


class Supplier(models.Model):
    name = models.CharField(max_length=255)
    gstin = models.CharField(max_length=15, blank=True, default='')
    state = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class StockLot(models.Model):
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name='stock_lots')
    batch_no = models.CharField(max_length=100)
    received_date = models.DateField()
    expiry_date = models.DateField(null=True, blank=True)
    unit_cost_paise = models.IntegerField(help_text="Cost per stock unit in paise")
    qty_received = models.DecimalField(max_digits=14, decimal_places=3)
    source_type = models.CharField(max_length=50, blank=True, default='')
    source_id = models.CharField(max_length=100, blank=True, default='', null=True)

    def __str__(self):
        return f"{self.item.name} (Batch: {self.batch_no})"


class StockMovementQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ImmutableStockMovementError("StockMovement rows cannot be edited or updated once created.")

    def delete(self):
        raise ImmutableStockMovementError("StockMovement rows cannot be deleted.")


class StockMovement(models.Model):
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name='stock_movements')
    lot = models.ForeignKey(StockLot, on_delete=models.PROTECT, null=True, blank=True, related_name='stock_movements')
    qty = models.DecimalField(max_digits=14, decimal_places=3, help_text="Positive for in, negative for out")
    unit_cost_paise = models.IntegerField(default=0)
    source_type = models.CharField(max_length=50, blank=True, default='')
    source_id = models.CharField(max_length=100, blank=True, default='', null=True)
    note = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='stock_movements'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = StockMovementQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if self.pk and (not self._state.adding or StockMovement.objects.filter(pk=self.pk).exists()):
            raise ImmutableStockMovementError("StockMovement rows cannot be edited or updated once created.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableStockMovementError("StockMovement rows cannot be deleted.")

    def __str__(self):
        return f"{self.item.name}: {self.qty} ({self.source_type})"


class AuditLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs'
    )
    action = models.CharField(max_length=50)
    model_name = models.CharField(max_length=100, blank=True, default='')
    object_id = models.CharField(max_length=100, blank=True, default='')
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.action} on {self.model_name}:{self.object_id}"


class CompanySettings(models.Model):
    name = models.CharField(max_length=255, default='')
    gstin = models.CharField(max_length=15, default='')
    state = models.CharField(max_length=100, default='')

    class Meta:
        verbose_name = 'Company Settings'
        verbose_name_plural = 'Company Settings'

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


def log_action(user=None, action="", model_name="", object_id=None, details=None, instance=None):
    """
    Helper function to record an AuditLog entry.
    """
    if instance is not None:
        if not model_name:
            model_name = instance._meta.model_name
        if object_id is None:
            object_id = str(instance.pk)
    if details is None:
        details = {}
    if object_id is not None:
        object_id = str(object_id)
    else:
        object_id = ""
    actual_user = user
    if actual_user is not None and not getattr(actual_user, 'is_authenticated', True):
        actual_user = None

    return AuditLog.objects.create(
        user=actual_user,
        action=action,
        model_name=model_name,
        object_id=object_id,
        details=details
    )

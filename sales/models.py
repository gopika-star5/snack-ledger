import uuid
from decimal import Decimal
from django.conf import settings
from django.db import models


class Order(models.Model):
    STATUS_SUBMITTED = 'submitted'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_INVOICED = 'invoiced'
    STATUS_DISPATCHED = 'dispatched'

    STATUS_CHOICES = [
        (STATUS_SUBMITTED, 'Submitted'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
        (STATUS_INVOICED, 'Invoiced'),
        (STATUS_DISPATCHED, 'Dispatched'),
    ]

    client_uuid = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        db_index=True,
        help_text="Unique client-generated UUID to block duplicate submits",
    )
    customer = models.ForeignKey(
        'core.Customer',
        on_delete=models.PROTECT,
        related_name='orders',
    )
    salesman = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='orders',
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_SUBMITTED,
    )
    note = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Order #{self.pk or ''} - {self.customer.name} ({self.get_status_display()})"

    @property
    def total_paise(self) -> int:
        return sum(line.total_paise for line in self.lines.all())

    @property
    def total(self) -> int:
        return self.total_paise

    @property
    def total_rupees(self) -> str:
        return f"{self.total_paise / 100:.2f}"

    @property
    def total_items_qty(self) -> Decimal:
        return sum((line.qty for line in self.lines.all()), Decimal('0'))


class OrderLine(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name='lines',
    )
    item = models.ForeignKey(
        'core.Item',
        on_delete=models.PROTECT,
        related_name='order_lines',
    )
    qty = models.DecimalField(max_digits=14, decimal_places=3)
    price_paise = models.IntegerField(help_text="Price per item unit in paise")

    def __str__(self):
        return f"{self.item.name} x {self.qty} @ {self.price_paise} paise"

    @property
    def total_paise(self) -> int:
        return int(self.qty * self.price_paise)

    @property
    def total(self) -> int:
        return self.total_paise

    @property
    def total_rupees(self) -> str:
        return f"{self.total_paise / 100:.2f}"

    @property
    def price_rupees(self) -> str:
        return f"{self.price_paise / 100:.2f}"

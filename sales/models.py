import datetime
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
    has_shortage = models.BooleanField(
        default=False,
        help_text="Flag indicating whether finished goods stock was insufficient when reviewed/approved",
    )
    rejection_reason = models.TextField(
        blank=True,
        default='',
        help_text="Reason provided when an admin rejects the order",
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


class Invoice(models.Model):
    STATUS_POSTED = 'posted'
    STATUS_CANCELLED = 'cancelled'

    STATUS_CHOICES = [
        (STATUS_POSTED, 'Posted'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    number = models.CharField(max_length=50, unique=True)
    financial_year = models.CharField(max_length=20)
    order = models.ForeignKey(
        'sales.Order',
        on_delete=models.PROTECT,
        related_name='invoices',
    )
    customer = models.ForeignKey(
        'core.Customer',
        on_delete=models.PROTECT,
        related_name='invoices',
    )
    date = models.DateField(default=datetime.date.today)
    place_of_supply = models.CharField(max_length=100)
    taxable_paise = models.BigIntegerField(default=0)
    cgst_paise = models.BigIntegerField(default=0)
    sgst_paise = models.BigIntegerField(default=0)
    igst_paise = models.BigIntegerField(default=0)
    total_paise = models.BigIntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_POSTED,
    )

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return f"Invoice {self.number} - {self.customer.name} ({self.status})"

    @property
    def total_rupees(self) -> str:
        return f"{self.total_paise / 100:.2f}"

    @property
    def dispatch(self):
        return self.dispatches.first()


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name='lines',
    )
    item = models.ForeignKey(
        'core.Item',
        on_delete=models.PROTECT,
        related_name='invoice_lines',
    )
    hsn = models.CharField(max_length=20, blank=True, default='')
    qty = models.DecimalField(max_digits=14, decimal_places=3)
    rate_paise = models.IntegerField(help_text="Rate per unit in paise")
    taxable_paise = models.BigIntegerField(help_text="Taxable value in paise")
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    lot = models.ForeignKey(
        'core.StockLot',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='invoice_lines',
    )

    def __str__(self):
        return f"{self.item.name} x {self.qty} for Invoice {self.invoice.number}"

    @property
    def total_paise(self) -> int:
        return int(self.taxable_paise)


class Dispatch(models.Model):
    STATUS_DISPATCHED = 'dispatched'
    STATUS_CANCELLED = 'cancelled'

    STATUS_CHOICES = [
        (STATUS_DISPATCHED, 'Dispatched'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        related_name='dispatches',
    )
    date = models.DateField(default=datetime.date.today)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DISPATCHED,
    )

    def __str__(self):
        return f"Dispatch for Invoice {self.invoice.number} on {self.date}"


class Payment(models.Model):
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='payments',
    )
    customer = models.ForeignKey(
        'core.Customer',
        on_delete=models.PROTECT,
        related_name='payments',
    )
    amount_paise = models.BigIntegerField(help_text="Amount paid in paise")
    date = models.DateField(default=datetime.date.today)
    mode = models.CharField(max_length=50, blank=True, default='bank_transfer')
    reference = models.CharField(max_length=100, blank=True, default='')

    def __str__(self):
        return f"Payment of {self.amount_paise} paise by {self.customer.name}"

    @property
    def amount_rupees(self) -> str:
        return f"{self.amount_paise / 100:.2f}"


class CreditNote(models.Model):
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        related_name='credit_notes',
    )
    number = models.CharField(max_length=50, unique=True)
    reason = models.TextField(blank=True, default='')
    date = models.DateField(default=datetime.date.today)
    taxable_paise = models.BigIntegerField(default=0)
    cgst_paise = models.BigIntegerField(default=0)
    sgst_paise = models.BigIntegerField(default=0)
    igst_paise = models.BigIntegerField(default=0)
    total_paise = models.BigIntegerField(default=0)

    def __str__(self):
        return f"CreditNote {self.number} for Invoice {self.invoice.number}"

    @property
    def total_rupees(self) -> str:
        return f"{self.total_paise / 100:.2f}"


class InvoiceSequence(models.Model):
    financial_year = models.CharField(max_length=20, unique=True)
    last_number = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.financial_year}: {self.last_number}"


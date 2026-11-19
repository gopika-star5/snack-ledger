import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import decimal


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Unit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=20, unique=True)),
                ('kind', models.CharField(choices=[('mass', 'Mass'), ('volume', 'Volume'), ('count', 'Count')], max_length=20)),
                ('factor_to_base', models.DecimalField(decimal_places=4, max_digits=12)),
            ],
        ),
        migrations.CreateModel(
            name='Item',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('item_type', models.CharField(choices=[('raw', 'Raw Material'), ('packaging', 'Packaging'), ('finished', 'Finished Goods')], max_length=20)),
                ('stock_unit', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='items', to='core.unit')),
                ('hsn_code', models.CharField(blank=True, default='', max_length=20)),
                ('gst_rate_percent', models.DecimalField(decimal_places=2, default=decimal.Decimal('0.00'), max_digits=5)),
                ('selling_price_paise', models.IntegerField(blank=True, null=True)),
                ('shelf_life_days', models.IntegerField(blank=True, null=True)),
                ('reorder_level', models.DecimalField(blank=True, decimal_places=3, max_digits=14, null=True)),
                ('active', models.BooleanField(default=True)),
            ],
        ),
        migrations.CreateModel(
            name='Customer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('customer_type', models.CharField(choices=[('retailer', 'Retailer'), ('distributor', 'Distributor')], max_length=20)),
                ('gstin', models.CharField(blank=True, default='', max_length=15)),
                ('state', models.CharField(max_length=100)),
                ('address', models.TextField(blank=True, default='')),
                ('phone', models.CharField(blank=True, default='', max_length=20)),
                ('credit_limit_paise', models.BigIntegerField(blank=True, null=True)),
            ],
        ),
        migrations.CreateModel(
            name='Supplier',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('gstin', models.CharField(blank=True, default='', max_length=15)),
                ('state', models.CharField(max_length=100)),
            ],
        ),
        migrations.CreateModel(
            name='StockLot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='stock_lots', to='core.item')),
                ('batch_no', models.CharField(max_length=100)),
                ('received_date', models.DateField()),
                ('expiry_date', models.DateField(blank=True, null=True)),
                ('unit_cost_paise', models.IntegerField(help_text='Cost per stock unit in paise')),
                ('qty_received', models.DecimalField(decimal_places=3, max_digits=14)),
                ('source_type', models.CharField(blank=True, default='', max_length=50)),
                ('source_id', models.CharField(blank=True, default='', max_length=100, null=True)),
            ],
        ),
        migrations.CreateModel(
            name='StockMovement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='stock_movements', to='core.item')),
                ('lot', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='stock_movements', to='core.stocklot')),
                ('qty', models.DecimalField(decimal_places=3, help_text='Positive for in, negative for out', max_digits=14)),
                ('unit_cost_paise', models.IntegerField(default=0)),
                ('source_type', models.CharField(blank=True, default='', max_length=50)),
                ('source_id', models.CharField(blank=True, default='', max_length=100, null=True)),
                ('note', models.TextField(blank=True, default='')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='stock_movements', to=settings.AUTH_USER_MODEL)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name='AuditLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='audit_logs', to=settings.AUTH_USER_MODEL)),
                ('action', models.CharField(max_length=50)),
                ('model_name', models.CharField(blank=True, default='', max_length=100)),
                ('object_id', models.CharField(blank=True, default='', max_length=100)),
                ('details', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]

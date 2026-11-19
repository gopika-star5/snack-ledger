import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0003_companysettings'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Purchase',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField()),
                ('note', models.TextField(blank=True, default='')),
                ('total_paise', models.BigIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('supplier', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='purchases', to='core.supplier')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='purchases', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='PurchaseLine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('qty_in_purchase_unit', models.DecimalField(decimal_places=3, max_digits=14)),
                ('qty_in_stock_unit', models.DecimalField(decimal_places=3, max_digits=14)),
                ('unit_cost_paise', models.IntegerField(help_text='Rate per stock unit in paise')),
                ('line_total_paise', models.BigIntegerField(default=0)),
                ('purchase', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lines', to='production.purchase')),
                ('item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='purchase_lines', to='core.item')),
                ('purchase_unit', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='core.unit')),
            ],
        ),
    ]

from django.db import migrations
from decimal import Decimal

UNITS = [
    # (code, kind, factor_to_base)
    ('kg',     'mass',   Decimal('1000')),
    ('g',      'mass',   Decimal('1')),
    ('L',      'volume', Decimal('1000')),
    ('ml',     'volume', Decimal('1')),
    ('piece',  'count',  Decimal('1')),
    ('packet', 'count',  Decimal('1')),
    ('carton', 'count',  Decimal('20')),
]


def load_units(apps, schema_editor):
    Unit = apps.get_model('core', 'Unit')
    for code, kind, factor in UNITS:
        Unit.objects.get_or_create(
            code=code,
            defaults={'kind': kind, 'factor_to_base': factor}
        )


def unload_units(apps, schema_editor):
    Unit = apps.get_model('core', 'Unit')
    codes = [u[0] for u in UNITS]
    Unit.objects.filter(code__in=codes).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(load_units, reverse_code=unload_units),
    ]

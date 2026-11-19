from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_seed_units'),
    ]

    operations = [
        migrations.CreateModel(
            name='CompanySettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(default='', max_length=255)),
                ('gstin', models.CharField(default='', max_length=15)),
                ('state', models.CharField(default='', max_length=100)),
            ],
            options={
                'verbose_name': 'Company Settings',
                'verbose_name_plural': 'Company Settings',
            },
        ),
    ]

# Generated by Django 3.2.19 on 2023-06-18 21:25

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('staffing', '0019_auto_20230607_2253'),
    ]

    operations = [
        migrations.AlterField(
            model_name='mission',
            name='min_charge_multiple_per_day',
            field=models.FloatField(default=0, verbose_name='Min charge multiple per day'),
        ),
    ]
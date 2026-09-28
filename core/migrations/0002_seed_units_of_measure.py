"""
Seed the unit-of-measure master list from Section 3:
"m, m², m³, km, t, kg, L, nr, item, sum, day, hr."

This is reference data every install needs (not sample/demo data), so it
belongs in a migration rather than a management command someone has to
remember to run.
"""

from django.db import migrations


UNITS = [
    ("m", "Metre", "length"),
    ("m²", "Square metre", "area"),
    ("m³", "Cubic metre", "volume"),
    ("km", "Kilometre", "length"),
    ("t", "Tonne", "weight"),
    ("kg", "Kilogram", "weight"),
    ("L", "Litre", "volume"),
    ("nr", "Number", "count"),
    ("item", "Item", "count"),
    ("sum", "Lump sum", "lump_sum"),
    ("day", "Day", "time"),
    ("hr", "Hour", "time"),
]


def seed_units(apps, schema_editor):
    UnitOfMeasure = apps.get_model("core", "UnitOfMeasure")
    for code, name, unit_type in UNITS:
        UnitOfMeasure.objects.get_or_create(
            code=code, defaults={"name": name, "type": unit_type}
        )


def remove_units(apps, schema_editor):
    UnitOfMeasure = apps.get_model("core", "UnitOfMeasure")
    UnitOfMeasure.objects.filter(code__in=[code for code, _, _ in UNITS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_units, remove_units),
    ]

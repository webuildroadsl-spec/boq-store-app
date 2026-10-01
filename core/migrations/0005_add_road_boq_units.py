"""
Units found in real road-contract BOQs that the original Section 3 list
("m, m², m³, km, t, kg, L, nr, item, sum, day, hr") did not cover:

  ha    -- site clearance is measured by area in hectares
  m³·km -- "extra over for haulage" of material, volume x distance
  t·km  -- the same, by weight

Reference data every install needs, so (like 0002) it is a migration.
"""

from django.db import migrations

NEW_UNITS = [
    ("ha", "Hectare", "area"),
    ("m³·km", "Cubic metre-kilometre (haulage)", "haulage"),
    ("t·km", "Tonne-kilometre (haulage)", "haulage"),
]


def add_units(apps, schema_editor):
    UnitOfMeasure = apps.get_model("core", "UnitOfMeasure")
    for code, name, unit_type in NEW_UNITS:
        UnitOfMeasure.objects.get_or_create(code=code, defaults={"name": name, "type": unit_type})


def remove_units(apps, schema_editor):
    UnitOfMeasure = apps.get_model("core", "UnitOfMeasure")
    UnitOfMeasure.objects.filter(code__in=[code for code, _, _ in NEW_UNITS]).delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0004_unit_type_haulage")]
    operations = [migrations.RunPython(add_units, remove_units)]

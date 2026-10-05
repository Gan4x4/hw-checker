import uuid

from django.db import migrations, models


def populate_tokens(apps, schema_editor):
    tests = apps.get_model("quiz", "Test").objects.using(schema_editor.connection.alias)
    for test in tests.filter(results_token__isnull=True).iterator():
        tests.filter(pk=test.pk).update(results_token=uuid.uuid4())


class Migration(migrations.Migration):
    dependencies = [("quiz", "0011_academic_year")]

    operations = [
        migrations.AddField(
            model_name="test", name="results_token",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(populate_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="test", name="results_token",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("quiz", "0006_test_model"),
    ]

    operations = [
        migrations.AddField(
            model_name="quizlink",
            name="included_question_ids",
            field=models.JSONField(blank=True, default=list),
        ),
    ]

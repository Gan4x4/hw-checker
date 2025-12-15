from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ("quiz", "0008_quizlink_original_filename"),
    ]

    operations = [
        migrations.AddField(
            model_name="test",
            name="question_timeout",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Per-question time limit in seconds. Defaults to QUIZ_QUESTION_TIMEOUT.",
                null=True,
                validators=[django.core.validators.MinValueValidator(1)],
            ),
        ),
    ]

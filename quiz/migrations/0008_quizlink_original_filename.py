from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("quiz", "0007_quizlink_included_question_ids"),
    ]

    operations = [
        migrations.AddField(
            model_name="quizlink",
            name="original_filename",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
    ]

from __future__ import annotations

from datetime import timedelta

from django import forms
from django.conf import settings

from .models import QuizLink, Student, Test, academic_year_validator, default_academic_year


class TestAdminForm(forms.ModelForm):
    academic_year = forms.ChoiceField()
    duration = forms.FloatField(label="Duration (minutes)", min_value=0, help_text="Total time the test stays active, in minutes.")

    class Meta:
        model = Test
        fields = ("academic_year", "title", "duration", "question_timeout")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        years = {default_academic_year()}
        for model in (Student, Test, QuizLink):
            years.update(model.objects.order_by().values_list("academic_year", flat=True).distinct())
        self.fields["academic_year"].choices = [(year, year) for year in sorted(years)]
        duration = self.initial.get("duration")
        if isinstance(duration, timedelta):
            self.initial["duration"] = duration.total_seconds() / 60

    def clean_duration(self):
        try:
            duration = timedelta(minutes=self.cleaned_data["duration"])
        except OverflowError:
            raise forms.ValidationError("Duration is too large.") from None
        if duration <= timedelta(0):
            raise forms.ValidationError("Duration must be positive.")
        return duration


class QuizImportForm(forms.Form):
    academic_year = forms.CharField(max_length=7, initial=default_academic_year, validators=[academic_year_validator])
    json_file = forms.FileField(
        label="Quiz JSON file",
        help_text="Upload a JSON file describing the quiz questions.",
    )
    student = forms.ModelChoiceField(
        queryset=Student.objects.all(),
        required=False,
        label="Student",
        help_text="Select the student this quiz belongs to.",
        empty_label="— No student —",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["student"].queryset = Student.objects.all().order_by("name")

    def clean(self):
        cleaned = super().clean()
        student = cleaned.get("student")
        if student and student.academic_year != cleaned.get("academic_year"):
            self.add_error("student", "Choose a student from the selected academic year.")
        return cleaned


class StudentImportForm(forms.Form):
    academic_year = forms.CharField(max_length=7, initial=default_academic_year, validators=[academic_year_validator])
    csv_file = forms.FileField(
        label="Students CSV file",
        help_text="UTF-8 CSV: name/email required, course/group optional. ФИО/Курс/Группа headers are also accepted.",
    )


class TestCreationForm(forms.Form):
    title = forms.CharField(
        max_length=255,
        required=False,
        label="Title",
        help_text="Optional name that will be shown in admin interfaces.",
    )
    duration_minutes = forms.IntegerField(
        min_value=1,
        initial=5,
        label="Duration (minutes)",
        help_text="How long the test remains active after starting.",
    )
    question_timeout_seconds = forms.IntegerField(
        min_value=1,
        required=False,
        label="Question timeout (seconds)",
        help_text="Optional per-question time limit for this test.",
        initial=getattr(settings, "QUIZ_QUESTION_TIMEOUT", 60),
    )

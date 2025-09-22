from __future__ import annotations

from django import forms

from .models import Student


class QuizImportForm(forms.Form):
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


class StudentImportForm(forms.Form):
    csv_file = forms.FileField(
        label="Students CSV file",
        help_text="Upload a UTF-8 encoded CSV with columns: name, email, course, group.",
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

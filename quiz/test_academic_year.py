import io
import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from .forms import QuizImportForm, TestAdminForm
from .admin import _infer_student_from_filename, _student_slug_tokens
from .management.commands.import_questions import import_quiz_from_json
from .models import Attempt, Question, QuizLink, QuizQuestion, Student, Test
from .utils import import_students_from_content


class StudentFilenameMatchingTests(SimpleTestCase):
    def test_transliterated_filenames_and_ambiguous_names(self):
        cases = [
            ("Балдина Анастасия Сергеевна", "Baldina_Anastasiya_Sergeevna"),
            ("Чепурны()х Владислав Евгеньевич", "Chepurnykh_Vladislav_Evgenevich"),
            ("Ивани()цкий Леонид Дмитриевич", "Ivanitskii_Leonid_Dmitrievich"),
            ("Чураков Пётр Максимович", "Churakov_Petr_Maksimovich"),
            ("Курюкин Егор Сергеевич", "Kuryukin_Egor_Sergeevich"),
            ("Якимов Георгий Юрьевич", "Yakimov_Georgii_Yurevich"),
            ("José Pérez", "Perez"),
            ("Балдина Ирина Сергеевна", "Baldina_Irina_Sergeevna"),
        ]
        students = [Student(name=name, email=f"student{index}@example.com") for index, (name, _) in enumerate(cases)]
        tokens = [(student, _student_slug_tokens(student)) for student in students]
        for student, (_, filename) in zip(students, cases):
            with self.subTest(filename=filename):
                self.assertIs(_infer_student_from_filename(filename + "_questions.json", tokens), student)
        self.assertIsNone(_infer_student_from_filename("Baldina_questions.json", tokens))
        self.assertIsNone(_infer_student_from_filename("Unknown_questions.json", tokens))


class AcademicYearTests(TestCase):
    def setUp(self):
        self.old = Student.objects.create(name="Ivan Popov", email="popov@example.com", academic_year="2025_26", group="A")
        self.new = Student.objects.create(name="Ivan Popov", email="popov@example.com", academic_year="2026_27")
        self.client.force_login(get_user_model().objects.create_superuser("yearadmin", password="test"))
        self.payload = json.dumps([{"question": "Q?", "answers": ["A", "B"], "correct_answer_index": 0}])

    def test_csv_formats_and_optional_columns(self):
        content = "\ufeff,,\nФИО,email,Курс\nIvan Popov,popov@example.com,4 курс\n"
        self.assertEqual(import_students_from_content(content, academic_year="2026_27"), 1)
        self.new.refresh_from_db()
        self.old.refresh_from_db()
        self.assertEqual(self.new.course, "4 курс")
        self.assertEqual(self.old.course, "")
        import_students_from_content("name,email\nUpdated,popov@example.com\n", academic_year="2025_26")
        self.old.refresh_from_db()
        self.assertEqual((self.old.name, self.old.group), ("Updated", "A"))
        self.assertEqual(Student.objects.count(), 2)

    def test_csv_validation_is_atomic(self):
        with self.assertRaises(ValidationError):
            import_students_from_content("name,email\nValid,valid@example.com\nInvalid,not-an-email\n")
        self.assertEqual(Student.objects.count(), 2)
        with self.assertRaises(ValueError):
            import_students_from_content("name,course\nNobody,3\n")
        with self.assertRaises(ValidationError):
            import_students_from_content("name,email\nValid,valid@example.com\n", academic_year="bad")

    def test_cli_imports_honor_explicit_year(self):
        with TemporaryDirectory() as directory:
            csv_path = Path(directory) / "students.csv"
            csv_path.write_text("ФИО,email\nCLI Student,cli@example.com\n", encoding="utf-8-sig")
            call_command("import_students", str(csv_path), academic_year="2025_26", stdout=io.StringIO())
            self.assertTrue(Student.objects.filter(email="cli@example.com", academic_year="2025_26").exists())
            json_path = Path(directory) / "questions.json"
            json_path.write_text(self.payload)
            call_command("import_questions", str(json_path), academic_year="2025_26", stdout=io.StringIO())
            self.assertEqual(QuizLink.objects.get().academic_year, "2025_26")

    def test_admin_student_import_and_year_filters(self):
        csv_file = SimpleUploadedFile("students.csv", b"name,email\nSomeone,new@example.com\n")
        response = self.client.post(reverse("admin:quiz_student_changelist"), {"academic_year": "2025_26", "csv_file": csv_file})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Student.objects.filter(email="new@example.com", academic_year="2025_26").exists())
        for model, url_name in ((Student, "student"), (Test, "test"), (QuizLink, "quizlink")):
            if model is not Student:
                for year in ("2025_26", "2026_27"):
                    values = {"academic_year": year}
                    if model is Test:
                        values["duration"] = timedelta(minutes=5)
                    model.objects.create(**values)
            response = self.client.get(reverse(f"admin:quiz_{url_name}_changelist"), {"academic_year": "2025_26"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(set(response.context["cl"].queryset.values_list("academic_year", flat=True)), {"2025_26"})

    @override_settings(DEFAULT_ACADEMIC_YEAR="2025_26")
    def test_student_list_defaults_to_configured_year_with_explicit_all(self):
        url = reverse("admin:quiz_student_changelist")
        for params, expected in (({}, [self.old]), ({"academic_year": "2026_27"}, [self.new]),
                                 ({"academic_year": "all"}, [self.old, self.new])):
            response = self.client.get(url, params)
            self.assertEqual(response.status_code, 200)
            self.assertCountEqual(response.context["cl"].queryset, expected)
            choices = list(response.context["cl"].filter_specs[0].choices(response.context["cl"]))
            selected = [choice["display"] for choice in choices if choice["selected"]]
            self.assertEqual(selected, ["All" if params.get("academic_year") == "all" else params.get("academic_year", "2025_26")])
            self.assertIn("academic_year=all", choices[0]["query_string"])
        response = self.client.get(url, {"academic_year": "all", "q": "Nobody"})
        self.assertFalse(response.context["cl"].queryset.exists())

    @override_settings(DEFAULT_ACADEMIC_YEAR="2025_26")
    def test_every_admin_list_defaults_to_year_and_all_remains_available(self):
        expected = {name: {} for name in ("student", "test", "quizlink", "question", "attempt", "quizquestionfeedback")}
        for student in (self.old, self.new):
            year = student.academic_year
            test = Test.objects.create(academic_year=year, duration=timedelta(minutes=5))
            quiz, _, _ = import_quiz_from_json(self.payload, default_name=year, academic_year=year)
            question = quiz.questions.get()
            quiz_question = quiz.quiz_questions.get()
            quiz_question.disabled_comment = "Feedback"
            quiz_question.save(update_fields=["disabled_comment"])
            attempt = Attempt.objects.create(quiz=quiz, question=question)
            for name, obj in zip(expected, (student, test, quiz, question, attempt, quiz_question)):
                expected[name][year] = [obj.pk]
            # A question shared by two quizzes in one year must only appear once.
            other_quiz = QuizLink.objects.create(academic_year=year)
            QuizQuestion.objects.create(quiz=other_quiz, question=question, order=1)
            expected["quizlink"][year].append(other_quiz.pk)
        Question.objects.create(question="Unassigned", answers=["A"], correct_answer_index=0)
        for name, years in expected.items():
            url = reverse(f"admin:quiz_{name}_changelist")
            for params, ids in (({}, years["2025_26"]), ({"academic_year": "2026_27"}, years["2026_27"])):
                with self.subTest(model=name, params=params):
                    response = self.client.get(url, params)
                    self.assertEqual(response.status_code, 200)
                    self.assertCountEqual(response.context["cl"].queryset.values_list("pk", flat=True), ids)
            response = self.client.get(url, {"academic_year": "all"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["cl"].queryset.count(), len(years["2025_26"] + years["2026_27"]) + (name == "question"))

    def test_bulk_quiz_import_uses_test_year(self):
        test = Test.objects.create(academic_year="2025_26", duration=timedelta(minutes=5))
        upload = SimpleUploadedFile("Popov_questions.json", self.payload.encode())
        response = self.client.post(reverse("admin:quiz_test_change", args=[test.pk]), {"_import_questions": "1", "json_files": upload})
        self.assertEqual(response.status_code, 302)
        quiz = test.quizzes.get()
        self.assertEqual((quiz.student_id, quiz.academic_year), (self.old.pk, "2025_26"))

    def test_transliterated_bulk_import_stays_in_test_year(self):
        Student.objects.filter(pk__in=[self.old.pk, self.new.pk]).update(name="Балдина Анастасия Сергеевна")
        test = Test.objects.create(academic_year="2025_26", duration=timedelta(minutes=5))
        upload = SimpleUploadedFile("Baldina_Anastasiya_Sergeevna_questions.json", self.payload.encode())
        response = self.client.post(reverse("admin:quiz_test_change", args=[test.pk]), {"_import_questions": "1", "json_files": upload})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(test.quizzes.get().student_id, self.old.pk)

    def test_test_admin_uses_minutes_dropdown_and_hides_creation_metadata(self):
        url = reverse("admin:quiz_test_add")
        response = self.client.get(url)
        self.assertContains(response, '<select name="academic_year"')
        self.assertContains(response, 'Duration (minutes)')
        fields = response.context["adminform"].fieldsets[0][1]["fields"]
        self.assertFalse({"state", "started_at", "finished_at", "created_at"}.intersection(fields))
        data = {"title": "Minutes test", "academic_year": "2025_26", "duration": "15", "question_timeout": "60"}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        test = Test.objects.get(title="Minutes test")
        self.assertEqual(test.duration, timedelta(minutes=15))
        url = reverse("admin:quiz_test_change", args=[test.pk])
        response = self.client.get(url)
        self.assertEqual(response.context["adminform"].form.initial["duration"], 15)
        self.assertIn("state", response.context["adminform"].fieldsets[0][1]["fields"])
        data["duration"] = "1.5"
        self.assertEqual(self.client.post(url, data).status_code, 302)
        test.refresh_from_db()
        self.assertEqual(test.duration, timedelta(seconds=90))
        for value in ("0", "-1", "1e100"):
            form = TestAdminForm(data={**data, "duration": value})
            self.assertFalse(form.is_valid())
            self.assertIn("duration", form.errors)

    def test_standalone_import_and_cross_year_validation(self):
        upload = SimpleUploadedFile("questions.json", self.payload.encode())
        response = self.client.post(reverse("admin:quiz_quizlink_import"), {"academic_year": "2025_26", "student": self.old.pk, "json_file": upload})
        self.assertEqual(response.status_code, 302)
        quiz = QuizLink.objects.get()
        self.assertEqual((quiz.academic_year, quiz.student_id), ("2025_26", self.old.pk))
        quiz.student = self.new
        with self.assertRaises(ValidationError):
            quiz.full_clean()
        form = QuizImportForm({"academic_year": "2025_26", "student": self.new.pk}, {"json_file": SimpleUploadedFile("q.json", self.payload.encode())})
        self.assertFalse(form.is_valid())
        self.assertIn("student", form.errors)

    def test_make_test_inherits_year_and_rejects_mixed_years(self):
        first = QuizLink.objects.create(academic_year="2025_26")
        second = QuizLink.objects.create(academic_year="2026_27")
        data = {"action": "make_test_action", "_selected_action": [first.pk, second.pk], "apply": "1", "duration_minutes": "5"}
        self.client.post(reverse("admin:quiz_quizlink_changelist") + "?academic_year=all", data)
        self.assertFalse(Test.objects.exists())
        data["_selected_action"] = [first.pk]
        self.client.post(reverse("admin:quiz_quizlink_changelist") + "?academic_year=2025_26", data)
        self.assertEqual(Test.objects.get().academic_year, "2025_26")

    def test_replace_keeps_other_years_and_their_questions(self):
        old, _, _ = import_quiz_from_json(self.payload, default_name="old", academic_year="2025_26")
        current, _, _ = import_quiz_from_json(self.payload, default_name="current", academic_year="2026_27")
        shared = old.questions.get()
        QuizQuestion.objects.create(quiz=current, question=shared, order=2)
        replacement, _, _ = import_quiz_from_json(self.payload, default_name="replacement", academic_year="2026_27", replace=True)
        self.assertEqual(old.questions.get().pk, shared.pk)
        self.assertFalse(QuizLink.objects.filter(pk=current.pk).exists())
        self.assertEqual(replacement.academic_year, "2026_27")


class AcademicYearMigrationTests(TransactionTestCase):
    @override_settings(DEFAULT_ACADEMIC_YEAR="2030_31")
    def test_existing_records_get_legacy_year_independently_of_default(self):
        executor = MigrationExecutor(connection)
        before = [("quiz", "0010_quizquestionfeedback")]
        after = [("quiz", "0011_academic_year")]
        executor.migrate(before)
        try:
            apps = executor.loader.project_state(before).apps
            apps.get_model("quiz", "Student").objects.create(name="Legacy", email="legacy@example.com", course="3 курс")
            apps.get_model("quiz", "Test").objects.create(duration=timedelta(minutes=5))
            apps.get_model("quiz", "QuizLink").objects.create()
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            for model in (Student, Test, QuizLink):
                self.assertEqual(model.objects.get().academic_year, "2025_26")
            self.assertEqual(Student.objects.get().course, "3 курс")
            self.assertEqual(Student(name="New", email="new@example.com").academic_year, "2030_31")
        finally:
            MigrationExecutor(connection).migrate(after)

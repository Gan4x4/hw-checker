import csv
import io
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from .models import Attempt, Question, QuizLink, QuizQuestion, Student, Test


class TestSummaryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("summaryadmin", password="test")
        self.client.force_login(self.user)
        self.test = Test.objects.create(title="Old exam", academic_year="2025_26", duration=timedelta(minutes=30))
        student = Student.objects.create(name="Alice Example", email="alice@example.com", academic_year=self.test.academic_year)
        self.quiz = QuizLink.objects.create(test=self.test, student=student, academic_year=self.test.academic_year)

    def question(self, text, **kwargs):
        question = Question.objects.create(question=text, answers=["Right", "Wrong"], correct_answer_index=0)
        return QuizQuestion.objects.create(quiz=self.quiz, question=question, order=self.quiz.quiz_questions.count() + 1, **kwargs)

    def url(self, kind):
        return reverse("admin:quiz_test_summary", args=[self.test.pk, kind])

    def test_incorrect_summary_uses_latest_attempt_and_only_this_test(self):
        wrong = self.question("Wrong question", disabled_comment="Please clarify this answer")
        timeout = self.question("Timed out")
        corrected = self.question("Corrected question")
        disabled = self.question("Disabled question", is_disabled=True)
        self.question("Never attempted")
        for row in (wrong, corrected, disabled):
            Attempt.objects.create(quiz=self.quiz, question=row.question, selected_answer_index=1)
        Attempt.objects.create(quiz=self.quiz, question=timeout.question)
        Attempt.objects.create(quiz=self.quiz, question=corrected.question, selected_answer_index=0)
        other = QuizLink.objects.create()
        QuizQuestion.objects.create(quiz=other, question=wrong.question, order=1)
        Attempt.objects.create(quiz=other, question=wrong.question, selected_answer_index=1)

        response = self.client.get(self.url("incorrect"))
        self.assertEqual([row.pk for row in response.context["page"]], [wrong.pk, timeout.pk])
        self.assertContains(response, "Alice Example")
        self.assertContains(response, "No answer")
        self.assertContains(response, "Right")
        self.assertContains(response, "Please clarify this answer")
        self.assertContains(response, f'<a href="{reverse("admin:quiz_quizlink_results", args=[self.quiz.pk])}">Alice Example</a>', html=True)
        self.assertContains(response, f"?focus={wrong.pk}#question-{wrong.pk}")
        view = self.client.get(reverse("admin:quiz_test_view", args=[self.test.pk]))
        for kind in ("incorrect", "feedback"):
            self.assertContains(view, self.url(kind))

    def test_feedback_summary_includes_unanswered_feedback_and_escapes_content(self):
        feedback = self.question("Question <script>bad()</script>", disabled_comment="Please clarify <b>this</b>")
        self.question("No feedback")
        self.question("Instructor note", is_disabled=True, disabled_comment="Disabled reason")
        other = QuizLink.objects.create()
        QuizQuestion.objects.create(quiz=other, question=feedback.question, order=1, disabled_comment="Other test")
        response = self.client.get(self.url("feedback"))
        self.assertEqual([row.pk for row in response.context["page"]], [feedback.pk])
        self.assertContains(response, "Alice Example")
        self.assertContains(response, "Please clarify &lt;b&gt;this&lt;/b&gt;")
        self.assertNotContains(response, "<script>bad()</script>")

    def test_empty_missing_and_permission_checks(self):
        self.assertContains(self.client.get(self.url("incorrect")), "No matching questions.")
        self.assertEqual(self.client.get(self.url("unknown")).status_code, 404)
        missing = reverse("admin:quiz_test_summary", args=[self.test.pk + 100, "feedback"])
        self.assertEqual(self.client.get(missing).status_code, 404)
        staff = get_user_model().objects.create_user("staff", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.url("feedback")).status_code, 403)
        staff.user_permissions.add(Permission.objects.get(codename="view_test"))
        self.assertEqual(self.client.get(self.url("feedback")).status_code, 403)
        staff.user_permissions.add(Permission.objects.get(codename="view_quizquestionfeedback"))
        self.assertEqual(self.client.get(self.url("feedback")).status_code, 200)
        self.assertEqual(self.client.get(self.url("incorrect")).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url("feedback")).status_code, 302)

    @override_settings(QUIZ_MAX_QUESTIONS=2)
    def test_public_csv_uses_final_scores_and_private_headers(self):
        right = self.question("Right")
        wrong = self.question("Wrong")
        disabled = self.question("Disabled", is_disabled=True)
        excluded = self.question("Excluded")
        self.quiz.included_question_ids = [right.pk, wrong.pk, disabled.pk]
        self.quiz.save(update_fields=["included_question_ids"])
        for row in (right, wrong, disabled, excluded):
            Attempt.objects.create(quiz=self.quiz, question=row.question, selected_answer_index=0)
        Attempt.objects.create(quiz=self.quiz, question=wrong.question, selected_answer_index=1)
        other = Test.objects.create(title="Other", duration=timedelta(minutes=10))
        QuizLink.objects.create(test=other, student=self.quiz.student)
        url = reverse("quiz:test_results_csv", args=[self.test.results_token])
        view = self.client.get(reverse("admin:quiz_test_view", args=[self.test.pk]))
        self.assertContains(view, "http://testserver" + url)
        self.client.logout()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(csv.reader(io.StringIO(response.content.decode("utf-8-sig")))), [
            ["Student name", "Final score (%)"], ["Alice Example", "50.00"],
        ])
        self.assertIn("noindex", response["X-Robots-Tag"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(self.client.post(url).status_code, 405)
        self.assertEqual(self.client.get(reverse("quiz:test_results_csv", args=[uuid.uuid4()])).status_code, 404)
        self.test.refresh_from_db()
        self.assertEqual(url, reverse("quiz:test_results_csv", args=[self.test.results_token]))

    def test_csv_handles_multiple_quizzes_empty_scores_and_formula_names(self):
        self.quiz.student.name = '=HYPERLINK("bad")'
        self.quiz.student.save()
        second = QuizLink.objects.create(test=self.test, student=self.quiz.student)
        row = self.question("No answer yet")
        QuizQuestion.objects.create(quiz=second, question=row.question, order=1)
        Attempt.objects.create(quiz=second, question=row.question, selected_answer_index=0)
        empty_student = Student.objects.create(name="Empty", email="empty@example.com")
        QuizLink.objects.create(test=self.test, student=empty_student)
        self.client.logout()
        response = self.client.get(reverse("quiz:test_results_csv", args=[self.test.results_token]))
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(rows[1:], [["'=HYPERLINK(\"bad\")", "50.00"], ["Empty", ""]])


class ResultsTokenMigrationTests(TransactionTestCase):
    def test_existing_tests_receive_distinct_tokens(self):
        executor = MigrationExecutor(connection)
        before = [("quiz", "0011_academic_year")]
        after = [("quiz", "0012_test_results_token")]
        executor.migrate(before)
        try:
            historical_test = executor.loader.project_state(before).apps.get_model("quiz", "Test")
            for _ in range(2):
                historical_test.objects.create(duration=timedelta(minutes=5))
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            tokens = list(Test.objects.values_list("results_token", flat=True))
            self.assertEqual(len(set(tokens)), 2)
            self.assertTrue(all(token.version == 4 for token in tokens))
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())

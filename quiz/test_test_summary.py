from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
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
        wrong = self.question("Wrong question")
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

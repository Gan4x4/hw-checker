from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from .admin import QuizLinkAdmin, StudentAdmin
from .management.commands.import_questions import import_quiz_from_json
from .models import Attempt, Question, QuizLink, QuizQuestion, Student
from .views import QuizSessionView


class QuizImportTests(TestCase):
    def test_import_uses_name_and_student_for_title(self):
        payload = {
            "name": "EX1_CIFAR",
            "student": "Попов",
            "questions": [
                {
                    "question": "What is 2 + 2?",
                    "answers": ["3", "4"],
                    "correct_answer_index": 1,
                    "weight": 1.5,
                    "source": "cell 5",
                }
            ],
        }

        quiz, created, student_name = import_quiz_from_json(json.dumps(payload), default_name="fallback")

        self.assertEqual(created, 1)
        self.assertEqual(quiz.title, "EX1_CIFAR - Попов")
        self.assertEqual(student_name, "Попов")
        quiz_question = quiz.ordered_quiz_questions().first()
        self.assertIsNotNone(quiz_question)
        self.assertEqual(quiz_question.question.source, "cell 5")
        self.assertEqual(quiz_question.question.correct_answer_index, 1)
        self.assertEqual(quiz_question.question.penalty, 1.5)


class QuestionImageRenderTests(TestCase):
    def test_generate_image_adds_source_text(self):
        question = Question.objects.create(
            question="What is 2 + 2?",
            answers=["3", "4"],
            correct_answer_index=1,
            source="cell 5",
        )

        draw_calls = []

        def fake_draw(image):
            class DummyDraw:
                def __init__(self):
                    self.calls = []

                def text(self, position, text, fill, font):
                    self.calls.append((position, text, font))

            draw = DummyDraw()
            draw_calls.append(draw)
            return draw

        with self.settings(MEDIA_ROOT="ignored"), \
            patch("quiz.models.Path.mkdir", return_value=None), \
            patch("PIL.Image.Image.save", return_value=None), \
            patch("quiz.models.ImageDraw.Draw", side_effect=fake_draw):
            question.generate_image()

        texts = [text for draw in draw_calls for (_, text, _) in draw.calls if text]
        self.assertIn("Source: cell 5", texts)


class QuizSessionResultsTests(TestCase):
    def setUp(self):
        self.question = Question.objects.create(
            question="Select true",
            answers=["False", "True"],
            correct_answer_index=1,
            penalty=2.5,
        )
        self.quiz = QuizLink.objects.create(title="Session quiz")
        QuizQuestion.objects.create(quiz=self.quiz, question=self.question, order=1)

    def test_build_results_compiles_summary(self):
        Attempt.objects.create(
            quiz=self.quiz,
            question=self.question,
            selected_answer_index=1,
            time_spent=8.2,
        )

        rows, score = QuizSessionView._build_results(self.quiz)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["status"], "correct")
        self.assertEqual(row["selected_answer"], "True")
        self.assertEqual(row["correct_answer"], "True")
        self.assertEqual(row["answers"], ["False", "True"])
        self.assertEqual(row["time_spent"], 8.2)
        self.assertEqual(row["weight"], 2.5)
        self.assertEqual(score["correct"], 1)
        self.assertEqual(score["total"], 1)
        self.assertEqual(score["attempted"], 1)
        self.assertAlmostEqual(score["percent"], 100.0)

    def test_completed_view_renders_results_in_context(self):
        Attempt.objects.create(
            quiz=self.quiz,
            question=self.question,
            selected_answer_index=0,
            time_spent=5.0,
        )

        session = self.client.session
        session[QuizSessionView._start_flag_key(self.quiz.pk)] = True
        session.save()

        response = self.client.get(reverse("quiz:session", args=[self.quiz.token]))

        self.assertEqual(response.status_code, 200)
        self.assertIn("rows", response.context)
        rows = response.context["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "incorrect")
        score = response.context["score"]
        self.assertEqual(score["correct"], 0)
        self.assertEqual(score["total"], 1)


class QuizLinkResetTests(TestCase):
    def setUp(self):
        self.question = Question.objects.create(
            question="What is 2 + 2?",
            answers=["3", "4", "5"],
            correct_answer_index=1,
        )
        self.quiz = QuizLink.objects.create(title="Simple quiz")
        QuizQuestion.objects.create(quiz=self.quiz, question=self.question, order=1)

    def test_reset_clears_attempts_and_completion(self):
        Attempt.objects.create(quiz=self.quiz, question=self.question, selected_answer_index=1)
        self.quiz.completed_at = timezone.now()
        self.quiz.save(update_fields=["completed_at"])

        deleted = self.quiz.reset()

        self.assertEqual(deleted, 1)
        self.assertFalse(Attempt.objects.filter(quiz=self.quiz).exists())
        self.quiz.refresh_from_db(fields=["completed_at"])
        self.assertIsNone(self.quiz.completed_at)


class QuizLinkAdminActionsTests(TestCase):
    def setUp(self):
        self.admin_site = AdminSite()
        self.admin = QuizLinkAdmin(QuizLink, self.admin_site)
        self.factory = RequestFactory()
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password123",
        )
        question = Question.objects.create(
            question="Select true",
            answers=["False", "True"],
            correct_answer_index=1,
        )
        self.quiz = QuizLink.objects.create(title="Admin quiz")
        QuizQuestion.objects.create(quiz=self.quiz, question=question, order=1)

    def test_admin_actions_renders_open_when_fresh(self):
        html = self.admin.admin_actions(self.quiz)
        self.assertIn("View", html)
        self.assertIn("Open", html)
        self.assertNotIn("Reset", html)

    def test_admin_actions_renders_reset_when_attempts_exist(self):
        Attempt.objects.create(quiz=self.quiz, question=self.quiz.quiz_questions.first().question)
        html = self.admin.admin_actions(self.quiz)
        self.assertIn("View", html)
        self.assertIn("Reset", html)
        self.assertNotIn("Open", html)

    def test_admin_actions_renders_reset_when_marked_completed(self):
        self.quiz.completed_at = timezone.now()
        self.quiz.save(update_fields=["completed_at"])
        html = self.admin.admin_actions(self.quiz)
        self.assertIn("View", html)
        self.assertIn("Reset", html)
        self.assertNotIn("Open", html)

    def test_reset_view_clears_state_and_redirects(self):
        Attempt.objects.create(quiz=self.quiz, question=self.quiz.quiz_questions.first().question)
        self.quiz.completed_at = timezone.now()
        self.quiz.save(update_fields=["completed_at"])

        request = self.factory.post("/reset/")
        request.session = self.client.session
        request.user = self.superuser
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)
        response = self.admin.reset_view(request, self.quiz.pk)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Attempt.objects.filter(quiz=self.quiz).exists())
        self.quiz.refresh_from_db(fields=["completed_at"])
        self.assertIsNone(self.quiz.completed_at)

    def test_reset_endpoint_behaves_like_admin_button(self):
        Attempt.objects.create(quiz=self.quiz, question=self.quiz.quiz_questions.first().question)
        self.quiz.completed_at = timezone.now()
        self.quiz.save(update_fields=["completed_at"])

        self.client.force_login(self.superuser)
        url = f"/admin/quiz/quizlink/{self.quiz.pk}/reset/"
        response = self.client.post(url, follow=True)

        self.assertEqual(response.status_code, 200)
        self.quiz.refresh_from_db(fields=["completed_at"])
        self.assertIsNone(self.quiz.completed_at)
        self.assertFalse(Attempt.objects.filter(quiz=self.quiz).exists())

    def test_score_display_uses_annotations(self):
        question_two = Question.objects.create(
            question="Second?",
            answers=["No", "Yes"],
            correct_answer_index=1,
        )
        QuizQuestion.objects.create(quiz=self.quiz, question=question_two, order=2)

        Attempt.objects.create(
            quiz=self.quiz,
            question=self.quiz.quiz_questions.first().question,
            selected_answer_index=1,
        )
        Attempt.objects.create(
            quiz=self.quiz,
            question=question_two,
            selected_answer_index=0,
        )

        request = self.factory.get("/admin/quiz/quizlink/")
        request.user = self.superuser
        annotated_quiz = self.admin.get_queryset(request).get(pk=self.quiz.pk)
        self.assertEqual(self.admin.score_display(annotated_quiz), "1/2 (50%)")

    def test_results_view_includes_attempt_details(self):
        question = self.quiz.quiz_questions.first().question
        Attempt.objects.create(
            quiz=self.quiz,
            question=question,
            selected_answer_index=1,
            time_spent=12.3,
        )

        request = self.factory.get("/admin/quiz/quizlink/{}/results/".format(self.quiz.pk))
        request.user = self.superuser
        response = self.admin.results_view(request, self.quiz.pk)
        self.assertEqual(response.status_code, 200)
        response.render()
        rows = response.context_data["rows"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["selected_answer"], "True")
        self.assertEqual(row["status"], "correct")
        self.assertEqual(row["weight"], question.penalty)
        self.assertEqual(row["answers"], question.answers)
        score = response.context_data["score"]
        self.assertEqual(score["correct"], 1)
        self.assertEqual(score["total"], 1)


class StudentAdminTests(TestCase):
    def setUp(self):
        self.admin_site = AdminSite()
        self.admin = StudentAdmin(Student, self.admin_site)
        self.factory = RequestFactory()

        self.student = Student.objects.create(
            name="Test Student",
            email="student@example.com",
            course="Course",
            group="Group",
        )

        self.question1 = Question.objects.create(
            question="Q1",
            answers=["A", "B"],
            correct_answer_index=1,
            penalty=2.0,
        )
        self.question2 = Question.objects.create(
            question="Q2",
            answers=["A", "B"],
            correct_answer_index=0,
            penalty=3.0,
        )

        self.quiz = QuizLink.objects.create(title="Quiz", student=self.student)
        QuizQuestion.objects.create(quiz=self.quiz, question=self.question1, order=1)
        QuizQuestion.objects.create(quiz=self.quiz, question=self.question2, order=2)

    def test_overall_grade_and_score_columns(self):
        Attempt.objects.create(
            quiz=self.quiz,
            question=self.question1,
            selected_answer_index=1,
        )
        request = self.factory.get("/admin/quiz/student/")
        queryset = self.admin.get_queryset(request)
        student = queryset.get(pk=self.student.pk)

        self.assertEqual(self.admin.overall_grade(student), "3.00")
        self.assertEqual(self.admin.score_percent(student), "100%")
        actions_html = self.admin.student_actions(student)
        self.assertIn(">1<", actions_html)

    def test_quizzes_view_lists_completed_quizzes(self):
        Attempt.objects.create(
            quiz=self.quiz,
            question=self.question1,
            selected_answer_index=1,
        )
        self.quiz.completed_at = timezone.now()
        self.quiz.save(update_fields=["completed_at"])

        request = self.factory.get(f"/admin/quiz/student/{self.student.pk}/quizzes/")
        request.user = get_user_model().objects.create_superuser(
            username="admin2",
            email="admin2@example.com",
            password="password123",
        )

        response = self.admin.quizzes_view(request, self.student.pk)
        self.assertEqual(response.status_code, 200)
        response.render()
        self.assertTrue(response.context_data["rows"])

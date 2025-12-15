from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .admin import QuizLinkAdmin, StudentAdmin, TestAdmin
from .management.commands.import_questions import import_quiz_from_json
from .models import Attempt, Question, QuizLink, QuizQuestion, Student, Test, TestState
from .templatetags.quiz_extras import wrap_long_lines
from .utils import wrap_code_snippet, wrap_text, wrap_text_html, wrap_text_to_lines
from .views import QuizQuestionFeedbackView, QuizSessionView


class TextWrappingTests(SimpleTestCase):
    def test_wrap_text_to_lines_splits_long_words(self):
        lines = wrap_text_to_lines("x" * 17, width=5)

        self.assertGreaterEqual(len(lines), 4)
        self.assertTrue(all(len(line) <= 5 for line in lines if line))

    def test_wrap_text_preserves_blank_lines(self):
        text = "line one\n\nline two"
        wrapped = wrap_text(text, width=5)

        self.assertIn("\n\n", wrapped)

    def test_wrap_long_lines_filter_outputs_br(self):
        rendered = wrap_long_lines("abcdef", width=3)

        self.assertIn("<br>", rendered)
        self.assertEqual(rendered, wrap_text_html("abcdef", width=3))

    def test_wrap_text_html_escapes_content(self):
        html = wrap_text_html("<script>", width=2)

        self.assertIn("&lt;script&gt;", html.replace("<br>", ""))

    def test_wrap_code_snippet_breaks_long_lines(self):
        long_line = "x" * 160
        wrapped = wrap_code_snippet(long_line, width=150)

        self.assertIn("\n", wrapped)
        head, tail = wrapped.split("\n", 1)
        self.assertEqual(len(head), 150)
        self.assertEqual(tail, "x" * 10)

    def test_wrap_code_snippet_preserves_short_lines(self):
        snippet = "    print('hello world')"
        wrapped = wrap_code_snippet(snippet, width=150)

        self.assertEqual(wrapped, snippet)

    def test_wrap_code_snippet_uses_space_boundary(self):
        snippet = "    foo bar baz qux quux corge grault"
        wrapped = wrap_code_snippet(snippet, width=20)

        lines = wrapped.split("\n")
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(line.startswith("    ") for line in lines if line))
        self.assertNotIn("\n", lines[0])


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

    def test_generate_image_places_question_first(self):
        question = Question.objects.create(
            question="What happens?",
            code_snippet="print('hello')",
            answers=["A", "B"],
            correct_answer_index=0,
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
        self.assertIn("What happens?", texts)
        self.assertIn("print('hello')", texts)
        self.assertLess(texts.index("What happens?"), texts.index("print('hello')"))

    def test_generate_image_wraps_long_lines(self):
        long_question = "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua."
        question = Question.objects.create(
            question=long_question,
            answers=["Option"],
            correct_answer_index=0,
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

        with self.settings(MEDIA_ROOT="ignored", QUIZ_IMAGE_WRAP_WIDTH=20), \
            patch("quiz.models.Path.mkdir", return_value=None), \
            patch("PIL.Image.Image.save", return_value=None), \
            patch("quiz.models.ImageDraw.Draw", side_effect=fake_draw):
            question.generate_image()

        texts = [text for draw in draw_calls for (_, text, _) in draw.calls if text]
        question_lines = [text for text in texts if not text.startswith("Source:")]

        self.assertGreater(len(question_lines), 1)
        self.assertTrue(all(len(line) <= 20 for line in question_lines))

    def test_generate_image_wraps_code_snippet_lines(self):
        snippet = "print('hello world') " * 10
        question = Question.objects.create(
            question="Explain",
            code_snippet=snippet,
            answers=["A"],
            correct_answer_index=0,
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

        with self.settings(MEDIA_ROOT="ignored", QUIZ_IMAGE_WRAP_WIDTH=60), \
            patch("quiz.models.Path.mkdir", return_value=None), \
            patch("PIL.Image.Image.save", return_value=None), \
            patch("quiz.models.ImageDraw.Draw", side_effect=fake_draw):
            question.generate_image()

        texts = [text for draw in draw_calls for (_, text, _) in draw.calls if text]
        self.assertIn("-------------", texts)
        divider_index = texts.index("-------------")
        snippet_segments = []
        for text in texts[divider_index + 1 :]:
            if text.startswith("Source:"):
                break
            snippet_segments.append(text)

        self.assertGreater(len(snippet_segments), 1)
        self.assertTrue(all(len(segment) <= 60 for segment in snippet_segments))


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

        self.question.code_snippet = "print('hello world')\nreturn 42"
        self.question.save(update_fields=["code_snippet"])

        rows, score = QuizSessionView._build_results(self.quiz)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["status"], "correct")
        self.assertEqual(row["selected_answer"], "True")
        self.assertEqual(row["correct_answer"], "True")
        self.assertEqual(row["answers"], ["False", "True"])
        self.assertEqual(len(row["answers_display"]), 2)
        self.assertEqual(row["time_spent"], 8.2)
        self.assertEqual(row["weight"], 2.5)
        self.assertIn("quiz_question_id", row)
        self.assertFalse(row["has_feedback"])
        self.assertEqual(row["feedback_comment"], "")
        self.assertTrue(row["question_html"])  # wrapped for display
        self.assertIn("code_snippet_wrapped", row)
        self.assertEqual(row["code_snippet_wrapped"], "print('hello world')\nreturn 42")
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

    def test_build_results_skips_disabled_questions(self):
        disabled_question = Question.objects.create(
            question="Skip me",
            answers=["No", "Yes"],
            correct_answer_index=1,
        )
        QuizQuestion.objects.create(
            quiz=self.quiz,
            question=disabled_question,
            order=2,
            is_disabled=True,
        )
        Attempt.objects.create(
            quiz=self.quiz,
            question=disabled_question,
            selected_answer_index=1,
        )

        rows, score = QuizSessionView._build_results(self.quiz)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["question"], self.question)
        self.assertEqual(score["total"], 1)
        self.assertEqual(score["attempted"], 0)

    def test_submit_feedback_saves_comment_and_surfaces_in_results(self):
        Attempt.objects.create(
            quiz=self.quiz,
            question=self.question,
            selected_answer_index=1,
        )
        self.quiz.mark_completed()
        quiz_question = self.quiz.quiz_questions.first()

        url = reverse("quiz:feedback", args=[self.quiz.token, quiz_question.pk])
        response = self.client.post(url, {"comment": "Please clarify"}, follow=True)

        self.assertEqual(response.status_code, 200)
        quiz_question.refresh_from_db()
        self.assertFalse(quiz_question.is_disabled)
        self.assertEqual(quiz_question.disabled_comment, "Please clarify")

        rows = response.context["rows"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row["has_feedback"])
        self.assertEqual(row["feedback_comment"], "Please clarify")
        self.assertEqual(response.context["feedback_question_id"], str(quiz_question.pk))

    def test_submit_feedback_ajax_returns_payload(self):
        Attempt.objects.create(
            quiz=self.quiz,
            question=self.question,
            selected_answer_index=1,
        )
        self.quiz.mark_completed()
        quiz_question = self.quiz.quiz_questions.first()

        long_comment = "A" * (QuizQuestionFeedbackView.max_comment_length + 10)
        url = reverse("quiz:feedback", args=[self.quiz.token, quiz_question.pk])
        response = self.client.post(
            url,
            {"comment": long_comment},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["has_feedback"])
        self.assertEqual(payload["quiz_question_id"], quiz_question.pk)
        self.assertEqual(len(payload["comment"]), QuizQuestionFeedbackView.max_comment_length)
        self.assertTrue(payload["was_trimmed"])


class TestAccessControlTests(TestCase):
    def setUp(self):
        self.question = Question.objects.create(
            question="Gatekeeper",
            answers=["No", "Yes"],
            correct_answer_index=1,
        )
        self.quiz = QuizLink.objects.create(title="Restricted quiz")
        QuizQuestion.objects.create(quiz=self.quiz, question=self.question, order=1)
        self.test = Test.objects.create(title="Midterm", duration=timedelta(minutes=5))
        self.quiz.test = self.test
        self.quiz.save(update_fields=["test"])

    def test_quiz_unavailable_before_test_starts(self):
        response = self.client.get(reverse("quiz:session", args=[self.quiz.token]))

        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "quiz/test_unavailable.html")
        self.assertTrue(response.context["is_pending"])
        self.assertFalse(response.context["is_finished"])

    def test_quiz_available_during_active_test(self):
        self.test.start()

        response = self.client.get(reverse("quiz:session", args=[self.quiz.token]))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "quiz/welcome.html")

    def test_quiz_unavailable_after_test_finishes(self):
        self.test.start()
        self.test.started_at = timezone.now() - timedelta(minutes=10)
        self.test.finished_at = timezone.now() - timedelta(minutes=5)
        self.test.state = TestState.ACTIVE
        self.test.save(update_fields=["started_at", "finished_at", "state"])

        response = self.client.get(reverse("quiz:session", args=[self.quiz.token]))

        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "quiz/test_unavailable.html")
        self.assertTrue(response.context["is_finished"])


class QuizTimeoutConfigTests(TestCase):
    def setUp(self):
        self.question = Question.objects.create(
            question="Timed question",
            answers=["A", "B"],
            correct_answer_index=0,
        )
        self.quiz = QuizLink.objects.create(title="Timed quiz")
        QuizQuestion.objects.create(quiz=self.quiz, question=self.question, order=1)
        self.test = Test.objects.create(title="Timed test", duration=timedelta(minutes=5))
        self.quiz.test = self.test
        self.quiz.save(update_fields=["test"])

    def _start_session(self):
        self.test.start()
        session = self.client.session
        session[QuizSessionView._start_flag_key(self.quiz.pk)] = True
        session.save()

    @override_settings(QUIZ_QUESTION_TIMEOUT=45)
    def test_test_specific_timeout_overrides_global(self):
        self.test.question_timeout = 12
        self.test.save(update_fields=["question_timeout"])

        self._start_session()
        response = self.client.get(reverse("quiz:session", args=[self.quiz.token]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["timeout_seconds"], 12)

    @override_settings(QUIZ_QUESTION_TIMEOUT=33)
    def test_global_timeout_used_when_test_has_no_override(self):
        self._start_session()

        response = self.client.get(reverse("quiz:session", args=[self.quiz.token]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["timeout_seconds"], 33)

    @override_settings(QUIZ_QUESTION_TIMEOUT=27)
    def test_quiz_without_test_uses_global_timeout(self):
        standalone_quiz = QuizLink.objects.create(title="Standalone quiz")
        QuizQuestion.objects.create(quiz=standalone_quiz, question=self.question, order=1)
        session = self.client.session
        session[QuizSessionView._start_flag_key(standalone_quiz.pk)] = True
        session.save()

        response = self.client.get(reverse("quiz:session", args=[standalone_quiz.token]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["timeout_seconds"], 27)


class TestAdminStartTests(TestCase):
    def setUp(self):
        self.admin_site = AdminSite()
        self.admin = TestAdmin(Test, self.admin_site)
        self.factory = RequestFactory()
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password123",
        )
        self.question = Question.objects.create(
            question="Access",
            answers=["No", "Yes"],
            correct_answer_index=1,
        )
        self.quiz = QuizLink.objects.create(title="Locked quiz")
        QuizQuestion.objects.create(quiz=self.quiz, question=self.question, order=1)
        self.test = Test.objects.create(title="Exam", duration=timedelta(minutes=5))
        self.student = Student.objects.create(
            name="Alice Example",
            email="alice@example.com",
        )
        self.quiz.test = self.test
        self.quiz.student = self.student
        self.quiz.save(update_fields=["test", "student"])

    def test_start_button_activates_test(self):
        url = f"/admin/quiz/test/{self.test.pk}/change/"
        request = self.factory.post(url, data={"_start_test": "1"})
        request.user = self.superuser
        request.session = self.client.session
        setattr(request, "_messages", FallbackStorage(request))

        response = self.admin.changeform_view(request, str(self.test.pk))

        self.assertEqual(response.status_code, 302)
        self.test.refresh_from_db()
        self.assertEqual(self.test.state, TestState.ACTIVE)

    @override_settings(QUIZ_QUESTION_TIMEOUT=99)
    def test_add_form_prefills_question_timeout(self):
        request = self.factory.get("/admin/quiz/test/add/")
        request.user = self.superuser
        initial = self.admin.get_changeform_initial_data(request)

        self.assertEqual(initial["question_timeout"], 99)

    def test_reset_button_returns_test_to_draft_and_clears_quizzes(self):
        self.test.start()
        quiz_question = self.quiz.quiz_questions.first()
        Attempt.objects.create(
            quiz=self.quiz,
            question=quiz_question.question,
            selected_answer_index=1,
        )
        self.quiz.completed_at = timezone.now()
        self.quiz.save(update_fields=["completed_at"])

        url = f"/admin/quiz/test/{self.test.pk}/change/"
        request = self.factory.post(url, data={"_reset_test": "1"})
        request.user = self.superuser
        request.session = self.client.session
        setattr(request, "_messages", FallbackStorage(request))

        response = self.admin.changeform_view(request, str(self.test.pk))

        self.assertEqual(response.status_code, 302)
        self.test.refresh_from_db()
        self.assertEqual(self.test.state, TestState.DRAFT)
        self.assertIsNone(self.test.started_at)
        self.assertIsNone(self.test.finished_at)
        self.quiz.refresh_from_db()
        self.assertIsNone(self.quiz.completed_at)
        self.assertFalse(self.quiz.attempts.exists())

    def test_export_links_returns_csv(self):
        url = f"/admin/quiz/test/{self.test.pk}/change/"
        request = self.factory.post(url, data={"_export_links": "1"})
        request.user = self.superuser
        request.session = self.client.session
        setattr(request, "_messages", FallbackStorage(request))

        response = self.admin.changeform_view(request, str(self.test.pk))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("links.csv", response["Content-Disposition"])
        content = response.content.decode("utf-8")
        header = content.splitlines()[0]
        self.assertEqual(header, "name,email,quiz_url")
        self.assertIn(self.student.name, content)
        self.assertIn(self.student.email, content)
        expected_url = f"http://testserver{reverse('quiz:session', kwargs={'token': self.quiz.token})}"
        self.assertIn(expected_url, content)

class TestAdminImportQuestionsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username="importer",
            email="importer@example.com",
            password="password123",
        )
        self.client.force_login(self.superuser)
        self.test = Test.objects.create(title="Exam", duration=timedelta(minutes=15))
        self.student = Student.objects.create(name="Ivan Popov", email="popov@example.com")

    def _build_upload(self, filename):
        payload = json.dumps(
            [
                {
                    "question": "What is 2 + 2?",
                    "answers": ["3", "4"],
                    "correct_answer_index": 1,
                }
            ]
        )
        return SimpleUploadedFile(
            filename,
            payload.encode("utf-8"),
            content_type="application/json",
        )

    def test_import_assigns_quiz_to_student(self):
        upload = self._build_upload("Popov_questions.json")
        url = reverse("admin:quiz_test_change", args=[self.test.pk])

        response = self.client.post(
            url,
            {"_import_questions": "1", "json_files": upload},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        quiz = QuizLink.objects.get(test=self.test)
        self.assertEqual(quiz.student, self.student)
        self.assertEqual(quiz.quiz_questions.count(), 1)
        self.assertEqual(quiz.title, "Popov_questions")
        self.assertEqual(quiz.original_filename, "Popov_questions.json")

    def test_import_uses_shortened_title_and_preserves_original_filename(self):
        long_filename = "Максимов Тимофей Степанович_4257171_assignsubmission_file_Maksimov_T_EX3_Multilabel_questions.json"
        upload = self._build_upload(long_filename)
        url = reverse("admin:quiz_test_change", args=[self.test.pk])

        response = self.client.post(
            url,
            {"_import_questions": "1", "json_files": upload},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        quiz = QuizLink.objects.get(test=self.test)
        self.assertEqual(quiz.student, self.student)
        self.assertEqual(quiz.original_filename, long_filename)
        self.assertEqual(quiz.title, "Максимов Тимофей Сте")

    def test_import_skips_unknown_student(self):
        upload = self._build_upload("unknown.json")
        url = reverse("admin:quiz_test_change", args=[self.test.pk])

        response = self.client.post(
            url,
            {"_import_questions": "1", "json_files": upload},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(QuizLink.objects.filter(test=self.test).exists())

    def test_import_skips_existing_student_quiz(self):
        question = Question.objects.create(
            question="Existing?",
            answers=["No", "Yes"],
            correct_answer_index=1,
        )
        quiz = QuizLink.objects.create(title="Existing", student=self.student, test=self.test)
        QuizQuestion.objects.create(quiz=quiz, question=question, order=1)

        upload = self._build_upload("Popov_questions.json")
        url = reverse("admin:quiz_test_change", args=[self.test.pk])

        response = self.client.post(
            url,
            {"_import_questions": "1", "json_files": upload},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            QuizLink.objects.filter(test=self.test, student=self.student).count(),
            1,
        )


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

    def test_score_display_ignores_disabled_questions(self):
        question_two = Question.objects.create(
            question="Second?",
            answers=["No", "Yes"],
            correct_answer_index=1,
        )
        quiz_question_two = QuizQuestion.objects.create(
            quiz=self.quiz,
            question=question_two,
            order=2,
            is_disabled=True,
        )

        Attempt.objects.create(
            quiz=self.quiz,
            question=self.quiz.quiz_questions.first().question,
            selected_answer_index=1,
        )
        Attempt.objects.create(
            quiz=self.quiz,
            question=quiz_question_two.question,
            selected_answer_index=1,
        )

        request = self.factory.get("/admin/quiz/quizlink/")
        request.user = self.superuser
        annotated_quiz = self.admin.get_queryset(request).get(pk=self.quiz.pk)
        self.assertEqual(self.admin.score_display(annotated_quiz), "1/1 (100%)")

    def test_unhidden_question_count_uses_annotation(self):
        question_two = Question.objects.create(
            question="Second?",
            answers=["No", "Yes"],
            correct_answer_index=0,
        )
        QuizQuestion.objects.create(
            quiz=self.quiz,
            question=question_two,
            order=2,
            is_disabled=True,
        )

        request = self.factory.get("/admin/quiz/quizlink/")
        request.user = self.superuser
        annotated_quiz = self.admin.get_queryset(request).get(pk=self.quiz.pk)

        self.assertEqual(self.admin.unhidden_question_count(annotated_quiz), 1)

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

    def test_results_view_counts_flagged_feedback(self):
        quiz_question = self.quiz.quiz_questions.first()
        quiz_question.disabled_comment = "Confusing wording"
        quiz_question.save(update_fields=["disabled_comment"])

        request = self.factory.get(f"/admin/quiz/quizlink/{self.quiz.pk}/results/")
        request.user = self.superuser
        response = self.admin.results_view(request, self.quiz.pk)

        self.assertEqual(response.status_code, 200)
        response.render()
        self.assertEqual(response.context_data["feedback_count"], 1)
        rows = response.context_data["rows"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row["has_feedback"])
        self.assertEqual(row["disabled_comment"], "Confusing wording")

    @override_settings(QUIZ_MAX_QUESTIONS=1)
    def test_results_view_marks_excluded_questions(self):
        first_quiz_question = self.quiz.quiz_questions.first()
        second_question = Question.objects.create(
            question="Extra?",
            answers=["No", "Yes"],
            correct_answer_index=1,
        )
        extra_quiz_question = QuizQuestion.objects.create(
            quiz=self.quiz,
            question=second_question,
            order=2,
        )

        Attempt.objects.create(
            quiz=self.quiz,
            question=first_quiz_question.question,
            selected_answer_index=1,
        )

        self.quiz.ensure_included_question_ids(force=True)

        # Swap the order values to ensure the second question appears first.
        request = self.factory.get(f"/admin/quiz/quizlink/{self.quiz.pk}/results/")
        request.user = self.superuser
        response = self.admin.results_view(request, self.quiz.pk)

        self.assertEqual(response.status_code, 200)
        response.render()
        rows = response.context_data["rows"]
        self.assertEqual(len(rows), 2)
        excluded_row = next(row for row in rows if row["quiz_question_id"] == extra_quiz_question.id)
        included_row = next(row for row in rows if row["quiz_question_id"] == first_quiz_question.id)

        self.assertFalse(included_row.get("is_excluded"))
        self.assertTrue(excluded_row.get("is_excluded"))
        self.assertEqual(excluded_row["status"], "excluded")

        score = response.context_data["score"]
        self.assertEqual(score["total"], 1)
        self.assertEqual(score["correct"], 1)
        self.assertEqual(score["attempted"], 1)
        self.assertEqual(response.context_data["excluded_count"], 1)

    def test_results_view_does_not_persist_included_ids(self):
        request = self.factory.get(f"/admin/quiz/quizlink/{self.quiz.pk}/results/")
        request.user = self.superuser

        response = self.admin.results_view(request, self.quiz.pk)
        self.assertEqual(response.status_code, 200)

        self.quiz.refresh_from_db()
        self.assertFalse(self.quiz.included_question_ids)

    def test_download_hidden_questions_action_returns_file(self):
        quiz_question = self.quiz.quiz_questions.first()
        quiz_question.is_disabled = True
        quiz_question.disabled_comment = "Broken"
        quiz_question.save(update_fields=["is_disabled", "disabled_comment"])

        request = self.factory.post("/admin/quiz/quizlink/")
        request.user = self.superuser

        queryset = QuizLink.objects.filter(pk=self.quiz.pk)
        response = self.admin.download_hidden_questions_action(request, queryset)

        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode("utf-8"))
        self.assertIsInstance(payload, list)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["questions"][0]["disabled_comment"], "Broken")

    def test_download_hidden_questions_action_shows_message_when_empty(self):
        request = self.factory.post("/admin/quiz/quizlink/")
        request.user = self.superuser
        request.session = self.client.session
        setattr(request, "_messages", FallbackStorage(request))

        queryset = QuizLink.objects.filter(pk=self.quiz.pk)
        response = self.admin.download_hidden_questions_action(request, queryset)

        self.assertIsNone(response)

    def test_export_hidden_questions_downloads_payload(self):
        quiz_question = self.quiz.quiz_questions.first()
        quiz_question.is_disabled = True
        quiz_question.disabled_comment = "Needs review"
        quiz_question.save(update_fields=["is_disabled", "disabled_comment"])

        request = self.factory.get(f"/admin/quiz/quizlink/{self.quiz.pk}/results/export-hidden/")
        request.user = self.superuser
        response = self.admin.export_hidden_questions_view(request, self.quiz.pk)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        content = response.content.decode("utf-8")
        payload = json.loads(content)
        self.assertIn("questions", payload)
        self.assertEqual(len(payload["questions"]), 1)
        exported_question = payload["questions"][0]
        self.assertEqual(exported_question["question"], quiz_question.question.question)
        self.assertEqual(exported_question["disabled_comment"], "Needs review")

    def test_export_hidden_questions_redirects_when_none(self):
        request = self.factory.get(f"/admin/quiz/quizlink/{self.quiz.pk}/results/export-hidden/")
        request.user = self.superuser
        request.session = self.client.session
        setattr(request, "_messages", FallbackStorage(request))

        response = self.admin.export_hidden_questions_view(request, self.quiz.pk)

        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/admin/quiz/quizlink/{self.quiz.pk}/results/", response.url)

    def test_disable_and_enable_question_flow(self):
        quiz_question = self.quiz.quiz_questions.first()

        disable_request = self.factory.post("/disable/", data={"comment": "Buggy question"})
        disable_request.user = self.superuser
        disable_request.session = self.client.session
        setattr(disable_request, "_messages", FallbackStorage(disable_request))

        response = self.admin.disable_question_view(disable_request, self.quiz.pk, quiz_question.pk)
        self.assertEqual(response.status_code, 302)
        quiz_question.refresh_from_db()
        self.assertTrue(quiz_question.is_disabled)
        self.assertEqual(quiz_question.disabled_comment, "Buggy question")

        enable_request = self.factory.post("/enable/")
        enable_request.user = self.superuser
        enable_request.session = self.client.session
        setattr(enable_request, "_messages", FallbackStorage(enable_request))

        response = self.admin.enable_question_view(enable_request, self.quiz.pk, quiz_question.pk)
        self.assertEqual(response.status_code, 302)
        quiz_question.refresh_from_db()
        self.assertFalse(quiz_question.is_disabled)
        self.assertEqual(quiz_question.disabled_comment, "")

    def test_results_view_marks_disabled_questions(self):
        quiz_question = self.quiz.quiz_questions.first()
        quiz_question.is_disabled = True
        quiz_question.disabled_comment = "Broken"
        quiz_question.save(update_fields=["is_disabled", "disabled_comment"])

        Attempt.objects.create(
            quiz=self.quiz,
            question=quiz_question.question,
            selected_answer_index=1,
        )

        request = self.factory.get(f"/admin/quiz/quizlink/{self.quiz.pk}/results/")
        request.user = self.superuser
        response = self.admin.results_view(request, self.quiz.pk)
        self.assertEqual(response.status_code, 200)
        response.render()
        rows = response.context_data["rows"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row["is_disabled"])
        self.assertEqual(row["disabled_comment"], "Broken")
        score = response.context_data["score"]
        self.assertEqual(score["total"], 0)
        self.assertEqual(score["attempted"], 0)

    def test_disable_question_requires_comment(self):
        quiz_question = self.quiz.quiz_questions.first()
        request = self.factory.post("/disable/", data={"comment": ""})
        request.user = self.superuser
        request.session = self.client.session
        setattr(request, "_messages", FallbackStorage(request))

        response = self.admin.disable_question_view(request, self.quiz.pk, quiz_question.pk)

        self.assertEqual(response.status_code, 302)
        quiz_question.refresh_from_db()
        self.assertFalse(quiz_question.is_disabled)


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


class QuizQuestionLimitTests(TestCase):
    def setUp(self):
        self.quiz = QuizLink.objects.create(title="Limited Quiz")
        self.questions = []
        for order in range(1, 4):
            question = Question.objects.create(
                question=f"Question {order}",
                answers=["A", "B"],
                correct_answer_index=0,
            )
            QuizQuestion.objects.create(quiz=self.quiz, question=question, order=order)
            self.questions.append(question)

    @override_settings(QUIZ_MAX_QUESTIONS=2)
    def test_ordered_questions_respect_limit(self):
        quiz_questions = list(self.quiz.ordered_quiz_questions())
        self.assertEqual(len(quiz_questions), 2)
        self.assertEqual([qq.order for qq in quiz_questions], [1, 2])
        self.assertEqual(self.quiz.total_questions(), 2)

    @override_settings(QUIZ_MAX_QUESTIONS=2)
    def test_results_summary_respects_limit(self):
        Attempt.objects.create(
            quiz=self.quiz,
            question=self.questions[0],
            selected_answer_index=0,
        )

        rows, score = QuizSessionView._build_results(self.quiz)
        self.assertEqual(len(rows), 2)
        self.assertEqual(score["total"], 2)
        self.assertEqual(score["attempted"], 1)


class QuizIncludedQuestionIdsTests(TestCase):
    def setUp(self):
        self.quiz = QuizLink.objects.create(title="Tracking Quiz")
        for order in range(1, 4):
            question = Question.objects.create(
                question=f"Question {order}",
                answers=["A", "B"],
                correct_answer_index=0,
            )
            QuizQuestion.objects.create(quiz=self.quiz, question=question, order=order)

    @override_settings(QUIZ_MAX_QUESTIONS=2)
    def test_ensure_without_persist_does_not_save(self):
        snapshot = self.quiz.ensure_included_question_ids()
        self.assertEqual(len(snapshot), 2)
        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.included_question_ids, [])

    @override_settings(QUIZ_MAX_QUESTIONS=2)
    def test_start_persists_included_ids(self):
        url = reverse("quiz:session", args=[self.quiz.token])
        response = self.client.post(url, {"start_quiz": "1"})
        self.assertEqual(response.status_code, 302)
        self.quiz.refresh_from_db()
        self.assertEqual(len(self.quiz.included_question_ids), 2)

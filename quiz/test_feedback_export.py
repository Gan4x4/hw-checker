import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from .management.commands.import_questions import QuizImportError, import_quiz_from_json
from .models import Feedback, Question, QuizLink, QuizQuestion, Test, TestState
from .views import QuizSessionView


class FeedbackExportTests(TestCase):
    def setUp(self):
        self.test = Test.objects.create(duration=timedelta(minutes=30), state=TestState.ACTIVE)
        self.quiz, _, _ = import_quiz_from_json(json.dumps({
            "provenance": {"generation_run_id": "run-1", "prompt_hashes": {"questioner": "abc"}},
            "questions": [{"question": "Почему?", "code_snippet": "print(1)", "answers": ["Да", "Нет"],
                           "correct_answer_index": 0, "explanation": "Объяснение", "original_question_ref": "artifact/q1"}],
        }), default_name="Private student name")
        self.quiz.test = self.test
        self.quiz.save()
        self.row = self.quiz.quiz_questions.get()
        self.url = reverse("quiz:test_feedback_json", args=[self.test.results_token])
        self.submit = reverse("quiz:feedback", args=[self.quiz.token, self.row.pk])

    def test_snapshots_multiple_comments_unfinished_and_general_feedback(self):
        session = self.client.session
        session[QuizSessionView._start_flag_key(self.quiz.pk)] = True
        session.save()
        with patch.object(Question, "generate_image", return_value="questions/test.png"):
            self.assertEqual(self.client.get(reverse("quiz:session", args=[self.quiz.token])).status_code, 200)
        self.row.question.question = "Edited later"
        self.row.question.answers = ["Changed", "Options"]
        self.row.question.save()
        comments = ["Неясно\nВторая строка 🐈", "Another comment"]
        for comment in comments:
            self.assertEqual(self.client.post(self.submit, {"comment": comment}).status_code, 302)
        self.client.post(self.submit, {"comment": ""})
        self.client.post(reverse("quiz:general_feedback", args=[self.quiz.token]), {"comment": "Общий отзыв"})
        self.row.is_disabled = True
        self.row.disabled_comment = "Instructor reason"
        self.row.save(update_fields=["is_disabled", "disabled_comment"])
        response = self.client.get(self.url)
        payload = response.json()
        self.assertEqual(payload["test_id"], self.test.pk)
        self.assertEqual([entry["text"] for entry in payload["feedback"]], comments)
        self.assertTrue(all(entry["submitted_at"] for entry in payload["feedback"]))
        self.assertEqual(len(payload["questions"]), 1)
        question = payload["questions"][payload["feedback"][0]["question_ref"]]
        self.assertEqual(question["question_id"], self.row.question_id)
        self.assertEqual(question["question"], "Почему?")
        self.assertEqual(question["answers"], ["Да", "Нет"])
        self.assertEqual(question["code_snippet"], "print(1)")
        self.assertEqual(question["correct_answer"], "Да")
        self.assertEqual(question["explanation"], "Объяснение")
        self.assertEqual(question["snapshot_source"], "shown")
        self.assertEqual(question["provenance"]["generation_run_id"], "run-1")
        self.assertEqual(question["provenance"]["original_question_ref"], "artifact/q1")
        self.assertIsNone(question["provenance"]["analyzer_model_version"])
        self.assertEqual(payload["general_feedback"][0]["text"], "Общий отзыв")
        self.assertIsNone(payload["general_feedback"][0]["question_ref"])
        self.assertNotIn("Private student name", response.content.decode())
        self.assertNotIn(str(self.quiz.token), response.content.decode())
        self.quiz.refresh_from_db()
        self.assertIsNone(self.quiz.completed_at)

    def test_empty_scoping_and_access_controls(self):
        other = QuizLink.objects.create()
        Feedback.objects.create(quiz=other, comment="Other quiz")
        response = self.client.get(self.url)
        self.assertEqual(response.json()["feedback"], [])
        self.assertEqual(response.json()["general_feedback"], [])
        self.assertEqual(response.json()["questions"], {})
        self.assertIn("noindex", response["X-Robots-Tag"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.client.post(self.url).status_code, 405)
        self.assertEqual(Client(enforce_csrf_checks=True).post(self.submit, {"comment": "No CSRF"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("quiz:test_feedback_json", args=[uuid.uuid4()])).status_code, 404)
        unrelated = QuizQuestion.objects.create(quiz=other, question=self.row.question, order=1)
        bad = reverse("quiz:feedback", args=[self.quiz.token, unrelated.pk])
        self.assertEqual(self.client.post(bad, {"comment": "Wrong quiz"}).status_code, 400)
        self.assertEqual(self.client.get(reverse("quiz:test_results_csv", args=[self.test.results_token])).status_code, 200)

    def test_invalid_provenance_is_rejected_before_import(self):
        with self.assertRaises(QuizImportError):
            import_quiz_from_json(json.dumps([{"question": "Q", "answers": ["A"], "correct_answer_index": 0, "provenance": []}]), default_name="Invalid")
        self.assertEqual(QuizLink.objects.count(), 1)


class FeedbackMigrationTests(TransactionTestCase):
    def test_legacy_comments_have_unknown_time_and_historical_snapshot(self):
        before = [("quiz", "0012_test_results_token")]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        try:
            apps = executor.loader.project_state(before).apps
            quiz = apps.get_model("quiz", "QuizLink").objects.create()
            question = apps.get_model("quiz", "Question").objects.create(question="Старый?", answers=["Да"], correct_answer_index=0)
            rows = apps.get_model("quiz", "QuizQuestion").objects
            rows.create(quiz=quiz, question=question, order=1, disabled_comment="Старый отзыв")
            rows.create(quiz=quiz, question=question, order=2, disabled_comment="Instructor note", is_disabled=True)
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
            entry = Feedback.objects.get()
            self.assertEqual(entry.comment, "Старый отзыв")
            self.assertIsNone(entry.submitted_at)
            self.assertEqual(entry.question_snapshot["snapshot_source"], "historical_current")
            self.assertEqual(entry.question_snapshot["provenance"], {})
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())

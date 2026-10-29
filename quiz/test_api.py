import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import APIClient, Feedback, QuizLink, Student, Test


class ClientAPITests(TestCase):
    def setUp(self):
        self.old = Test.objects.create(title="Old exam", academic_year="2025_26", duration=timedelta(minutes=5))
        self.new = Test.objects.create(title="New exam", duration=timedelta(minutes=5))
        self.api_client = APIClient.objects.create(name="Analyzer")
        self.client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {self.api_client.token}"
        quiz = QuizLink.objects.create(test=self.old, academic_year="2025_26")
        Feedback.objects.create(quiz=quiz, comment="Отзыв\nUnicode 🐈")

    def test_all_tests_and_existing_export_formats(self):
        response = self.client.get(reverse("quiz:api_tests"))
        self.assertEqual(response.status_code, 200)
        tests = response.json()["tests"]
        self.assertEqual([test["id"] for test in tests], [self.old.pk, self.new.pk])
        self.assertEqual(tests[0]["academic_year"], "2025_26")
        self.assertNotIn(str(self.old.results_token), response.content.decode())
        self.assertNotIn(str(self.api_client.token), response.content.decode())
        self.assertIn("no-store", response["Cache-Control"])
        feedback = self.client.get(tests[0]["feedback_url"])
        legacy = self.client.get(reverse("quiz:test_feedback_json", args=[self.old.results_token]))
        self.assertEqual(feedback.json(), legacy.json())
        csv = self.client.get(tests[0]["results_url"])
        self.assertEqual(csv.content, self.client.get(reverse("quiz:test_results_csv", args=[self.old.results_token])).content)
        for route in ("api_test_results", "api_test_feedback"):
            self.assertEqual(self.client.get(reverse("quiz:" + route, args=[self.new.pk])).status_code, 200)
        batch = self.client.get(reverse("quiz:api_feedback")).json()
        self.assertEqual(batch["tests"][0], legacy.json())
        self.assertEqual([test["test_id"] for test in batch["tests"]], [self.old.pk, self.new.pk])

    def test_feedback_student_identity(self):
        student = Student.objects.create(name="Иван Петров", email="ivan@example.com", academic_year="2025_26")
        quiz = self.old.quizzes.get()
        Feedback.objects.create(quiz=quiz, comment="Question comment", question_snapshot={"question_id": 1})
        for linked_student in (student, None):
            quiz.student = linked_student
            quiz.save(update_fields=["student"])
            expected = (student.pk, student.name) if linked_student else (None, None)
            for route, args in (("api_test_feedback", [self.old.pk]), ("api_feedback", []),
                                ("test_feedback_json", [self.old.results_token])):
                with self.subTest(student=linked_student, route=route):
                    payload = self.client.get(reverse("quiz:" + route, args=args)).json()
                    if route == "api_feedback":
                        payload = payload["tests"][0]
                    for kind in ("feedback", "general_feedback"):
                        self.assertEqual(len(payload[kind]), 1)
                        entry = payload[kind][0]
                        self.assertEqual((entry["student_id"], entry["student_name"]), expected)

    def test_all_tests_future_tests_selection_and_read_only_state(self):
        future = Test.objects.create(duration=timedelta(minutes=5), state="active", finished_at=timezone.now() - timedelta(seconds=1))
        response = self.client.get(reverse("quiz:api_tests"))
        self.assertEqual([row["id"] for row in response.json()["tests"]], [self.old.pk, self.new.pk, future.pk])
        self.assertEqual(response.json()["tests"][-1]["state"], "finished")
        future.refresh_from_db()
        self.assertEqual(future.state, "active")
        for route in ("api_tests", "api_feedback"):
            response = self.client.get(reverse("quiz:" + route), {"test_ids": f"{self.new.pk},{self.new.pk}"})
            self.assertEqual(len(response.json()["tests"]), 1)
            for bad in ("", "oops", "-1", "1,", "9" * 30):
                self.assertEqual(self.client.get(reverse("quiz:" + route), {"test_ids": bad}).status_code, 400)
        for route, args in (("api_tests", []), ("api_feedback", []), ("api_test_feedback", [self.old.pk]), ("api_test_results", [self.old.pk])):
            self.assertEqual(self.client.post(reverse("quiz:" + route, args=args)).status_code, 405)

    def test_missing_tests_empty_database_and_revocation(self):
        url = reverse("quiz:api_feedback")
        self.assertEqual(self.client.get(url, {"test_ids": f"{self.old.pk},{self.new.pk + 100}"}).status_code, 404)
        Test.objects.all().delete()
        self.assertEqual(self.client.get(url).json()["tests"], [])
        self.assertEqual(self.client.get(reverse("quiz:api_tests")).json()["tests"], [])
        self.api_client.is_active = False
        self.api_client.save()
        self.assertEqual(self.client.get(url).status_code, 401)
        self.api_client.is_active = True
        self.api_client.token = uuid.uuid4()
        self.api_client.save()
        self.assertEqual(self.client.get(url).status_code, 401)
        for header in ("", "Bearer nonsense", "Basic abc", f"Bearer {self.old.results_token}"):
            response = self.client.get(url, HTTP_AUTHORIZATION=header)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response["WWW-Authenticate"], "Bearer")
            self.assertIn("no-store", response["Cache-Control"])
        response = self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {self.api_client.token}")
        self.assertEqual(response.status_code, 200)

    def test_client_management_requires_superuser_and_rotates_tokens(self):
        staff = get_user_model().objects.create_user("staff", is_staff=True)
        self.client.force_login(staff)
        change = reverse("admin:quiz_apiclient_change", args=[self.api_client.pk])
        self.assertEqual(self.client.get(change).status_code, 403)
        superuser = get_user_model().objects.create_superuser("root", password="test")
        self.client.force_login(superuser)
        self.assertContains(self.client.get(change), str(self.api_client.token))
        original = self.api_client.token
        self.client.post(reverse("admin:quiz_apiclient_changelist"), {
            "action": "rotate_tokens", "_selected_action": [self.api_client.pk],
        })
        self.api_client.refresh_from_db()
        self.assertNotEqual(self.api_client.token, original)
        self.assertEqual(self.client.get(reverse("quiz:api_tests")).status_code, 401)

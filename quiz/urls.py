from django.urls import path

from . import api, views

app_name = "quiz"

urlpatterns = [
    path("api/tests/", api.tests, name="api_tests"),
    path("api/feedback.json", api.tests, {"export": "feedback"}, name="api_feedback"),
    path("api/tests/<int:test_id>/feedback.json", api.tests, {"export": "feedback"}, name="api_test_feedback"),
    path("api/tests/<int:test_id>/results.csv", api.tests, {"export": "csv"}, name="api_test_results"),
    path("test-results/<uuid:token>/feedback.json", views.test_feedback_json, name="test_feedback_json"),
    path("quiz/<uuid:token>/feedback/", views.QuizQuestionFeedbackView.as_view(), name="general_feedback"),
    path("test-results/<uuid:token>/results.csv", views.test_results_csv, name="test_results_csv"),
    path(
        "quiz/<uuid:token>/feedback/<int:quiz_question_id>/",
        views.QuizQuestionFeedbackView.as_view(),
        name="feedback",
    ),
    path("quiz/<uuid:token>/", views.QuizSessionView.as_view(), name="session"),
    path("", views.HomeView.as_view(), name="home"),
]

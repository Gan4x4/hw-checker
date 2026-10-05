from django.urls import path

from . import views

app_name = "quiz"

urlpatterns = [
    path("test-results/<uuid:token>/results.csv", views.test_results_csv, name="test_results_csv"),
    path(
        "quiz/<uuid:token>/feedback/<int:quiz_question_id>/",
        views.QuizQuestionFeedbackView.as_view(),
        name="feedback",
    ),
    path("quiz/<uuid:token>/", views.QuizSessionView.as_view(), name="session"),
    path("", views.HomeView.as_view(), name="home"),
]

from django.urls import path

from . import views

app_name = "quiz"

urlpatterns = [
    path("quiz/<uuid:token>/", views.QuizSessionView.as_view(), name="session"),
    path("", views.HomeView.as_view(), name="home"),
]

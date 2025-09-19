from __future__ import annotations

from pathlib import Path
from typing import List

from django.conf import settings
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import TemplateView, View

from .models import Attempt, QuizLink


class HomeView(TemplateView):
    template_name = "quiz/home.html"


class QuizSessionView(View):
    template_name = "quiz/question.html"
    timeout_seconds = 30

    def get(self, request, token, *args, **kwargs):
        quiz = self._get_quiz(token)
        start_key = self._start_flag_key(quiz.pk)

        if quiz.completed_at and quiz.attempts.exists():
            rows, score = self._build_results(quiz)
            return render(
                request,
                "quiz/completed.html",
                {"quiz": quiz, "rows": rows, "score": score},
            )

        if not request.session.get(start_key):
            return render(request, "quiz/welcome.html", {"quiz": quiz})

        quiz_questions = list(quiz.ordered_quiz_questions())
        total_questions = len(quiz_questions)
        answered_count = quiz.attempts.count()

        if answered_count >= total_questions:
            self._delete_image(request.session.pop("last_image_path", None))
            quiz.mark_completed()
            request.session.pop(start_key, None)
            rows, score = self._build_results(quiz)
            return render(
                request,
                "quiz/completed.html",
                {"quiz": quiz, "rows": rows, "score": score},
            )

        self._delete_image(request.session.pop("last_image_path", None))
        current_quiz_question = quiz_questions[answered_count]
        question = current_quiz_question.question
        image_path = question.generate_image()
        image_url = f"{settings.MEDIA_URL}{image_path}" if settings.MEDIA_URL else image_path
        request.session["last_image_path"] = image_path

        context = {
            "quiz": quiz,
            "question": question,
            "answers": list(enumerate(question.answers)),
            "image_url": image_url,
            "image_path": image_path,
            "question_number": answered_count + 1,
            "total_questions": total_questions,
            "timeout_seconds": self.timeout_seconds,
        }
        return render(request, self.template_name, context)

    def post(self, request, token, *args, **kwargs):
        quiz = self._get_quiz(token)
        start_key = self._start_flag_key(quiz.pk)

        if "start_quiz" in request.POST:
            request.session[start_key] = True
            request.session.modified = True
            return redirect("quiz:session", token=quiz.token)

        if not request.session.get(start_key):
            return redirect("quiz:session", token=quiz.token)

        quiz_questions: List = list(quiz.ordered_quiz_questions())
        total_questions = len(quiz_questions)
        answered_count = quiz.attempts.count()

        if answered_count >= total_questions:
            quiz.mark_completed()
            request.session.pop(start_key, None)
            rows, score = self._build_results(quiz)
            return render(
                request,
                "quiz/completed.html",
                {"quiz": quiz, "rows": rows, "score": score},
            )

        current_quiz_question = quiz_questions[answered_count]
        question = current_quiz_question.question

        image_path = request.POST.get("image_path", "")
        self._delete_image(image_path)
        request.session.pop("last_image_path", None)

        submitted_question_id = request.POST.get("question_id")
        if str(question.id) != str(submitted_question_id):
            return HttpResponseBadRequest("Question mismatch")

        selected_answer = request.POST.get("selected_answer")
        selected_index = self._coerce_index(selected_answer, len(question.answers))
        time_spent = self._coerce_float(request.POST.get("time_spent"))

        Attempt.objects.create(
            quiz=quiz,
            question=question,
            selected_answer_index=selected_index,
            time_spent=time_spent,
        )

        answered_count += 1
        if answered_count >= total_questions:
            quiz.mark_completed()
            request.session.pop(start_key, None)
            rows, score = self._build_results(quiz)
            return render(
                request,
                "quiz/completed.html",
                {"quiz": quiz, "rows": rows, "score": score},
            )

        return redirect("quiz:session", token=quiz.token)

    @staticmethod
    def _coerce_index(raw_value: str | None, max_length: int) -> int | None:
        if raw_value in (None, ""):
            return None
        try:
            index = int(raw_value)
        except (TypeError, ValueError):
            return None
        if 0 <= index < max_length:
            return index
        return None

    @staticmethod
    def _coerce_float(raw_value: str | None) -> float | None:
        if not raw_value:
            return None
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _delete_image(relative_path: str | None) -> None:
        if not relative_path:
            return
        relative_path = relative_path.lstrip("/")
        media_root = Path(settings.MEDIA_ROOT)
        target_path = media_root / relative_path
        try:
            target_path.resolve().relative_to(media_root.resolve())
        except ValueError:
            return
        if target_path.exists():
            target_path.unlink()

    @staticmethod
    def _get_quiz(token) -> QuizLink:
        return get_object_or_404(QuizLink, token=token)

    @staticmethod
    def _start_flag_key(quiz_id: int) -> str:
        return f"quiz_started_{quiz_id}"

    @staticmethod
    def _build_results(quiz: QuizLink) -> tuple[list[dict], dict]:
        quiz_questions = list(quiz.quiz_questions.select_related("question").order_by("order"))
        attempts = {
            attempt.question_id: attempt
            for attempt in quiz.attempts.select_related("question").order_by("created_at")
        }

        rows: list[dict] = []
        correct = 0
        attempted = 0

        for quiz_question in quiz_questions:
            question = quiz_question.question
            attempt = attempts.get(question.id)
            answers = list(question.answers or [])
            selected_answer = None
            status = "unanswered"
            time_spent = None

            if attempt:
                attempted += 1
                index = attempt.selected_answer_index
                if index is not None and 0 <= index < len(answers):
                    selected_answer = answers[index]
                status = "correct" if attempt.is_correct else "incorrect"
                time_spent = attempt.time_spent
                if attempt.is_correct:
                    correct += 1

            correct_answer = None
            if 0 <= question.correct_answer_index < len(answers):
                correct_answer = answers[question.correct_answer_index]

            rows.append(
                {
                    "order": quiz_question.order,
                    "question": question,
                    "answers": answers,
                    "selected_answer": selected_answer,
                    "correct_answer": correct_answer,
                    "status": status,
                    "weight": question.penalty,
                    "time_spent": time_spent,
                }
            )

        total_questions = len(quiz_questions)
        percent = (correct / total_questions * 100) if total_questions else None

        score = {
            "correct": correct,
            "attempted": attempted,
            "total": total_questions,
            "percent": percent,
        }

        return rows, score

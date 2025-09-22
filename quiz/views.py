from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path
from typing import List

from django.conf import settings
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.generic import TemplateView, View
from django.utils import timezone

from .models import Attempt, QuizLink, TestState


class HomeView(TemplateView):
    template_name = "quiz/home.html"


class QuizSessionView(View):
    template_name = "quiz/question.html"
    timeout_seconds = 30

    def get(self, request, token, *args, **kwargs):
        quiz = self._get_quiz(token)
        start_key = self._start_flag_key(quiz.pk)

        access_allowed, access_context = self._check_test_access(quiz)
        if not access_allowed:
            self._clear_all_timers(request, quiz)
            request.session.pop(start_key, None)
            return render(
                request,
                "quiz/test_unavailable.html",
                access_context,
                status=403,
            )

        if quiz.completed_at and quiz.attempts.exists():
            self._clear_all_timers(request, quiz)
            rows, score = self._build_results(quiz)
            return render(
                request,
                "quiz/completed.html",
                {
                    "quiz": quiz,
                    "rows": rows,
                    "score": score,
                    "feedback_question_id": request.GET.get("feedback"),
                },
            )

        if not request.session.get(start_key):
            self._clear_all_timers(request, quiz)
            return render(request, "quiz/welcome.html", {"quiz": quiz})

        quiz_questions = list(quiz.ordered_quiz_questions())
        total_questions = len(quiz_questions)
        answered_count = (
            quiz.attempts.filter(
                question__quizquestion__quiz=quiz,
                question__quizquestion__is_disabled=False,
            )
            .distinct()
            .count()
        )

        if answered_count >= total_questions:
            self._delete_image(request.session.pop("last_image_path", None))
            self._clear_all_timers(request, quiz)
            quiz.mark_completed()
            request.session.pop(start_key, None)
            rows, score = self._build_results(quiz)
            return render(
                request,
                "quiz/completed.html",
                {
                    "quiz": quiz,
                    "rows": rows,
                    "score": score,
                    "feedback_question_id": request.GET.get("feedback"),
                },
            )

        self._delete_image(request.session.pop("last_image_path", None))
        current_quiz_question = quiz_questions[answered_count]
        question = current_quiz_question.question

        started_at = self._ensure_question_timer(request, quiz, question.id)
        elapsed_seconds = self._elapsed_seconds_since(started_at) or 0.0
        if elapsed_seconds >= self.timeout_seconds:
            self._clear_question_timer(request, quiz, question.id)
            Attempt.objects.create(
                quiz=quiz,
                question=question,
                selected_answer_index=None,
                time_spent=self.timeout_seconds,
            )
            return redirect("quiz:session", token=quiz.token)

        image_path = question.generate_image()
        image_url = f"{settings.MEDIA_URL}{image_path}" if settings.MEDIA_URL else image_path
        request.session["last_image_path"] = image_path

        remaining_seconds = max(0, int(self.timeout_seconds - elapsed_seconds))
        answers = list(enumerate(question.answers))
        random.shuffle(answers)

        context = {
            "quiz": quiz,
            "question": question,
            "answers": answers,
            "image_url": image_url,
            "image_path": image_path,
            "question_number": answered_count + 1,
            "total_questions": total_questions,
            "timeout_seconds": self.timeout_seconds,
            "remaining_seconds": remaining_seconds,
            "question_started_at": started_at,
            "server_now": timezone.now().isoformat(),
        }
        return render(request, self.template_name, context)

    def post(self, request, token, *args, **kwargs):
        quiz = self._get_quiz(token)
        start_key = self._start_flag_key(quiz.pk)

        access_allowed, access_context = self._check_test_access(quiz)
        if not access_allowed:
            self._clear_all_timers(request, quiz)
            request.session.pop(start_key, None)
            return render(
                request,
                "quiz/test_unavailable.html",
                access_context,
                status=403,
            )

        if "start_quiz" in request.POST:
            self._clear_all_timers(request, quiz)
            request.session[start_key] = True
            request.session.modified = True
            return redirect("quiz:session", token=quiz.token)

        if not request.session.get(start_key):
            self._clear_all_timers(request, quiz)
            return redirect("quiz:session", token=quiz.token)

        quiz_questions: List = list(quiz.ordered_quiz_questions())
        total_questions = len(quiz_questions)
        answered_count = (
            quiz.attempts.filter(
                question__quizquestion__quiz=quiz,
                question__quizquestion__is_disabled=False,
            )
            .distinct()
            .count()
        )

        if answered_count >= total_questions:
            self._clear_all_timers(request, quiz)
            quiz.mark_completed()
            request.session.pop(start_key, None)
            rows, score = self._build_results(quiz)
            return render(
                request,
                "quiz/completed.html",
                {
                    "quiz": quiz,
                    "rows": rows,
                    "score": score,
                    "feedback_question_id": None,
                },
            )

        current_quiz_question = quiz_questions[answered_count]
        question = current_quiz_question.question

        image_path = request.POST.get("image_path", "")
        self._delete_image(image_path)
        request.session.pop("last_image_path", None)

        submitted_question_id = request.POST.get("question_id")
        if str(question.id) != str(submitted_question_id):
            quiz_question_match = quiz.quiz_questions.filter(question_id=submitted_question_id).first()
            if quiz_question_match and quiz_question_match.is_disabled:
                return redirect("quiz:session", token=quiz.token)
            return HttpResponseBadRequest("Question mismatch")

        if current_quiz_question.is_disabled:
            return redirect("quiz:session", token=quiz.token)

        selected_answer = request.POST.get("selected_answer")
        selected_index = self._coerce_index(selected_answer, len(question.answers))

        started_at = self._get_question_timer(request, quiz, question.id)
        elapsed_seconds = self._elapsed_seconds_since(started_at) if started_at else None
        if elapsed_seconds is not None and elapsed_seconds >= self.timeout_seconds:
            selected_index = None

        time_spent = None
        if elapsed_seconds is not None:
            time_spent = max(0.0, min(float(self.timeout_seconds), float(elapsed_seconds)))

        Attempt.objects.create(
            quiz=quiz,
            question=question,
            selected_answer_index=selected_index,
            time_spent=time_spent,
        )

        self._clear_question_timer(request, quiz, question.id)

        answered_count += 1
        if answered_count >= total_questions:
            self._clear_all_timers(request, quiz)
            quiz.mark_completed()
            request.session.pop(start_key, None)
            rows, score = self._build_results(quiz)
            return render(
                request,
                "quiz/completed.html",
                {
                    "quiz": quiz,
                    "rows": rows,
                    "score": score,
                    "feedback_question_id": None,
                },
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

    def _timer_state_key(self, quiz_id: int) -> str:
        return f"quiz_timer_{quiz_id}"

    def _ensure_question_timer(self, request, quiz: QuizLink, question_id: int) -> str:
        timer_key = self._timer_state_key(quiz.pk)
        timers = request.session.get(timer_key, {})
        question_key = str(question_id)
        started_at = timers.get(question_key)
        if not started_at:
            started_at = timezone.now().isoformat()
            timers[question_key] = started_at
            request.session[timer_key] = timers
            request.session.modified = True
        return started_at

    def _get_question_timer(self, request, quiz: QuizLink, question_id: int) -> str | None:
        timer_key = self._timer_state_key(quiz.pk)
        timers = request.session.get(timer_key, {})
        return timers.get(str(question_id))

    def _clear_question_timer(self, request, quiz: QuizLink, question_id: int) -> None:
        timer_key = self._timer_state_key(quiz.pk)
        timers = request.session.get(timer_key)
        if not timers:
            return
        question_key = str(question_id)
        if question_key in timers:
            timers.pop(question_key, None)
            if timers:
                request.session[timer_key] = timers
            else:
                request.session.pop(timer_key, None)
            request.session.modified = True

    def _clear_all_timers(self, request, quiz: QuizLink) -> None:
        timer_key = self._timer_state_key(quiz.pk)
        if timer_key in request.session:
            request.session.pop(timer_key, None)
            request.session.modified = True

    def _elapsed_seconds_since(self, started_at: str | None) -> float | None:
        if not started_at:
            return None
        try:
            parsed = datetime.fromisoformat(started_at)
        except ValueError:
            return None
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed)
        now = timezone.now()
        delta = now - parsed
        return delta.total_seconds()

    @staticmethod
    def _build_results(quiz: QuizLink) -> tuple[list[dict], dict]:
        quiz_questions = list(quiz.ordered_quiz_questions())
        question_ids = [quiz_question.question_id for quiz_question in quiz_questions]

        attempts_queryset = quiz.attempts.select_related("question").order_by("created_at")
        if question_ids:
            attempts_queryset = attempts_queryset.filter(question_id__in=question_ids)
        else:
            attempts_queryset = attempts_queryset.none()

        attempts = {attempt.question_id: attempt for attempt in attempts_queryset}

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

            feedback_comment = ""
            has_feedback = False
            if not quiz_question.is_disabled and quiz_question.disabled_comment:
                feedback_comment = quiz_question.disabled_comment
                has_feedback = True

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
                    "quiz_question_id": quiz_question.id,
                    "feedback_comment": feedback_comment,
                    "has_feedback": has_feedback,
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

    def _check_test_access(self, quiz: QuizLink) -> tuple[bool, dict]:
        test = getattr(quiz, "test", None)
        if not test:
            return True, {"quiz": quiz, "test": None, "test_state": None}

        test.refresh_state()
        duration_minutes = None
        if test.duration:
            total_minutes = test.duration.total_seconds() / 60
            duration_minutes = max(1, int(round(total_minutes)))

        context = {
            "quiz": quiz,
            "test": test,
            "test_state": test.state,
            "is_finished": test.state == TestState.FINISHED,
            "is_pending": test.state == TestState.DRAFT,
            "duration_minutes": duration_minutes,
            "finished_at": test.finished_at,
            "started_at": test.started_at,
        }

        if test.state == TestState.ACTIVE:
            return True, context

        return False, context


class QuizQuestionFeedbackView(View):
    """Allow participants to submit optional per-question feedback once finished."""

    max_comment_length = 2000

    def post(self, request, token, quiz_question_id, *args, **kwargs):
        quiz = get_object_or_404(QuizLink, token=token)
        if quiz.completed_at is None:
            return HttpResponseBadRequest("Quiz not completed")

        quiz_question = (
            quiz.quiz_questions.select_related("question")
            .filter(pk=quiz_question_id, is_disabled=False)
            .first()
        )
        if not quiz_question:
            return HttpResponseBadRequest("Question not available")

        raw_comment = (request.POST.get("comment") or "").strip()
        comment = raw_comment
        if len(comment) > self.max_comment_length:
            comment = comment[: self.max_comment_length]
        was_trimmed = len(raw_comment) > len(comment)

        update_fields: list[str] = []
        if comment and quiz_question.disabled_comment != comment:
            quiz_question.disabled_comment = comment
            update_fields.append("disabled_comment")
        elif not comment and quiz_question.disabled_comment:
            quiz_question.disabled_comment = ""
            update_fields.append("disabled_comment")

        if update_fields:
            quiz_question.save(update_fields=update_fields)

        response_payload = {
            "quiz_question_id": quiz_question.pk,
            "comment": quiz_question.disabled_comment,
            "has_feedback": bool(quiz_question.disabled_comment),
            "was_trimmed": was_trimmed,
        }

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(response_payload)

        redirect_url = f"{reverse('quiz:session', args=[quiz.token])}?feedback={quiz_question.pk}"
        return redirect(redirect_url)

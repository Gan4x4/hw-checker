from pathlib import Path
from threading import local

from django.contrib import admin, messages
from django.db.models import Count, Q
from django.http import HttpResponseBadRequest
from django.middleware.csrf import get_token
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from django.db.models import Prefetch

from .forms import QuizImportForm
from .management.commands.import_questions import QuizImportError, import_quiz_from_json
from .models import Attempt, Question, QuizLink, QuizQuestion, Student
from .utils import sync_students_from_csv


_thread_locals = local()


def _current_request():
    return getattr(_thread_locals, "request", None)


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "email",
        "course",
        "group",
        "overall_grade",
        "score_percent",
        "student_actions",
    )
    search_fields = ("name", "email")

    def get_queryset(self, request):
        quizzes_prefetch = Prefetch(
            "quizzes",
            queryset=
            QuizLink.objects.select_related("student")
            .prefetch_related(
                "attempts",
                Prefetch(
                    "quiz_questions",
                    queryset=QuizQuestion.objects.select_related("question"),
                ),
            ),
        )
        return super().get_queryset(request).prefetch_related(quizzes_prefetch)

    @admin.display(description=_("Grade"))
    def overall_grade(self, obj):
        total = 0.0
        for quiz in obj.quizzes.all():
            attempts_question_ids = {attempt.question_id for attempt in quiz.attempts.all()}
            for quiz_question in quiz.quiz_questions.all():
                if quiz_question.question_id not in attempts_question_ids:
                    total += quiz_question.question.penalty
        return f"{total:.2f}"

    @admin.display(description=_("Score"))
    def score_percent(self, obj):
        correct = 0
        answered = 0
        for quiz in obj.quizzes.all():
            for attempt in quiz.attempts.all():
                answered += 1
                if attempt.is_correct:
                    correct += 1
        if not answered:
            return "—"
        percent = (correct / answered) * 100
        return f"{percent:.0f}%"

    @admin.display(description=_("Actions"), ordering=False)
    def student_actions(self, obj):
        view_url = reverse("admin:quiz_student_quizzes", args=[obj.pk])
        if hasattr(obj, "_prefetched_objects_cache") and "quizzes" in obj._prefetched_objects_cache:
            quiz_count = len(obj._prefetched_objects_cache["quizzes"])
        else:
            quiz_count = obj.quizzes.count()
        return format_html(
            '<a class="button" href="{}">{} <span style="display:inline-block; min-width:22px; padding:2px 6px; margin-left:6px; border-radius:999px; background:#e5ecff; color:#1d3b8b; font-weight:600; font-size:12px; text-align:center;">{}</span></a>',
            view_url,
            _("Quizzes"),
            quiz_count,
        )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<int:student_id>/quizzes/",
                self.admin_site.admin_view(self.quizzes_view),
                name="quiz_student_quizzes",
            ),
        ]
        return custom_urls + urls

    def quizzes_view(self, request, student_id):
        student = self.get_object(request, student_id)
        if not student:
            return HttpResponseBadRequest(_("Student not found."))

        quizzes = (
            student.quizzes.select_related("student")
            .prefetch_related("quiz_questions__question", "attempts")
            .order_by("-completed_at", "-created_at")
        )

        rows = []
        for quiz in quizzes:
            quiz_questions = list(quiz.quiz_questions.all())
            attempts = list(quiz.attempts.all())
            attempts_map = {attempt.question_id: attempt for attempt in attempts}
            total_questions = len(quiz_questions)
            answered = len(attempts)
            correct = sum(1 for attempt in attempts if attempt.is_correct)
            unanswered_weight = sum(
                qq.question.penalty
                for qq in quiz_questions
                if qq.question_id not in attempts_map
            )
            score_percent = (correct / answered * 100) if answered else None
            rows.append(
                {
                    "quiz": quiz,
                    "total_questions": total_questions,
                    "answered": answered,
                    "correct": correct,
                    "score_percent": score_percent,
                    "unanswered_weight": unanswered_weight,
                }
            )

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "student": student,
            "rows": rows,
            "title": _("Completed quizzes"),
        }
        return TemplateResponse(request, "admin/quiz/student/quizzes.html", context)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("id", "short_question", "penalty")
    search_fields = ("question", "code_snippet")

    @staticmethod
    def short_question(obj):  # pragma: no cover - admin display helper
        return obj.question[:60]


class QuizQuestionInline(admin.TabularInline):
    model = QuizQuestion
    extra = 1


@admin.register(QuizLink)
class QuizLinkAdmin(admin.ModelAdmin):
    list_display = (
        "token",
        "title",
        "student",
        "created_at",
        "completed_at",
        "score_display",
        "admin_actions",
    )
    inlines = [QuizQuestionInline]
    readonly_fields = ("token", "created_at", "completed_at")
    change_list_template = "admin/quiz/quizlink/change_list.html"

    def changelist_view(self, request, extra_context=None):
        _thread_locals.request = request
        try:
            response = super().changelist_view(request, extra_context=extra_context)
            if hasattr(response, "render") and callable(response.render):
                is_rendered = getattr(response, "is_rendered", True)
                if not is_rendered:
                    response.render()
            return response
        finally:
            _thread_locals.request = None

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "import/",
                self.admin_site.admin_view(self.import_view),
                name="quiz_quizlink_import",
            ),
            path(
                "<int:quiz_id>/reset/",
                self.admin_site.admin_view(self.reset_view),
                name="quiz_quizlink_reset",
            ),
            path(
                "<int:quiz_id>/results/",
                self.admin_site.admin_view(self.results_view),
                name="quiz_quizlink_results",
            ),
        ]
        return custom_urls + urls

    def get_queryset(self, request):
        queryset = (
            super()
            .get_queryset(request)
            .select_related("student")
            .annotate(
                attempts_total=Count("attempts", distinct=True),
                correct_total=Count(
                    "attempts",
                    filter=Q(attempts__is_correct=True),
                    distinct=True,
                ),
                question_total=Count("quiz_questions", distinct=True),
            )
        )
        return queryset

    def import_view(self, request):
        sync_students_from_csv()
        form = QuizImportForm(request.POST or None, request.FILES or None)

        if request.method == "POST" and form.is_valid():
            upload = form.cleaned_data["json_file"]
            default_name = Path(upload.name or "").stem or "Uploaded quiz"
            try:
                content = upload.read().decode("utf-8")
            except UnicodeDecodeError:
                form.add_error("json_file", _("File must be valid UTF-8 encoded JSON."))
            else:
                try:
                    quiz, created, json_student_name = import_quiz_from_json(
                        content, default_name=default_name
                    )
                except QuizImportError as exc:
                    form.add_error("json_file", str(exc))
                else:
                    selected_student = form.cleaned_data.get("student")
                    student = selected_student
                    if student is None and json_student_name:
                        student = Student.objects.filter(name__icontains=json_student_name).first()
                    if student:
                        quiz.student = student
                        quiz.save(update_fields=["student"])
                    messages.success(
                        request,
                        _(
                            "Created quiz '%(title)s' with %(count)d question(s). Token: %(token)s"
                        )
                        % {"title": quiz.title or quiz.token, "count": created, "token": quiz.token},
                    )
                    return redirect("admin:quiz_quizlink_changelist")

        context = {
            **self.admin_site.each_context(request),
            "form": form,
            "opts": self.model._meta,
            "title": _("Import quiz from JSON"),
        }
        return TemplateResponse(request, "admin/quiz/quizlink/import.html", context)

    def reset_view(self, request, quiz_id):
        if request.method != "POST":
            return HttpResponseBadRequest(_("Reset must be submitted via POST."))

        quiz = self.get_object(request, quiz_id)
        if not quiz:
            return HttpResponseBadRequest(_("Quiz not found."))

        removed = quiz.reset()
        messages.success(
            request,
            _("Reset quiz '%(title)s'; %(count)d attempt(s) removed.")
            % {"title": quiz.title or quiz.token, "count": removed},
        )
        return redirect("admin:quiz_quizlink_changelist")

    def results_view(self, request, quiz_id):
        quiz = self.get_object(request, quiz_id)
        if not quiz:
            return HttpResponseBadRequest(_("Quiz not found."))

        quiz_questions = list(
            quiz.quiz_questions.select_related("question").order_by("order")
        )
        attempts = {
            attempt.question_id: attempt
            for attempt in quiz.attempts.select_related("question").order_by("created_at")
        }

        rows = []
        correct = 0
        attempted = 0

        for quiz_question in quiz_questions:
            question = quiz_question.question
            attempt = attempts.get(question.id)
            selected_answer = None
            status = "unanswered"
            if attempt:
                attempted += 1
                index = attempt.selected_answer_index
                answers = question.answers or []
                if index is not None and 0 <= index < len(answers):
                    selected_answer = answers[index]
                status = "correct" if attempt.is_correct else "incorrect"
                if attempt.is_correct:
                    correct += 1

            correct_answer = None
            answers = question.answers or []
            if 0 <= question.correct_answer_index < len(answers):
                correct_answer = answers[question.correct_answer_index]

            rows.append(
                {
                    "order": quiz_question.order,
                    "question": question,
                    "attempt": attempt,
                    "selected_answer": selected_answer,
                    "correct_answer": correct_answer,
                    "answers": list(question.answers or []),
                    "status": status,
                    "weight": question.penalty,
                }
            )

        total_questions = len(quiz_questions)
        score_percent = (correct / total_questions * 100) if total_questions else None

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "quiz": quiz,
            "rows": rows,
            "score": {
                "correct": correct,
                "attempted": attempted,
                "total": total_questions,
                "percent": score_percent,
            },
            "title": _("Quiz results"),
        }
        return TemplateResponse(request, "admin/quiz/quizlink/results.html", context)

    @admin.display(description=_("Score"), ordering="correct_total")
    def score_display(self, obj):
        total_questions = getattr(obj, "question_total", None)
        if total_questions is None:
            total_questions = obj.total_questions()

        correct_answers = getattr(obj, "correct_total", None)
        if correct_answers is None:
            correct_answers = obj.attempts.filter(is_correct=True).count()

        if not total_questions:
            return "—"

        percent = (correct_answers / total_questions) * 100
        return f"{correct_answers}/{total_questions} ({percent:.0f}%)"

    @admin.display(description=_("Actions"), ordering=False)
    def admin_actions(self, obj):
        request = _current_request()
        has_attempts = obj.attempts.exists()

        view_url = reverse("admin:quiz_quizlink_results", args=[obj.pk])
        view_button = format_html('<a class="button" href="{}">{}</a>', view_url, _("View"))

        if not has_attempts and obj.completed_at is None:
            open_url = reverse("quiz:session", kwargs={"token": obj.token})
            if request:
                open_url = request.build_absolute_uri(open_url)
            open_button = format_html(
                '<a class="button" style="margin-left:4px;" href="{}" target="_blank" rel="noopener">{}</a>',
                open_url,
                _("Open"),
            )
            return format_html("{}{}", view_button, open_button)

        reset_url = reverse("admin:quiz_quizlink_reset", args=[obj.pk])
        csrf_input = ""
        if request:
            token = get_token(request)
            csrf_input = format_html(
                '<input type="hidden" name="csrfmiddlewaretoken" value="{}">', token
            )

        reset_button = format_html(
            '<form method="post" action="{}" style="display:inline; margin-left:4px;">'
            "{}"
            '<button type="submit" class="button">{}</button>'
            "</form>",
            reset_url,
            csrf_input,
            _("Reset"),
        )

        return format_html("{}{}", view_button, reset_button)


@admin.register(Attempt)
class AttemptAdmin(admin.ModelAdmin):
    list_display = ("quiz", "question", "selected_answer_index", "is_correct", "time_spent", "created_at")
    list_filter = ("quiz", "is_correct")
    search_fields = ("question__question",)
    readonly_fields = ("quiz", "question", "selected_answer_index", "is_correct", "time_spent", "created_at")

    def has_add_permission(self, request):  # pragma: no cover - admin guard
        return False

    def has_change_permission(self, request, obj=None):  # pragma: no cover - admin guard
        return False

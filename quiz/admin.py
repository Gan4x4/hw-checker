import csv
import json

from datetime import timedelta
from pathlib import Path
from threading import local

from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core.exceptions import ValidationError
from django.db.models import Count, Q, F, Prefetch
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from .forms import QuizImportForm, StudentImportForm, TestCreationForm
from .management.commands.import_questions import QuizImportError, import_quiz_from_json
from .models import Attempt, Question, QuizLink, QuizQuestion, Student, Test, TestState
from .utils import import_students_from_content, sync_students_from_csv


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
    change_list_template = "admin/quiz/student/change_list.html"

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
            active_quiz_questions = list(quiz.ordered_quiz_questions())
            active_question_ids = {qq.question_id for qq in active_quiz_questions}
            attempts_question_ids = {
                attempt.question_id
                for attempt in quiz.attempts.all()
                if attempt.question_id in active_question_ids
            }
            for quiz_question in active_quiz_questions:
                if quiz_question.question_id not in attempts_question_ids:
                    total += quiz_question.question.penalty
        return f"{total:.2f}"

    @admin.display(description=_("Score"))
    def score_percent(self, obj):
        correct = 0
        answered = 0
        for quiz in obj.quizzes.all():
            active_question_ids = {
                qq.question_id for qq in quiz.ordered_quiz_questions()
            }
            for attempt in quiz.attempts.all():
                if attempt.question_id not in active_question_ids:
                    continue
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

    def changelist_view(self, request, extra_context=None):
        form = StudentImportForm(request.POST or None, request.FILES or None)

        if request.method == "POST" and form.is_valid():
            upload = form.cleaned_data["csv_file"]
            try:
                content = upload.read().decode("utf-8-sig")
            except UnicodeDecodeError:
                form.add_error("csv_file", _("File must be valid UTF-8 encoded CSV."))
            else:
                try:
                    created = import_students_from_content(content)
                except Exception as exc:  # pragma: no cover - handled via admin feedback
                    form.add_error("csv_file", str(exc))
                else:
                    if created:
                        messages.success(
                            request,
                            _("Imported or updated %(count)d student(s).")
                            % {"count": created},
                        )
                    else:
                        messages.info(
                            request,
                            _("No new students were imported or updated."),
                        )
                    return HttpResponseRedirect(request.path)

        extra_context = extra_context or {}
        extra_context["import_form"] = form
        return super().changelist_view(request, extra_context=extra_context)

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
            quiz_questions = list(quiz.ordered_quiz_questions())
            active_question_ids = {qq.question_id for qq in quiz_questions}
            attempts = [
                attempt
                for attempt in quiz.attempts.all()
                if attempt.question_id in active_question_ids
            ]
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
        "unhidden_question_count",
        "score_display",
        "admin_actions",
    )
    inlines = [QuizQuestionInline]
    readonly_fields = ("token", "created_at", "completed_at")
    change_list_template = "admin/quiz/quizlink/change_list.html"
    actions = ["download_hidden_questions_action", "make_test_action"]

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
            path(
                "<int:quiz_id>/results/<int:quiz_question_id>/disable/",
                self.admin_site.admin_view(self.disable_question_view),
                name="quiz_quizlink_disable_question",
            ),
            path(
                "<int:quiz_id>/results/<int:quiz_question_id>/enable/",
                self.admin_site.admin_view(self.enable_question_view),
                name="quiz_quizlink_enable_question",
            ),
            path(
                "<int:quiz_id>/results/export-hidden/",
                self.admin_site.admin_view(self.export_hidden_questions_view),
                name="quiz_quizlink_export_hidden",
            ),
        ]
        return custom_urls + urls

    def get_queryset(self, request):
        queryset = (
            super()
            .get_queryset(request)
            .select_related("student")
            .annotate(
                attempts_total=Count(
                    "attempts",
                    filter=Q(
                        attempts__question__quizquestion__quiz=F("pk"),
                        attempts__question__quizquestion__is_disabled=False,
                    ),
                    distinct=True,
                ),
                correct_total=Count(
                    "attempts",
                    filter=Q(
                        attempts__is_correct=True,
                        attempts__question__quizquestion__quiz=F("pk"),
                        attempts__question__quizquestion__is_disabled=False,
                    ),
                    distinct=True,
                ),
                question_total=Count(
                    "quiz_questions",
                    filter=Q(quiz_questions__is_disabled=False),
                    distinct=True,
                ),
            )
        )
        return queryset

    @admin.display(description=_("Unhidden questions"), ordering="question_total")
    def unhidden_question_count(self, obj):
        total = getattr(obj, "question_total", None)
        if total is None:
            total = obj.quiz_questions.filter(is_disabled=False).count()
        return total

    def make_test_action(self, request, queryset):
        selected_ids = request.POST.getlist(ACTION_CHECKBOX_NAME)
        if not selected_ids:
            self.message_user(
                request,
                _("Select at least one quiz to build a test."),
                level=messages.WARNING,
            )
            return None

        quizzes = list(queryset.select_related("student", "test"))

        if not quizzes:
            self.message_user(
                request,
                _("No quizzes were selected."),
                level=messages.WARNING,
            )
            return None

        if "apply" in request.POST:
            form = TestCreationForm(request.POST)
            if form.is_valid():
                minutes = form.cleaned_data["duration_minutes"]
                duration = timedelta(minutes=minutes)
                title = form.cleaned_data["title"].strip()

                if not title:
                    first_named_quiz = next((quiz.title for quiz in quizzes if quiz.title), "")
                    if first_named_quiz:
                        title = _("Test for %(quiz)s") % {"quiz": first_named_quiz}
                    else:
                        title = timezone.now().strftime("Test %Y-%m-%d %H:%M")

                test = Test.objects.create(title=title, duration=duration)
                queryset.update(test=test)

                reassigned = [quiz for quiz in quizzes if quiz.test_id and quiz.test_id != test.pk]
                if reassigned:
                    self.message_user(
                        request,
                        _("%(count)d quiz(es) were reassigned to the new test.")
                        % {"count": len(reassigned)},
                        level=messages.WARNING,
                    )

                self.message_user(
                    request,
                    _(
                        "Created test '%(title)s' with %(count)d quiz(es)."
                    )
                    % {"title": test.title or test.pk, "count": len(quizzes)},
                    level=messages.SUCCESS,
                )
                return redirect("admin:quiz_test_change", test.pk)
        else:
            initial_title = next((quiz.title for quiz in quizzes if quiz.title), "")
            form = TestCreationForm(initial={"title": initial_title})

        context = {
            **self.admin_site.each_context(request),
            "title": _("Create test"),
            "form": form,
            "opts": self.model._meta,
            "quizzes": quizzes,
            "action_checkbox_name": ACTION_CHECKBOX_NAME,
            "selected_ids": selected_ids,
            "select_across": request.POST.get("select_across"),
            "action_name": "make_test_action",
        }
        return TemplateResponse(request, "admin/quiz/quizlink/make_test.html", context)

    make_test_action.short_description = _("Make test")

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
        total_active = 0

        for quiz_question in quiz_questions:
            question = quiz_question.question
            attempt = attempts.get(question.id)
            selected_answer = None
            status = "unanswered"
            answers = question.answers or []
            if attempt:
                index = attempt.selected_answer_index
                if index is not None and 0 <= index < len(answers):
                    selected_answer = answers[index]

            is_disabled = quiz_question.is_disabled
            comment = quiz_question.disabled_comment or ""
            has_feedback = bool(comment and not is_disabled)

            if is_disabled:
                status = "disabled"
            else:
                if attempt:
                    attempted += 1
                    status = "correct" if attempt.is_correct else "incorrect"
                    if attempt.is_correct:
                        correct += 1
                total_active += 1

            correct_answer = None
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
                    "is_disabled": is_disabled,
                    "disabled_comment": comment,
                    "has_feedback": has_feedback,
                    "quiz_question_id": quiz_question.id,
                }
            )

        score_percent = (correct / total_active * 100) if total_active else None
        disabled_count = sum(1 for row in rows if row["is_disabled"])
        feedback_count = sum(1 for row in rows if row["has_feedback"])

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "quiz": quiz,
            "rows": rows,
            "score": {
                "correct": correct,
                "attempted": attempted,
                "total": total_active,
                "percent": score_percent,
            },
            "disabled_count": disabled_count,
            "feedback_count": feedback_count,
            "hidden_export_url": (
                reverse("admin:quiz_quizlink_export_hidden", args=[quiz.pk])
                if disabled_count
                else None
            ),
            "title": _("Quiz results"),
        }
        return TemplateResponse(request, "admin/quiz/quizlink/results.html", context)

    def disable_question_view(self, request, quiz_id, quiz_question_id):
        if request.method != "POST":
            return HttpResponseBadRequest(_("Disable must be submitted via POST."))

        quiz = self.get_object(request, quiz_id)
        if not quiz:
            return HttpResponseBadRequest(_("Quiz not found."))

        try:
            quiz_question = quiz.quiz_questions.select_related("question").get(pk=quiz_question_id)
        except QuizQuestion.DoesNotExist:
            return HttpResponseBadRequest(_("Question not found."))

        comment = (request.POST.get("comment") or "").strip()
        if not comment:
            messages.error(request, _("Comment is required to disable a question."))
            return redirect("admin:quiz_quizlink_results", quiz_id)

        if not quiz_question.is_disabled or quiz_question.disabled_comment != comment:
            quiz_question.is_disabled = True
            quiz_question.disabled_comment = comment
            quiz_question.save(update_fields=["is_disabled", "disabled_comment"])

        messages.success(request, _("Question disabled."))
        return redirect("admin:quiz_quizlink_results", quiz_id)

    def enable_question_view(self, request, quiz_id, quiz_question_id):
        if request.method != "POST":
            return HttpResponseBadRequest(_("Enable must be submitted via POST."))

        quiz = self.get_object(request, quiz_id)
        if not quiz:
            return HttpResponseBadRequest(_("Quiz not found."))

        try:
            quiz_question = quiz.quiz_questions.select_related("question").get(pk=quiz_question_id)
        except QuizQuestion.DoesNotExist:
            return HttpResponseBadRequest(_("Question not found."))

        was_disabled = quiz_question.is_disabled
        had_comment = bool(quiz_question.disabled_comment)

        quiz_question.is_disabled = False
        quiz_question.disabled_comment = ""
        quiz_question.save(update_fields=["is_disabled", "disabled_comment"])

        if was_disabled:
            message = _("Question enabled.")
        elif had_comment:
            message = _("Feedback cleared.")
        else:
            message = _("Visibility updated.")

        messages.success(request, message)
        return redirect("admin:quiz_quizlink_results", quiz_id)

    def export_hidden_questions_view(self, request, quiz_id):
        quiz = self.get_object(request, quiz_id)
        if not quiz:
            return HttpResponseBadRequest(_("Quiz not found."))

        hidden_questions = list(
            quiz.quiz_questions.select_related("question")
            .filter(is_disabled=True)
            .order_by("order")
        )

        if not hidden_questions:
            messages.info(request, _("This quiz has no hidden questions."))
            return redirect("admin:quiz_quizlink_results", quiz_id)

        payload = self._build_hidden_questions_payload(quiz, hidden_questions)

        filename_parts = [slugify(quiz.title or "quiz"), "hidden", "questions"]
        filename = "-".join(part for part in filename_parts if part) or "hidden-questions"
        json_content = json.dumps(payload, ensure_ascii=False, indent=2)

        response = HttpResponse(json_content, content_type="application/json")
        response["Content-Disposition"] = f'attachment; filename="{filename}.json"'
        return response

    def download_hidden_questions_action(self, request, queryset):
        quizzes = queryset.select_related("student").prefetch_related(
            Prefetch("quiz_questions", queryset=QuizQuestion.objects.select_related("question"))
        )

        payload = []
        for quiz in quizzes:
            hidden_questions = [qq for qq in quiz.quiz_questions.all() if qq.is_disabled]
            if not hidden_questions:
                continue
            payload.append(self._build_hidden_questions_payload(quiz, hidden_questions))

        if not payload:
            self.message_user(request, _("No hidden questions in selected quizzes."), level=messages.INFO)
            return None

        filename = "hidden-questions" if len(payload) > 1 else slugify(payload[0]["name"] or "quiz") or "quiz"
        json_content = json.dumps(payload, ensure_ascii=False, indent=2)
        response = HttpResponse(json_content, content_type="application/json")
        response["Content-Disposition"] = f'attachment; filename="{filename}-hidden.json"'
        return response

    download_hidden_questions_action.short_description = _("Download hidden questions")

    def _build_hidden_questions_payload(self, quiz, hidden_questions):
        payload = {
            "name": quiz.title or "",
            "student": quiz.student.name if quiz.student else "",
            "questions": [],
        }

        for quiz_question in hidden_questions:
            question = quiz_question.question
            question_payload = {
                "question": question.question,
                "answers": list(question.answers or []),
                "correct_answer_index": question.correct_answer_index,
                "weight": question.penalty,
            }
            if question.code_snippet:
                question_payload["code_snippet"] = question.code_snippet
            if question.explanation:
                question_payload["explanation"] = question.explanation
            if question.teacher_note:
                question_payload["teacher_note"] = question.teacher_note
            if question.source:
                question_payload["source"] = question.source
            if quiz_question.disabled_comment:
                question_payload["disabled_comment"] = quiz_question.disabled_comment

            payload["questions"].append(question_payload)

        return payload


    @admin.display(description=_("Score"), ordering="correct_total")
    def score_display(self, obj):
        total_questions = getattr(obj, "question_total", None)
        if total_questions is None:
            total_questions = obj.total_questions()

        correct_answers = getattr(obj, "correct_total", None)
        if correct_answers is None:
            correct_answers = (
                obj.attempts.filter(
                    is_correct=True,
                    question__quizquestion__quiz=obj,
                    question__quizquestion__is_disabled=False,
                )
                .distinct()
                .count()
            )

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

        reset_button = format_html(
            '<button type="submit" class="button" style="margin-left:4px;" '
            'formmethod="post" formaction="{}" formnovalidate>{}</button>',
            reset_url,
            _("Reset"),
        )

        return format_html("{}{}", view_button, reset_button)


class TestQuizLinkInline(admin.TabularInline):
    model = QuizLink
    fields = ("title", "student", "created_at", "completed_at")
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):  # pragma: no cover - admin guard
        return False


@admin.register(Test)
class TestAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "state_display",
        "duration",
        "started_at",
        "finished_at",
        "quiz_count",
    )
    readonly_fields = ("state", "started_at", "finished_at", "created_at")
    inlines = [TestQuizLinkInline]
    change_form_template = "admin/quiz/test/change_form.html"

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.prefetch_related("quizzes")

    @admin.display(description=_("State"), ordering="state")
    def state_display(self, obj):
        obj.refresh_state()
        return obj.get_state_display()

    @admin.display(description=_("Quizzes"))
    def quiz_count(self, obj):
        return obj.quizzes.count()

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        obj = self.get_object(request, object_id) if object_id else None

        if request.method == "POST" and request.POST.get("_export_links"):
            if not obj:
                self.message_user(
                    request,
                    _("Test not found."),
                    level=messages.ERROR,
                )
                return redirect("admin:quiz_test_changelist")

            filename_root = slugify(obj.title or f"test-{obj.pk}") or f"test-{obj.pk}"
            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = f'attachment; filename="{filename_root}-links.csv"'

            writer = csv.writer(response)
            writer.writerow(["name", "email", "quiz_url"])

            quizzes = (
                obj.quizzes.select_related("student")
                .order_by("student__name", "student__email", "pk")
            )
            for quiz in quizzes:
                student = quiz.student
                name = (student.name or "") if student else ""
                email = (student.email or "") if student else ""
                link = reverse("quiz:session", kwargs={"token": quiz.token})
                link = request.build_absolute_uri(link)
                writer.writerow([name, email, link])

            return response

        if request.method == "POST":
            if request.POST.get("_reset_test"):
                if not obj:
                    self.message_user(
                        request,
                        _("Test not found."),
                        level=messages.ERROR,
                    )
                    return redirect("admin:quiz_test_changelist")
                total_attempts = obj.reset()
                if total_attempts:
                    self.message_user(
                        request,
                        _("Test reset. %(count)d attempt(s) cleared.")
                        % {"count": total_attempts},
                        level=messages.SUCCESS,
                    )
                else:
                    self.message_user(
                        request,
                        _("Test reset."),
                        level=messages.INFO,
                    )
                return redirect("admin:quiz_test_change", obj.pk)

            if request.POST.get("_start_test"):
                if not obj:
                    self.message_user(
                        request,
                        _("Test not found."),
                        level=messages.ERROR,
                    )
                    return redirect("admin:quiz_test_changelist")
                try:
                    obj.start()
                except ValidationError as exc:
                    self.message_user(request, str(exc), level=messages.ERROR)
                else:
                    self.message_user(
                        request,
                        _("Test '%(title)s' started.")
                        % {"title": obj.title or obj.pk},
                        level=messages.SUCCESS,
                    )
                return redirect("admin:quiz_test_change", obj.pk)

        extra_context = extra_context or {}
        if obj:
            obj.refresh_state()
            has_quizzes = obj.quizzes.exists()
            can_reset = obj.can_reset()
            extra_context.update(
                {
                    "can_start": obj.can_start() and has_quizzes,
                    "is_active": obj.state == TestState.ACTIVE,
                    "remaining_seconds": obj.remaining_seconds(),
                    "has_quizzes": has_quizzes,
                    "can_reset": can_reset,
                }
            )

        return super().changeform_view(
            request,
            object_id=object_id,
            form_url=form_url,
            extra_context=extra_context,
        )


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

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from quiz.models import Attempt, Question, QuizLink, QuizQuestion


class QuizImportError(Exception):
    """Raised when a quiz import payload is invalid."""


def _question_from_payload(data: Dict[str, Any], *, entry_index: int) -> Question:
    if not isinstance(data, dict):
        raise QuizImportError(f"Entry #{entry_index} must be an object with question fields.")

    question_text = data.get("question")
    if not isinstance(question_text, str) or not question_text.strip():
        raise QuizImportError(f"Entry #{entry_index} is missing a non-empty 'question' field.")

    answers = data.get("answers")
    if not isinstance(answers, list) or not answers:
        raise QuizImportError(f"Entry #{entry_index} must include a non-empty 'answers' list.")

    cleaned_answers: List[str] = []
    correct_index: int | None = None

    explicit_index = data.get("correct_answer_index")
    if explicit_index is not None:
        try:
            correct_index = int(explicit_index)
        except (TypeError, ValueError):
            raise QuizImportError(
                f"Entry #{entry_index} has non-integer 'correct_answer_index'."
            ) from None
    for answer_index, raw_answer in enumerate(answers):
        if not isinstance(raw_answer, str):
            raise QuizImportError(
                f"Entry #{entry_index} contains a non-string answer at position {answer_index + 1}."
            )
        normalized = raw_answer.strip()
        trailing_marker = normalized.rstrip()
        if trailing_marker.endswith("*"):
            normalized = trailing_marker.rstrip("*").rstrip()
            if correct_index is not None and explicit_index is not None:
                pass
            elif correct_index is not None:
                raise QuizImportError(
                    f"Entry #{entry_index} marks more than one answer as correct (use '*' once)."
                )
            else:
                correct_index = answer_index
        if normalized and normalized[0].lower() in {"a", "b", "c", "d", "e", "f"} and normalized[:2] in {"a)", "b)", "c)", "d)", "e)", "f)"}:
            normalized = normalized[2:].lstrip()
        cleaned_answers.append(normalized)

    if correct_index is None:
        raise QuizImportError(
            f"Entry #{entry_index} must include 'correct_answer_index' or mark one answer with '*'."
        )

    if not 0 <= correct_index < len(cleaned_answers):
        raise QuizImportError(
            f"Entry #{entry_index} has 'correct_answer_index' out of range for the answers list."
        )

    penalty_raw = data.get("weight", data.get("penalty", 0))
    try:
        penalty_value = float(penalty_raw)
    except (TypeError, ValueError):
        raise QuizImportError(f"Entry #{entry_index} has a non-numeric 'penalty'.") from None

    source_text = data.get("source")
    if source_text is not None and not isinstance(source_text, str):
        raise QuizImportError(f"Entry #{entry_index} has a non-string 'source'.")

    return Question(
        code_snippet=data.get("code_snippet", ""),
        question=question_text.strip(),
        answers=cleaned_answers,
        correct_answer_index=correct_index,
        explanation=(data.get("explanation") or ""),
        teacher_note=(data.get("teacher_note") or ""),
        penalty=penalty_value,
        source=(source_text or "").strip(),
    )


def _normalize_payload(payload: Any) -> tuple[str | None, str | None, List[Question]]:
    quiz_name: str | None = None
    student_name: str | None = None

    if isinstance(payload, dict):
        questions_data = payload.get("questions")
        if questions_data is None:
            raise QuizImportError("Expected a 'questions' list in the JSON object.")
        quiz_name_raw = payload.get("name")
        student_raw = payload.get("student")
        if isinstance(quiz_name_raw, str) and quiz_name_raw.strip():
            quiz_name = quiz_name_raw.strip()
        if isinstance(student_raw, str) and student_raw.strip():
            student_name = student_raw.strip()
    elif isinstance(payload, list):
        questions_data = payload
    else:
        raise QuizImportError("The JSON root must be either a list or an object with a 'questions' list.")

    if not isinstance(questions_data, list) or not questions_data:
        raise QuizImportError("Provide at least one question in the 'questions' list.")

    questions: List[Question] = []
    for index, item in enumerate(questions_data, start=1):
        questions.append(_question_from_payload(item, entry_index=index))

    if quiz_name and student_name:
        combined = f"{quiz_name} - {student_name}"
    else:
        combined = quiz_name or student_name

    return combined, student_name, questions


def _fallback_name(raw: str | None) -> str:
    if not raw:
        return "Untitled quiz"
    stem = Path(raw).stem
    return stem or "Untitled quiz"


def _title_max_length(default: int = 20) -> int:
    """Return the configured maximum title length or the provided default."""

    value = getattr(settings, "QUIZ_TITLE_MAX_LENGTH", default)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _short_title_from_filename(filename: str | None, *, max_length: int | None = None) -> str:
    """Return a trimmed stem of ``filename`` capped to ``max_length`` characters."""

    if not filename:
        return _fallback_name(filename)

    limit = max_length if max_length is not None else _title_max_length()
    stem = Path(filename).stem or filename
    title = stem.strip() or _fallback_name(filename)
    if len(title) > limit:
        return title[:limit]
    return title


def import_quiz_from_json(
    content: str,
    *,
    default_name: str,
    replace: bool = False,
    source_filename: str | None = None,
) -> Tuple[QuizLink, int, str | None]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise QuizImportError(f"Invalid JSON: {exc}") from exc

    quiz_name, student_name, questions = _normalize_payload(payload)
    quiz_title = quiz_name or _short_title_from_filename(source_filename or default_name)
    original_filename = source_filename or ""

    with transaction.atomic():
        if replace:
            Attempt.objects.all().delete()
            QuizQuestion.objects.all().delete()
            QuizLink.objects.all().delete()
            Question.objects.all().delete()

        for question in questions:
            question.save()

        quiz = QuizLink.objects.create(
            title=quiz_title,
            original_filename=original_filename,
        )
        for order, question in enumerate(questions, start=1):
            QuizQuestion.objects.create(quiz=quiz, question=question, order=order)

    return quiz, len(questions), student_name


def import_quiz_from_path(
    path: Path, *, replace: bool = False
) -> Tuple[QuizLink, int, str | None]:
    return import_quiz_from_json(
        path.read_text(encoding="utf-8"),
        default_name=path.name,
        replace=replace,
        source_filename=path.name,
    )


class Command(BaseCommand):
    help = "Import questions and create a quiz from a JSON file."

    def add_arguments(self, parser) -> None:
        parser.add_argument("json_path", type=str, help="Path to the JSON file containing questions")
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Delete all existing quizzes, questions, and attempts before importing the file.",
        )

    def handle(self, json_path: str, replace: bool, **options) -> None:
        path = Path(json_path)
        if not path.exists():
            raise CommandError(f"File not found: {path}")

        try:
            quiz, created, _ = import_quiz_from_path(path, replace=replace)
        except QuizImportError as exc:
            raise CommandError(str(exc)) from exc

        message = (
            f"Imported {created} question(s) into quiz '{quiz.title}' (token: {quiz.token})."
        )
        self.stdout.write(self.style.SUCCESS(message))

from __future__ import annotations

import textwrap
import uuid
from pathlib import Path
from typing import List

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS, models

from PIL import Image, ImageDraw, ImageFont


def _answers_default() -> List[str]:
    return []


def _load_font(size: int = 16) -> "ImageFont.ImageFont":
    """Load a font that supports ASCII and Cyrillic, falling back to default."""

    candidate_paths = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ]

    for font_path in candidate_paths:
        if font_path.exists():
            try:
                return ImageFont.truetype(str(font_path), size=size)
            except OSError:
                continue

    return ImageFont.load_default()

class Student(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    course = models.CharField(max_length=255, blank=True)
    group = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:  # pragma: no cover - admin display helper
        return self.name or self.email


class Question(models.Model):
    code_snippet = models.TextField(blank=True)
    question = models.TextField()
    answers = models.JSONField(default=_answers_default)
    correct_answer_index = models.PositiveSmallIntegerField()
    explanation = models.TextField(blank=True)
    teacher_note = models.TextField(blank=True)
    penalty = models.FloatField(default=0)
    source = models.CharField(max_length=255, blank=True)
    image_path = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:  # pragma: no cover - admin display helper
        return textwrap.shorten(self.question, width=60) if self.question else "Question"

    def clean(self) -> None:
        super().clean()
        if not self.answers:
            raise ValidationError("Question must include answer choices.")
        if self.correct_answer_index >= len(self.answers):
            raise ValidationError("Correct answer index is out of range for the provided answers.")

    def generate_image(self) -> str:
        """Render the code snippet and question text into an image on disk.

        Returns the relative media path (e.g. ``questions/<uuid>.png``).
        """

        main_font = _load_font()
        source_font = _load_font(size=12)

        def line_height(font: "ImageFont.ImageFont", base: int) -> int:
            bbox = font.getbbox("Ag")
            height = bbox[3] - bbox[1]
            padding = base
            return int(height + padding)

        main_height = line_height(main_font, 6)
        source_height = line_height(source_font, 4)

        render_lines: List[tuple[str, "ImageFont.ImageFont", int]] = []

        if self.code_snippet:
            snippet_lines = self.code_snippet.rstrip().splitlines() or [""]
            for snippet_line in snippet_lines:
                render_lines.append((snippet_line, main_font, main_height))
            render_lines.append(("", main_font, main_height))

        wrapped_question: List[str] = []
        paragraphs = [segment.strip() for segment in self.question.split("\n\n") if segment.strip()]
        if not paragraphs:
            paragraphs = [self.question.strip()]
        for paragraph in paragraphs:
            wrapped_question.extend(textwrap.wrap(paragraph, width=60) or [""])
            wrapped_question.append("")
        if not wrapped_question:
            wrapped_question = [""]
        # Remove trailing blank line introduced by wrapping logic
        if wrapped_question and wrapped_question[-1] == "":
            wrapped_question.pop()

        for text_line in wrapped_question:
            render_lines.append((text_line, main_font, main_height))

        if self.source:
            if render_lines and render_lines[-1][0] != "":
                render_lines.append(("", main_font, main_height))
            source_text = f"Source: {self.source.strip()}"
            render_lines.append((source_text, source_font, source_height))

        padding = 30
        if render_lines:
            max_line_width = max(
                font.getlength(text) if text else 0 for text, font, _ in render_lines
            )
            max_line_width = max(max_line_width, main_font.getlength(" "))
            content_height = sum(height for _, _, height in render_lines)
        else:
            max_line_width = main_font.getlength(" ")
            content_height = main_height

        image_width = int(max_line_width + padding * 2)
        image_height = int(content_height + padding * 2)

        image = Image.new("RGB", (image_width, image_height), color="white")
        draw = ImageDraw.Draw(image)

        y = padding
        for text, font, height in render_lines:
            if text:
                draw.text((padding, y), text, fill="black", font=font)
            y += height

        media_root = Path(settings.MEDIA_ROOT)
        questions_dir = media_root / "questions"
        questions_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex}.png"
        image_path = questions_dir / filename
        image.save(image_path, format="PNG")
        relative_path = str(Path("questions") / filename)
        self.image_path = relative_path
        if self.pk:
            self.save(update_fields=["image_path"])
        return relative_path


class QuizLink(models.Model):
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    title = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    student = models.ForeignKey(
        Student,
        null=True,
        blank=True,
        related_name="quizzes",
        on_delete=models.SET_NULL,
    )
    questions = models.ManyToManyField(Question, through="QuizQuestion", related_name="quiz_links")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - admin display helper
        return self.title or str(self.token)

    def ordered_quiz_questions(self) -> models.QuerySet["QuizQuestion"]:
        return self.quiz_questions.select_related("question").order_by("order")

    def total_questions(self) -> int:
        return self.quiz_questions.count()

    def mark_completed(self) -> None:
        if self.completed_at is None:
            from django.utils import timezone

            self.completed_at = timezone.now()
            self.save(update_fields=["completed_at"])

    def reset(self) -> int:
        """Clear attempts and bring the quiz back to a fresh state.

        Returns the number of attempts removed.
        """

        from django.db import transaction

        db_alias = self._state.db or DEFAULT_DB_ALIAS

        with transaction.atomic(using=db_alias):
            deleted, _ = self.attempts.using(db_alias).delete()
            if self.completed_at is not None:
                self.completed_at = None
                self.save(update_fields=["completed_at"])

        if hasattr(self, "_prefetched_objects_cache"):
            self._prefetched_objects_cache.pop("attempts", None)

        return deleted


class QuizQuestion(models.Model):
    quiz = models.ForeignKey(QuizLink, related_name="quiz_questions", on_delete=models.CASCADE)
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    order = models.PositiveIntegerField()

    class Meta:
        ordering = ["order"]
        unique_together = ("quiz", "order")

    def __str__(self) -> str:  # pragma: no cover - admin display helper
        return f"{self.quiz} - {self.question} ({self.order})"


class Attempt(models.Model):
    quiz = models.ForeignKey(QuizLink, related_name="attempts", on_delete=models.CASCADE)
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    selected_answer_index = models.IntegerField(null=True, blank=True)
    is_correct = models.BooleanField(default=False)
    time_spent = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:  # pragma: no cover - admin display helper
        return f"Attempt({self.quiz}, {self.question})"

    def save(self, *args, **kwargs) -> None:
        if self.selected_answer_index is None:
            self.is_correct = False
        else:
            self.is_correct = self.question.correct_answer_index == self.selected_answer_index
        super().save(*args, **kwargs)

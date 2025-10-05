from __future__ import annotations

import csv
import io
import textwrap
from pathlib import Path
from typing import Iterable, List, Mapping

from django.conf import settings
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe

def _parse_wrap_width(width: int | str | None) -> int | None:
    """Return a positive int width or ``None`` when not usable."""

    if width is None:
        width = getattr(settings, "QUIZ_IMAGE_WRAP_WIDTH", None)

    try:
        parsed = int(width)  # type: ignore[arg-type]
    except (TypeError, ValueError):  # pragma: no cover - defensive branch
        return None

    return parsed if parsed > 0 else None


def wrap_text_to_lines(text: str, *, width: int | str | None = None) -> List[str]:
    """Split ``text`` into lines no longer than ``width`` characters.

    Existing newlines are preserved, including blank lines, so paragraphs stay intact.
    When ``width`` cannot be parsed or is non-positive, the original ``splitlines``
    output is returned unchanged.
    """

    if not text:
        return []

    parsed_width = _parse_wrap_width(width)
    if parsed_width is None:
        return text.splitlines()

    wrapped: List[str] = []
    for raw_line in text.splitlines():
        normalized_line = raw_line.replace("\u00A0", " ")
        if not normalized_line.strip():
            wrapped.append("")
            continue

        segments = textwrap.wrap(
            normalized_line,
            width=parsed_width,
            break_long_words=True,
            drop_whitespace=False,
            replace_whitespace=False,
        )
        if segments:
            wrapped.extend(segments)
        else:  # pragma: no cover - textwrap returns at least one segment
            wrapped.append("")

    return wrapped


def wrap_text(text: str, *, width: int | str | None = None) -> str:
    """Return ``text`` with ``\n`` inserted so each line fits within ``width``."""

    if not text:
        return text

    lines = wrap_text_to_lines(text, width=width)
    return "\n".join(lines)


def wrap_text_html(text: str | None, *, width: int | str | None = None) -> str:
    """Return HTML-safe string with ``<br>`` separators at the configured width."""

    if not text:
        return ""

    lines = wrap_text_to_lines(text, width=width)
    if not lines:
        return ""

    escaped = [conditional_escape(line) for line in lines]
    return mark_safe("<br>".join(escaped))


def wrap_code_snippet(text: str | None, *, width: int | str | None = None) -> str:
    """Insert raw newlines in code ``text`` to keep line length under ``width``."""

    if text is None:
        return ""

    parsed_width = _parse_wrap_width(width)
    if parsed_width is None:
        return text

    wrapped_lines: List[str] = []
    for line in text.splitlines():
        if not line:
            wrapped_lines.append("")
            continue

        indent_length = len(line) - len(line.lstrip(" \t"))
        indent = line[:indent_length]
        remainder = line

        while len(remainder) > parsed_width:
            break_pos = remainder.rfind(" ", 0, parsed_width + 1)
            if break_pos <= indent_length:
                break_pos = parsed_width
                chunk = remainder[:break_pos]
                remainder = remainder[break_pos:]
            else:
                chunk = remainder[:break_pos]
                remainder = remainder[break_pos + 1 :]

            wrapped_lines.append(chunk)
            remainder = indent + remainder.lstrip(" \t")
            if not remainder:
                break

        if remainder:
            wrapped_lines.append(remainder)

    return "\n".join(wrapped_lines)


def find_participants_csv() -> Path | None:
    """Return the first existing participants.csv path in the known locations."""

    base_dir = Path(settings.BASE_DIR)
    candidates = [
        base_dir / "questions" / "participants.csv",
        base_dir.parent / "questions" / "participants.csv",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _import_students(rows: Iterable[Mapping[str, str | None]]) -> int:
    """Create or update students based on the provided iterable of CSV rows."""

    from .models import Student

    created_or_updated = 0
    for row in rows:
        name = (row.get("name") or "").strip()
        email = (row.get("email") or "").strip()
        if not name or not email:
            continue

        course = (row.get("course") or "").strip()
        group = (row.get("group") or "").strip()

        obj, created = Student.objects.get_or_create(
            email=email,
            defaults={"name": name, "course": course, "group": group},
        )

        if created:
            created_or_updated += 1
            continue

        updated_fields = []
        if obj.name != name:
            obj.name = name
            updated_fields.append("name")
        if obj.course != course:
            obj.course = course
            updated_fields.append("course")
        if obj.group != group:
            obj.group = group
            updated_fields.append("group")

        if updated_fields:
            obj.save(update_fields=updated_fields)
            created_or_updated += 1

    return created_or_updated


def import_students_from_file(handle: io.TextIOBase) -> int:
    """Import students from an open text file handle."""

    reader = csv.DictReader(handle)
    return _import_students(reader)


def import_students_from_content(content: str) -> int:
    """Import students from CSV content represented as a string."""

    return import_students_from_file(io.StringIO(content))


def sync_students_from_csv(path: Path | None = None) -> int:
    """Populate the Student table from the participants CSV.

    Returns the number of students created or updated.
    """

    participants_path = path or find_participants_csv()
    if not participants_path:
        return 0

    with participants_path.open(encoding="utf-8") as handle:
        return import_students_from_file(handle)

from __future__ import annotations

import csv
import io
import textwrap
from pathlib import Path
from typing import Iterable, List, Mapping

from django.conf import settings
from django.db import transaction
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


@transaction.atomic
def _import_students(rows: Iterable[Mapping[str, str | None]], *, academic_year: str | None = None) -> int:
    """Import one academic year atomically, preserving omitted optional fields."""
    from .models import Student, default_academic_year

    year = Student._meta.get_field("academic_year").clean(
        default_academic_year() if academic_year is None else academic_year, None
    )
    changed = 0
    for row_number, row in enumerate(rows, start=2):
        if None not in row and not any((value or "").strip() for value in row.values()):
            continue
        if None in row:
            raise ValueError(f"CSV row {row_number} has more values than its header.")
        name = (row.get("name") or "").strip()
        email = (row.get("email") or "").strip()
        if not name or not email:
            raise ValueError(f"CSV row {row_number} requires name and email.")
        values = {"name": name}
        for field in ("course", "group"):
            if field in row:
                values[field] = (row[field] or "").strip()
        student, created = Student.objects.get_or_create(
            email=email, academic_year=year, defaults=values
        )
        updated_fields = [field for field, value in values.items() if getattr(student, field) != value]
        for field, value in values.items():
            setattr(student, field, value)
        student.full_clean()
        if updated_fields:
            student.save(update_fields=updated_fields)
        changed += bool(created or updated_fields)
    return changed


def import_students_from_file(handle: io.TextIOBase, *, academic_year: str | None = None) -> int:
    reader = csv.DictReader(handle)
    # Skip spreadsheet export padding before the header (including rows of commas).
    while reader.fieldnames is not None and not any(value.lstrip("\ufeff").strip() for value in reader.fieldnames):
        reader.fieldnames = None
    aliases = {"фио": "name", "курс": "course", "группа": "group"}
    headers = [value.lstrip("\ufeff").strip().lower() for value in (reader.fieldnames or [])]
    reader.fieldnames = [aliases.get(value, value) for value in headers]
    if len(set(reader.fieldnames)) != len(reader.fieldnames) or not {"name", "email"}.issubset(reader.fieldnames):
        raise ValueError("CSV requires unique name (or ФИО) and email columns.")
    return _import_students(reader, academic_year=academic_year)


def import_students_from_content(content: str, *, academic_year: str | None = None) -> int:
    return import_students_from_file(io.StringIO(content), academic_year=academic_year)


def sync_students_from_csv(path: Path | None = None, *, academic_year: str | None = None) -> int:
    participants_path = path or find_participants_csv()
    if not participants_path:
        return 0
    with participants_path.open(encoding="utf-8-sig") as handle:
        return import_students_from_file(handle, academic_year=academic_year)

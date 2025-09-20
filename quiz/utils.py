from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Iterable, Mapping

from django.conf import settings

from .models import Student


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

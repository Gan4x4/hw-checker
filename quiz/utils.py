from __future__ import annotations

import csv
from pathlib import Path

from django.conf import settings

from .models import Student


def _participants_path() -> Path | None:
    base_dir = Path(settings.BASE_DIR)
    candidates = [
        base_dir / "questions" / "participants.csv",
        base_dir.parent / "questions" / "participants.csv",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def sync_students_from_csv() -> int:
    """Populate the Student table from the participants CSV.

    Returns the number of students created or updated.
    """

    participants_path = _participants_path()
    if not participants_path:
        return 0

    created_or_updated = 0
    with participants_path.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
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

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError

from quiz.utils import (
    find_participants_csv,
    sync_students_from_csv,
)


class Command(BaseCommand):
    help = "Import students from a CSV file."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--academic-year", help="Academic year (YYYY_YY); defaults to DEFAULT_ACADEMIC_YEAR.")
        parser.add_argument(
            "csv_path",
            nargs="?",
            help=(
                "Optional path to the CSV file. If omitted, the command looks for "
                "questions/participants.csv relative to BASE_DIR."
            ),
        )

    def handle(self, csv_path: str | None = None, **options) -> None:
        path = Path(csv_path).expanduser() if csv_path else find_participants_csv()
        if not path:
            raise CommandError(
                "participants.csv not found. Provide a path or place the file under "
                "questions/participants.csv."
            )

        try:
            created = sync_students_from_csv(path, academic_year=options["academic_year"])
        except (OSError, ValueError, ValidationError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Imported or updated {created} student(s) from {path}."
            )
        )

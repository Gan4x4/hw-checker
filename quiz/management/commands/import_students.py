from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from quiz.utils import (
    find_participants_csv,
    import_students_from_file,
    sync_students_from_csv,
)


class Command(BaseCommand):
    help = "Import students from a CSV file."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "csv_path",
            nargs="?",
            help=(
                "Optional path to the CSV file. If omitted, the command looks for "
                "questions/participants.csv relative to BASE_DIR."
            ),
        )

    def handle(self, csv_path: str | None = None, **options) -> None:
        if csv_path:
            path = Path(csv_path).expanduser()
            if not path.exists():
                raise CommandError(f"File not found: {path}")

            with path.open(encoding="utf-8") as handle:
                created = import_students_from_file(handle)
            self.stdout.write(
                self.style.SUCCESS(f"Imported or updated {created} student(s) from {path}.")
            )
            return

        default_path = find_participants_csv()
        if not default_path:
            raise CommandError(
                "participants.csv not found. Provide a path or place the file under "
                "questions/participants.csv."
            )

        created = sync_students_from_csv(default_path)
        self.stdout.write(
            self.style.SUCCESS(
                f"Imported or updated {created} student(s) from {default_path}."
            )
        )

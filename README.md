# Homework Checker

## Running the development server

Activate your virtualenv and dependencies, then launch Django with:

```bash
source .venv/bin/acvtvate
python manage.py runserver
```

## Restarting the production service

After deploying changes on the server, restart the Gunicorn service to apply them:

```bash
sudo systemctl restart hwchecker
```

## Importing students from CSV

- Command line: `python manage.py import_students` (reads `questions/participants.csv` relative to the project). Provide a custom file with `python manage.py import_students /path/to/file.csv`.
- Admin UI: open the Django admin “Students” list (`/admin/quiz/student/`) and use the upload form at the top to import a CSV with the columns `name,email,course,group`.

## Importing question files into a test

- Open the Django admin Tests list (`/admin/quiz/test/`) and click the test that should receive the questions.
- Scroll to the **Import question files** panel, choose one or more JSON files, and press **Import files**. Each file is assigned to the student inferred from its filename and creates a quiz that is automatically bound to the current test.
- Files whose names don’t match any known student (or whose student already has a quiz in that test) are skipped with a status message. Use the existing management command `python manage.py import_questions path/to/file.json` if you just want to create a standalone quiz.

## Applying migrations on the production server

When running management commands directly on the server you must load the same environment variables the service uses. From the project directory, run:

```bash
set -a
source .env
set +a
python manage.py migrate
```

That exports everything defined in `.env` (including `DATABASE_URL`, `SECRET_KEY`, etc.), so migrations run against the real production database.

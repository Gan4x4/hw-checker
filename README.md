# Homework Checker

Homework Checker is a Django-based oral-defense style assessment system for programming homework.

The main idea is simple: when students can use LLMs to generate code quickly, classic "submit and grade" workflows are not enough to verify authorship. This project checks understanding instead of only checking output.

Questions are generated per student submission (typically by a companion tool such as `hw-question-generator`) and then imported into Homework Checker. During the quiz, a student must answer fast, code-specific multiple-choice questions. This format makes it much easier to distinguish real understanding from copied code.

## What the project does

- Runs individual quiz sessions via unique token links.
- Supports per-question time limits (global default and per-test override).
- Renders each question as an image (question text + optional code snippet + source) to reduce trivial copy/paste cheating.
- Randomizes answer option order on each question page.
- Automatically records unanswered questions when time expires.
- Tracks per-attempt metadata: selected answer, correctness, and time spent.
- Computes final score and detailed per-question results for students.
- Supports optional student feedback per question after quiz completion.

## Admin and instructor workflow

- Import students from CSV (CLI command and admin upload form).
- Import quiz JSON files (single import page or bulk import into a test).
- Auto-match uploaded quiz files to students using filename token matching.
- Build tests from selected quizzes, set test duration, and start/reset test windows.
- Restrict access by test state (`draft`, `active`, `finished`).
- Export personalized quiz links for distribution.
- Review quiz results in admin, disable problematic questions with a required comment, and re-enable later.
- Export disabled questions and exported feedback as JSON for post-analysis.

## JSON question format (import)

Each question item supports fields such as:

- `question`
- `answers`
- `correct_answer_index` (or mark one answer with `*`)
- `code_snippet`
- `explanation`
- `teacher_note`
- `weight` / `penalty`
- `source`




## Running the development server

Activate your virtualenv and dependencies, then launch Django with:

```bash
source .venv/bin/activate
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

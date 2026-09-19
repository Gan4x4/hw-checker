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

Use Python 3.10, matching production. The dependency pins reproduce the current
deployment; Django 4.1 is unsupported and needs a separate framework upgrade.
Create a virtualenv and a local environment file (do not overwrite an existing `.env`):

```bash
python3.10 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

Load the environment before management commands:

```bash
source .venv/bin/activate
set -a
source .env
set +a
python manage.py migrate
python manage.py runserver
```

`./run.sh` loads `.env`, uses the project's virtualenv, and starts the development
server. It works from any directory and accepts runserver arguments, for example
`./run.sh 127.0.0.1:8001`. Keep its terminal open; Ctrl+C stops the server.

Run the server on the same machine as the browser: `127.0.0.1` always means the
machine where the browser runs. In this setup, Firefox is on **P15s**, while a
terminal showing `gan@xeon` is an SSH session on another machine. For local work,
open a terminal on P15s (`hostname` should print `P15s`), then:

```bash
cd ~/Code/HSE/hw-checker
./run.sh
```

Open **http://127.0.0.1:8000/** (HTTP, not HTTPS). If deliberately running on xeon,
start the server there and open an SSH tunnel in a separate P15s terminal:

```bash
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8000:127.0.0.1:8000 -p 2800 gan@46.188.19.112
```

The tunnel uses P15s port 8000, so do not also run a local server on that port.
Ctrl+C in the tunnel terminal closes the connection; Ctrl+C in the xeon server
terminal stops Django.

To check the
baseline, use `python manage.py test` and `python manage.py makemigrations --check --dry-run`
with the same environment loaded. Tests use a separate SQLite test database by
default; do not load the production database URL to run tests.

## Production configuration

Production defaults to `DEBUG=False` and requires a random `SECRET_KEY`. Set these
values explicitly in the service's private `.env`:

```dotenv
DEBUG=False
SECRET_KEY=<random-secret-generated-on-the-server>
ALLOWED_HOSTS=hw.cv.gan4x4.ru
TRUST_PROXY_SSL_HEADER=True
# Preserve the existing DATABASE_URL and quiz configuration.
```

Keep `.env` readable only by its owner (`chmod 600 .env`). The proxy setting is
safe here because Nginx overwrites `X-Forwarded-Proto` and connects to Gunicorn over
a Unix socket. Do not enable it behind a proxy that accepts that header unchanged.
Production enables HTTPS redirects, secure session/CSRF cookies, and one-hour
HSTS for this hostname only. Development with `DEBUG=True` supports local HTTP.

After loading the production environment, run `python manage.py check --deploy`
before restarting. Warnings W005 and W021 are expected: HSTS intentionally excludes
subdomains and browser preload enrollment. Resolve any other warnings.
Rotating `SECRET_KEY` invalidates existing
sessions; schedule it outside active quizzes. Back up settings and `.env` privately
before changes. Migration `0010_quizquestionfeedback` was already applied on the
server and is now included here; it adds a proxy model, not a database table.

## Restarting the production service

After deploying changes on the server, restart the Gunicorn service to apply them:

```bash
sudo systemctl restart hwchecker
```

For the current service only, `Restart=always` also allows its owner (`tor`) to
gracefully terminate the Gunicorn master and let systemd restart it after five
seconds. Verify that policy and the master PID with `systemctl show hwchecker
-p Restart -p MainPID` first, then send `kill -TERM <MainPID>`. This causes a brief
outage and reloads `.env`; a Gunicorn HUP does not reload the systemd environment.
Verify `systemctl is-active hwchecker` and the HTTPS home/admin pages afterward.

## Importing students from CSV

- Command line: `python manage.py import_students` (reads `questions/participants.csv` relative to the project). Provide a custom file with `python manage.py import_students /path/to/file.csv`.
- Admin UI: open the Django admin “Students” list (`/admin/quiz/student/`), choose the academic year, and upload a CSV. Name/email are required; course/group are optional. English headers and `ФИО,email,Курс,Группа` are accepted, including leading blank rows. Omitted optional columns preserve existing values; invalid rows roll back the import.
- Existing students, tests, and quizzes belong to `2025_26`. New records/imports default to `2026_27` (configurable with `DEFAULT_ACADEMIC_YEAR`). The academic-year marker is separate from the existing study-year `course` field. The same email can have a separate student record in each academic year.
- Both import commands accept `--academic-year YYYY_YY`, for example `python manage.py import_students development/students_2026_27.csv --academic-year 2026_27`. Students are imported explicitly; opening the quiz-import page does not import a legacy CSV.

## Importing question files into a test

- Open the Django admin Tests list (`/admin/quiz/test/`) and click the test that should receive the questions.
- Scroll to the **Import question files** panel, choose one or more JSON files, and press **Import files**. Each file is assigned to the student inferred from its filename and creates a quiz that is automatically bound to the current test.
- Files are matched only against students in the test's academic year. Unknown/ambiguous names and students who already have a quiz in the test are skipped. Imported quizzes inherit the test's year. Creating a test from selected quizzes requires one shared year.
- For a standalone quiz use `python manage.py import_questions path/to/file.json --academic-year 2026_27`, or select the year in the admin import form. `--replace` affects only the selected year and preserves questions shared with other years.

## Applying migrations on the production server

When running management commands directly on the server you must load the same environment variables the service uses. From the project directory, run:

```bash
set -a
source .env
set +a
python manage.py migrate
```

That exports everything defined in `.env` (including `DATABASE_URL`, `SECRET_KEY`, etc.), so migrations run against the real production database.

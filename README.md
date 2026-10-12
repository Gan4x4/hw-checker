# Homework Checker

Timed, per-student quizzes for checking understanding of programming homework.
Import generated questions, run a test, review results and feedback, and export
scores or feedback for analysis.

## Run locally

Use Python 3.10. On first setup (keep an existing `.env`):

```bash
python3.10 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
set -a; source .env; set +a
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
./run.sh
```

Open http://127.0.0.1:8000/admin/ on the same machine. Keep the terminal open;
Ctrl+C stops the server.

## Instructor workflow

1. Import students from CSV in **Students**, choosing the academic year.
2. Create a test and import its students' question JSON files.
3. Set duration/timeouts, distribute quiz links, and start the test.
4. Open the test to review scores, incorrect answers, and feedback, or download
   scores CSV and feedback JSON.

## API clients

As a superuser, open **API clients** in admin. Add a named client, save, and copy its token. Every enabled client can access
all tests, including future tests and every academic year. Disable the client to revoke access;
use **Rotate tokens** in the list to replace a token.

One client token works for listing tests and downloading their scores/feedback:

```bash
curl --fail -H "Authorization: Bearer $QUIZ_API_TOKEN" \
  https://hw.cv.gan4x4.ru/api/tests/
curl --fail -H "Authorization: Bearer $QUIZ_API_TOKEN" \
  'https://hw.cv.gan4x4.ru/api/feedback.json?test_ids=1,2' -o feedback.json
```

Omit `test_ids` to download feedback for all tests.
Existing private per-test download links still work.

## Details

- [LLM API instructions](doc/llm-api.md): authentication, endpoints, response handling.
- [Feedback format](doc/feedback-format.md): snapshots, provenance, historical limitations.
- [Development and deployment](doc/operations.md): configuration, imports, tests, backups and restarts.

Run checks with `DEBUG=True .venv/bin/python manage.py test quiz`.

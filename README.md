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

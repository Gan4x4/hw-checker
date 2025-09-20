# Homework Checker

## Running the development server

Activate your virtualenv and dependencies, then launch Django with:

```bash
python manage.py runserver
```

## Importing students from CSV

- Command line: `python manage.py import_students` (reads `questions/participants.csv` relative to the project). Provide a custom file with `python manage.py import_students /path/to/file.csv`.
- Admin UI: open the Django admin “Students” list (`/admin/quiz/student/`) and use the upload form at the top to import a CSV with the columns `name,email,course,group`.

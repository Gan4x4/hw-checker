# Academic-year filtering and imports

- Add an academic-year marker to students, tests, and quizzes, with admin filters. Keep the existing study-year (`course`) field.
- Mark existing records `2025_26`; default new records and imports to `2026_27`, with an explicit year override.
- Import student CSVs with required name/email and optional course/group, including `students_2026_27.csv` (blank first row; `ФИО,email,Курс` headers).
- Keep student records separate by email and academic year. Match imported quizzes within that year; imports into a test use the test's year.

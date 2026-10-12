# Feedback export format and legacy links

For client authentication and batch downloads, see [LLM API instructions](llm-api.md).

## Feedback JSON export

Open a test in the admin and use **Download feedback JSON**, or take its private
CSV link and replace `results.csv` with `feedback.json`:

```text
GET /test-results/<results_token>/feedback.json
```

The random UUID is the same bearer credential used by the scores export. No login
or additional authorization header is required. Keep the URL private. Numeric
test IDs do not grant access; unknown tokens return 404 and POST returns 405.
Responses use `X-Robots-Tag: noindex, nofollow, noarchive`, `Cache-Control:
private, no-store`, and `Referrer-Policy: no-referrer`. The scores CSV is unchanged.

An agent can download it with `curl --fail "$FEEDBACK_URL" -o feedback.json`.
The response contains:

- `schema_version`: `1`.
- `test_id`: the database test ID.
- `questions`: an object keyed by question-version references. Each snapshot has
  `question_id` (stable database ID), `question`, `code_snippet`, `answers`,
  zero-based `correct_answer_index`, `correct_answer`, `explanation`,
  `provenance`, and `snapshot_source`. Snapshots captured during display also
  include `answer_order`, the shuffled display order as indexes into `answers`.
- `feedback`: submitted question comments, each with `id`, `text`, `submitted_at`
  (ISO 8601), `question_ref` (a key in `questions`), `rating`, and `category`.
- `general_feedback`: the same comment fields with `question_ref: null`.

No student names, emails, quiz titles, or student access tokens are added to the
export. Comment text is preserved, so it can contain information written by the
participant. Rating and category are `null` because the service does not collect
them. An empty test returns `questions: {}`, `feedback: []`, and
`general_feedback: []`. Completion is not required; multiple submissions are
retained, even if the latest comment is cleared or the question is disabled.
Question content is stored once per distinct snapshot in the exported document.

Question snapshots are captured when the question is displayed and copied into
each feedback record, so later question edits do not change that feedback's
content. `snapshot_source: "shown"` identifies those snapshots. Comments submitted
without a captured display use `"current"`. Migration 0013 preserves existing
non-disabled comments with `submitted_at: null` and
`snapshot_source: "historical_current"`: the originally displayed text, earlier
overwritten comments, and submission times cannot be reconstructed. Disabled
comments are instructor reasons and are not backfilled as student feedback.

Imports accept a `provenance` object at the JSON root and/or per question (question
values override root values). The same fields may instead appear directly on
those objects: `generation_run_id`, `assignment_id`, `analyzer_model_version`,
`questioner_model_version`, `prompt_versions`, `prompt_hashes`, and
`original_question_ref`. Values may be strings, integer identifiers, or objects
(for example `"prompt_hashes": {"questioner": "sha256:..."}`). Missing metadata
exports as `null`, never as a value inferred from current settings.

The existing CSRF-protected form POST to
`/quiz/<quiz_token>/feedback/<quiz_question_id>/` saves question feedback and now
accepts unfinished quizzes. A form POST with `comment` to
`/quiz/<quiz_token>/feedback/` saves general feedback and returns JSON; there is
no new general-feedback form in the student UI. Both require the existing student
quiz token, and question IDs must belong to that quiz. Existing trimming and the
2,000-character comment limit still apply. Export endpoints are read-only.

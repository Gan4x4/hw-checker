# Quiz Service: Feedback Export Requirements

## Problem

The current `results.csv` endpoint exports only student names and final scores. It omits feedback, so it cannot support improvements to question-generation prompts.

## Required export

Provide a read-only endpoint that exports all submitted feedback for a test, including:

- Test ID and stable question ID.
- Feedback text, submission time, and rating/category if collected.
- The question as shown: text, code snippet, answer options, correct answer, and explanation.
- A question reference for question-specific feedback; preserve general test feedback separately.

Include questions with feedback even when the student did not finish the test. Return an explicit empty feedback list when no feedback exists.

## Recommended approach

Keep the existing scores CSV and add a JSON feedback export, for example:

`GET /test-results/{test_id}/feedback.json`

JSON accommodates question details and multiple feedback entries without duplicating question content. Document the endpoint, response fields, and authentication method so an agent can retrieve it programmatically.

Apply the service's existing access controls. Student names are unnecessary for prompt analysis; omit them from this export.

## Generation provenance

Where supplied during question import, retain and export:

- Generation run ID and assignment ID.
- Analyzer and questioner model versions.
- Prompt versions or hashes.
- Original question reference used to link back to generation artifacts.

Return missing historical metadata as unknown; do not infer it from current settings.

## Acceptance checks

- Submitted feedback appears in the export with the correct test and question.
- Multiple comments, multiline text, and Unicode survive export.
- Question details match the version the student answered.
- General feedback and feedback from unfinished attempts are retained.
- Tests without feedback return an empty collection.
- Existing scores export and access controls continue to work.

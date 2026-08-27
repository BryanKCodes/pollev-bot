# PollEV fixture capture

Step 1 requires real response shapes because this fork currently knows only the
multiple-choice participant route. Capture these from a PollEV account and host
you are authorized to use. Use synthetic question text and options so the
fixture does not contain coursework, participant data, or credentials.

## Capture workflow

1. Create or select one private multiple-choice test activity and one private
   open-ended test activity.
2. Open the participant page in a browser, sign in if required, and enable
   **Preserve log** in DevTools → Network.
3. Activate/answer the multiple-choice activity manually, then do the same for
   the open-ended activity with a harmless test response.
4. Export the Network log as a HAR file. Do not commit the raw HAR file.
5. Run the dependency-free redactor from the repository root:

   `python3 scripts/redact_pollev_har.py /path/to/capture.har fixtures/redacted_capture.json`

6. Inspect the generated JSON manually. Remove any remaining personal text,
   session data, participant identifiers, or unexpected secrets before adding it
   to version control.

The redactor preserves request methods, sanitized paths, field names, status
codes, and JSON structure. It redacts cookies, authorization/CSRF values,
passwords, tokens, and opaque IDs. It intentionally does not rewrite ordinary
question or response text, which is why synthetic content and manual review are
required.

## Required evidence from the capture

For each activity, record the entries corresponding to:

- Activity discovery/firehose message and the ID it yields.
- Activity fetch response, including the type marker, question/prompt field,
  and—if MCQ—the ordered option text and option IDs.
- MCQ submission request: method, route, CSRF behavior, and option-ID field.
- Open-ended submission request: method, route, CSRF behavior, and text field.
- HTTP status and representative response body for both submission types.

Do not assume that an activity with no `options` field is open-ended; PollEV has
other non-MCQ activity types. If the capture does not contain an explicit type
marker, note that fact and retain the endpoint/schema evidence used to classify
the activity.

## Expected deliverable

Commit one reviewed `fixtures/redacted_capture.json` plus a short note in the
implementation PR identifying the verified type field and the open-ended fetch
and submit routes. The fixture should be sufficient to write parser and
submission tests without contacting PollEV.

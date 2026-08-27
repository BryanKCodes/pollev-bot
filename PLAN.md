# Plan: LLM-backed PollEV responses

## Goal

Extend the existing PollEV bot so it can:

1. Identify the supported question type from PollEV activity data.
2. Ask a small local instruct model for the best multiple-choice selection.
3. Ask the same model for a concise, one-sentence open-ended response.
4. Submit each answer through the correct PollEV participant workflow.

This plan intentionally does not include implementation code.

## Current architecture

- `pollevbot/pollbot.py` owns authentication, polling, activity discovery, poll retrieval, answer selection, and submission.
- `get_new_poll_id()` reads one firehose message, extracts `uid`, and suppresses IDs already in `answered_polls`.
- `answer_poll()` calls the multiple-choice participant endpoint, reads `poll_data["options"]`, chooses randomly, and submits `option_id`.
- `pollevbot/endpoints.py` contains the private/session endpoints used by this fork.
- `main.py`, `clock.py`, and `herokuapp.py` are launchers; the latter two configure credentials, timing, and lifetime from environment variables.
- There is no test suite, package metadata, model abstraction, or structured activity model.
- `runtime.txt` pins Python 3.8.2 and `requirements.txt` contains only the web/scheduler dependencies.

The current `poll_data` and response route are explicitly multiple-choice-specific. Therefore, this is a small orchestration change but not merely a replacement for `random.choice()`.

## Recommended design

### 1. Add a normalized activity model

Introduce a small internal representation, conceptually containing:

- Poll/activity ID.
- Supported kind: `multiple_choice`, `open_ended`, or `unsupported`.
- Clean question text.
- Ordered options for multiple-choice activities, retaining both display text and PollEV option ID.
- Raw response data only where needed for endpoint compatibility.

Keep PollEV JSON parsing in a transport/normalization layer. Keep answer generation unaware of PollEV field names, URLs, CSRF handling, and opaque IDs.

### 2. Detect type from metadata, not only from option presence

Use the activity/poll type field returned by PollEV when available. Confirm its exact name and values with captured responses from a controlled test account. As a compatibility fallback, use the endpoint/schema that successfully returned the activity, but do not classify every payload without options as open-ended: Q&A, word cloud, ranking, clickable image, surveys, and newer PollEV activity types may also have no MCQ options.

Recommended behavior:

- Explicit MCQ metadata plus a non-empty option list: answer as MCQ.
- Explicit open-ended/free-text metadata: answer as open-ended.
- Missing or unknown type: log a redacted diagnostic and skip rather than submit to an uncertain route.
- Unsupported type: skip cleanly and continue polling.

Poll Everywhere documents multiple-choice and open-ended as separate activity types, and documents additional types such as Q&A, word cloud, ranking, and clickable image. This makes an explicit unsupported state important. See [Poll Everywhere activity types](https://support.polleverywhere.com/hc/en-us/articles/1260801552990-How-to-Create-Activities).

### 3. Separate answer generation behind a provider interface

Add an answerer module with two operations conceptually equivalent to:

- Select one option from a supplied ordered list.
- Produce a short response from supplied question text.

The PollBot should pass normalized text/options to this provider and receive a typed result. It should never accept an arbitrary model string as an option ID.

This boundary allows local inference now and a remote/OpenAI-compatible backend later without changing PollEV request code. It also makes deterministic fake answerers possible for tests.

### 4. Use `llama-cpp-python` locally, in-process

The primary recommendation is `llama-cpp-python` with a GGUF instruct model:

- No hosted LLM API or per-request charge.
- It can load and run the model in the bot process.
- Its current documentation lists Python 3.8+ support and CPU installation options.
- The official Qwen GGUF repository provides a direct `llama-cpp-python` usage path and quantized variants.

Start evaluation with Qwen2.5-1.5B-Instruct in a Q4_K_M-sized GGUF variant. It is a reasonable quality/footprint compromise for general questions and has an official GGUF distribution. Also benchmark Qwen3-0.6B if the target machine is especially constrained; its smaller size may reduce latency and memory but may be weaker on difficult questions and strict formatting.

Use lazy importing and a separate `requirements-local.txt` so the existing lightweight Heroku dependency set is not forced to compile or download a model. Store the model outside the repository and select it with configuration. Do not download a multi-gigabyte model on every bot start.

Ollama is a good development alternative, but it is a separate local service rather than an in-process library. Its local API requires no authentication, but it adds a daemon/model lifecycle dependency. Use it only if operational convenience outweighs the extra process. See [Ollama local API](https://github.com/ollama/ollama/blob/main/docs/api/introduction.mdx) and [llama-cpp-python installation](https://github.com/abetlen/llama-cpp-python/blob/main/README.md).

### 5. Make MCQ generation constrained and validation-first

The MCQ prompt should include only the cleaned question and ordered option text. Instruct the model to return exactly one option index or letter and nothing else. Use deterministic sampling, a very small output limit, and—if supported by the selected runtime—grammar/structured-output constraints.

Validate the result before submission:

- Parse a letter/index only.
- Check that it maps to an option in the permitted range.
- Resolve the validated position back to the original PollEV option ID.
- Retry once with a stricter prompt if parsing fails.
- If it still fails, skip submission by default; retain random selection only as an explicitly configured legacy fallback.

Preserve `min_option`/`max_option` as an optional candidate filter, but pass the same filtered ordering to the model and use the corresponding original option ID. The default for LLM mode should consider all options.

### 6. Make open-ended generation short and safe to submit

The open-ended prompt should request one direct sentence, no preamble, no explanation of the task, and no model commentary. Set a low generation limit and a deterministic temperature. Normalize whitespace, remove accidental answer labels or markdown, reject empty output, and enforce a configured character limit before submission. Prefer a retry or skip over submitting malformed model output.

The exact PollEV open-ended fetch and submission route/payload must be verified first. This fork only contains `multiple_choice_polls/{uid}` and its `results` route. Discover the open-ended route by inspecting the response-page network calls for an authorized test activity or by using an officially supported API if the account has access; do not guess based on the multiple-choice URL. Poll Everywhere’s documentation confirms that open-ended responses are written text and that response length/display limits vary by presentation mode. See [open-ended questions](https://support.polleverywhere.com/hc/en-us/articles/1260801546510-Open-ended-question).

### 7. Refactor `PollBot` into a small dispatch flow

Keep the existing login and firehose polling behavior initially. Change the answer path to:

1. Receive a new activity ID.
2. Fetch and normalize its activity data.
3. Dispatch on normalized kind.
4. Generate a validated MCQ choice or open-ended text.
5. Submit using a kind-specific responder.
6. Record success and log only safe metadata.

Move the answered-ID bookkeeping so a poll is not permanently marked answered before fetching, inference, and submission succeed. Add a bounded retry/backoff policy for transient fetch, model, CSRF, and submission failures. Avoid duplicate submissions by keeping an in-flight/attempted state and honoring PollEV’s response behavior.

Use explicit request timeouts for all network calls and an inference timeout or bounded generation setting so one unavailable model cannot stall the polling loop.

## Configuration changes to plan

Add environment/configuration controls rather than hard-code model behavior:

- `ANSWER_MODE`: `llm`, `random`, or `skip`.
- `LLM_BACKEND`: initially `llama_cpp`; optionally `ollama` later.
- `LLM_MODEL_PATH` or model repository/file settings.
- Context size, CPU thread count, maximum generated tokens, temperature, and inference timeout.
- Maximum open-ended answer length.
- Behavior on inference failure: default `skip`, optional `random` for backward compatibility.

Update `main.py`, `clock.py`, `herokuapp.py`, `README.md`, and `app.json` consistently. Remove the example’s hard-coded credentials while making that change. Document that an in-process local model is practical on a personal machine or sufficiently provisioned host, but is unlikely to fit the existing small Heroku deployment unchanged. On Heroku, the choices are a separately hosted model service, an authorized API, or retaining a non-LLM fallback.

## Implementation sequence

1. Capture and save redacted MCQ and open-ended payload/response fixtures from a permitted test account; identify exact type fields, text fields, option fields, endpoints, CSRF requirements, and response form fields.
2. Add normalized activity/result types and parsing tests using those fixtures.
3. Add kind-specific PollEV fetch/submit methods while preserving the existing MCQ behavior.
4. Add the answerer interface and a deterministic fake provider.
5. Add the local `llama-cpp-python` provider with lazy loading and configuration validation.
6. Add strict MCQ parsing, option-ID mapping, open-ended cleanup, retry behavior, and safe failure handling.
7. Wire dispatch into `PollBot`, then update launchers and configuration documentation.
8. Evaluate at least two small GGUF models on a representative, permissioned question set before selecting the default. Record accuracy, invalid-output rate, latency, and peak memory.

## Verification plan

- Unit-test type classification, HTML/entity cleanup, option slicing, letter/index parsing, malformed model output, length limits, and unsupported types.
- Test that the submitted MCQ ID is exactly the selected option’s original ID.
- Test that open-ended submissions use the separately verified route and text field.
- Mock HTTP calls to verify CSRF and submission payloads without contacting PollEV.
- Run an end-to-end test against a private test host with one MCQ and one open-ended activity.
- Confirm a failed model call does not silently submit a random answer or lose the poll permanently.
- Measure cold-start model load and per-question latency against `open_wait`; adjust polling timing accordingly.
- Verify the existing random mode still behaves as before when explicitly selected.

## Acceptance criteria

- MCQ and open-ended activities are distinguished using verified PollEV metadata/schema.
- Unknown and unsupported activity types are skipped safely.
- MCQ answers are model-selected, validated, and mapped to the correct PollEV option ID.
- Open-ended answers are one short sentence, within the configured limit, and submitted through the verified open-ended workflow.
- The default local configuration requires no hosted LLM API.
- The model is loaded once per process, not once per poll.
- Existing authentication, scheduling, and random-answer compatibility remain functional.
- Tests cover parsing, dispatch, provider failures, and submission mapping.

## Risks and decisions to revisit

- The repository relies on undocumented/private PollEV endpoints; PollEV changes may break both current and new activity routes.
- A 0.6B–1.5B model may be fast and inexpensive but will not reliably solve specialized or multi-step questions. Measure quality rather than assuming model size is sufficient.
- CPU inference may exceed the available response window, especially on Heroku-class dynos.
- Model files and native inference dependencies can substantially increase deployment size and startup time.
- Automated answers should be used only for authorized testing, demonstrations, or other contexts where the presenter/instructor permits them.

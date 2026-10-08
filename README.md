# pollevbot

**pollevbot** is a bot that automatically responds to polls on [pollev.com](https://pollev.com/). 
It continually checks if a specified host has opened any polls. Multiple-choice
answers use random selection by default; a local model can be selected instead.

Requires Python 3.8 or later for the local model dependency.

## Dependencies

[Requests](https://pypi.org/project/requests/), 
[BeautifulSoup](https://pypi.org/project/beautifulsoup4/). 

[APScheduler](https://pypi.org/project/APScheduler/) to deploy to Heroku.

## Local desktop launcher (NUS)

Use Python 3.10+ and Google Chrome. On an Apple Silicon Mac, install Python
with Tk support, create the environment, and install the browser and local
inference dependencies:

```sh
brew install python@3.12 python-tk@3.12
python3.12 -m venv .venv
source .venv/bin/activate
CMAKE_ARGS='-DGGML_METAL=on' python -m pip install -r requirements-desktop.txt
python -m pollevbot.gui
```

On other computers, use a Python installation with Tkinter and install
`requirements-desktop.txt` without the Metal flag. Native inference builds may
need a C/C++ compiler; see the
[llama-cpp-python installation instructions](https://github.com/abetlen/llama-cpp-python#installation).
On Linux, Tkinter may need your distribution's `python3-tk` package.
On macOS you can also double-click **Launch PollEV.command** after setup.

The launcher provides:

- Presenter name or full `pollev.com/host` URL.
- LLM, Theme, or Random mode. Theme reveals a course-topic field.
- Numeric duration with minutes or hours. The timer starts after sign-in.
- A **Keep the course browser open** checkbox.
- Model status, a **Download Qwen model** button, and a GGUF file picker.
- Start, Stop, Save settings, and **I checked in** controls.
- One updating status line and an event log for questions, answers, and errors.

Settings are saved locally in `.env` when you click Save settings or Start.
The file is ignored by Git. No username, password, or MFA code is stored.
Start opens a fresh Chrome session: enter your email, complete NUS sign-in and
MFA, and finish the respondent-name prompt if shown. Different users choose
their own account each run. The bot verifies the participant session before
polling. Old `.pollev-auth*` directories are no longer used by the local
launchers and can be removed after stopping any older bot/browser using them.

A temporary Chrome profile holds cookies during the current run, allowing the
bot to reopen the same signed-in session for check-in or a text response. It is
deleted when the run ends. The old `NUS_BROWSER_PROFILE` setting selected a saved
session between runs; a course-specific directory helped isolate that account
and check-in state but could conflict with another Chrome process. Local
launchers now manage this without a profile setting.

With **Keep the course browser open** off, Chrome closes after login and opens
again only if check-in or a text-response form is needed. With it on, the course
window stays open until Stop or the duration expires. If a course asks you to
check in, press its button and allow your real location in Chrome, then click
**I checked in** in the launcher. The bot rechecks course access before
resuming. Location is neither supplied nor emulated by the bot. If the course
requires roster registration, the presenter must add your account.

Keep the machine running with an internet connection. The Mac GUI uses
`caffeinate -i` while the bot runs to prevent idle sleep; closing the lid or
manually sleeping the machine can still interrupt it. Stop waits for a current
request or native model operation to finish, then closes the bot's browser.
Closing the launcher also requests Stop.

## Model setup and answer modes

The default is [Qwen2.5 1.5B Instruct Q4_K_M](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF),
about 1.12 GB. Use the GUI download button or:

```sh
python -m pollevbot.model_setup
```

The downloader uses the publisher's file and verifies SHA256 before making it
available. Your local copy lives in `models/`; GGUF binaries are ignored by Git,
so someone cloning the repository downloads a copy once. `LLM_MODEL_PATH`
defaults to `models/qwen2.5-1.5b-instruct-q4_k_m.gguf`, resolved from the project
root. Absolute paths to other GGUF instruct models also work. On Apple Silicon,
`LLM_GPU_LAYERS=-1` enables full Metal offload; use `0` for CPU inference.
The model loads on the first answer and is reused while the launcher remains
open. Random mode does not need a model.

LLM mode uses visible questions to choose MCQ answers or generate short text.
If the MCQ question is hidden, it falls back to a random option. Theme mode uses
a course topic such as `nlp transformers` to generate a two or three word text
phrase, including when the title is hidden; it uses random MCQ selection.
Random mode answers MCQs and skips text responses. Presenter-only title text is
not exposed by the participant workflow.

For a text activity without a verified participant JSON route, LLM or Theme
uses the actual visible Chrome form. LLM requires a visible question; Theme can
use a hidden-title form. The bot confirms the current activity ID before
clicking Submit. It records confirmed submissions and never resends an
uncertain submission during that run. Answered IDs are held in memory; starting
a new run resets that bookkeeping.

## Terminal launcher

Set a host in `.env` (see `.env.example`) or save it from the GUI. Run:

```sh
caffeinate -i python -m pollevbot.main
```

The prompt is `Select mode [1. LLM] [2. Theme] [3. Random]:`.
Theme mode then asks for a topic. `--mode theme --theme 'nlp transformers'`
bypasses prompts; noninteractive runs use `ANSWER_MODE` and `ANSWER_THEME`.
`LIFETIME` is the polling duration in seconds (default 3600). Local login defaults
to NUS and always opens a fresh sign-in session. With `NUS_KEEP_BROWSER_OPEN=false`,
the browser closes when it is not needed. For check-in, complete the browser step
and press Enter in the terminal. Press Ctrl+C to stop.

The terminal refreshes one timestamped idle line. Activity events log the
available question, selected option or generated text, submission result,
retries, and skips. Redirected output receives an idle update about once a
minute. Verify login without polling or answering using:

```sh
python -m pollevbot.main --check-login
```

When a host is idle, sign-in can be verified while live activity access remains
unverified. The legacy UW/Poll Everywhere launchers still use `USERNAME`,
`PASSWORD`, and `LOGIN_TYPE=uw` or `pollev` from their environment. Use separate
configuration for those launchers; the NUS GUI removes obsolete local password
and saved-profile settings when saving.

## Advanced answer settings

The provider accepts these environment settings:

| Setting | Default | Purpose |
| --- | --- | --- |
| `LLM_BACKEND` | `llama_cpp` | The supported local backend. |
| `LLM_MODEL_PATH` | `models/qwen2.5-1.5b-instruct-q4_k_m.gguf` | GGUF path relative to the project root, or absolute. |
| `LLM_CONTEXT_SIZE` | `2048` | Model context size. |
| `LLM_THREADS` | half the CPU count | CPU inference threads. |
| `LLM_GPU_LAYERS` | `0` | GPU layers; `-1` requests all layers. |
| `LLM_MCQ_MAX_TOKENS` | `8` | Maximum tokens for a choice. |
| `LLM_OPEN_MAX_TOKENS` | `64` | Maximum tokens for a text answer. |
| `LLM_TEMPERATURE` | `0` | Sampling temperature. |
| `LLM_INFERENCE_TIMEOUT` | `30` | Soft deadline between generated tokens, in seconds. |
| `LLM_MAX_OPEN_CHARS` | `280` | Maximum cleaned text length. |
| `LLM_FAILURE_POLICY` | `skip` | `skip` or explicit MCQ `random` fallback. |
| `LLM_SEED` | `0` | Sampling seed. |

For multiple choice, pass the same ordered, optionally sliced candidates to
the provider and to `resolve_option_id` in `pollevbot.answer_validation`.
The provider returns a position, never a Poll Everywhere option ID. Invalid
model output is retried once with a stricter prompt; an invalid second answer
is skipped unless the random fallback is explicitly enabled. Open-ended text
is cleaned and checked against the character limit before it can be used.
The deadline can stop generation between tokens; it cannot interrupt one
blocking native model operation. The exact Poll Everywhere open-ended fetch
and submission route is still unverified. When the current NUS Chrome page shows
one unanswered text form, the bot uses that form rather than guessing a private
submission URL. Otherwise it skips the activity unless a separately verified
`OpenEndedTransport` is injected.

`PollBot` fetches and classifies each activity, then passes its cleaned question
and ordered candidate text to the selected provider. `MIN_OPTION` and
`MAX_OPTION` optionally filter the candidate list (zero-based, inclusive and
exclusive respectively); the selected position is mapped to its original
Poll Everywhere option ID before submission. Local LLM mode considers all
options by default. The Heroku Scheduler launcher retains its older first-three
options default in random mode; set `MAX_OPTION` to override it.

| Setting | Default | Purpose |
| --- | --- | --- |
| `ANSWER_MODE` | `random` | Noninteractive mode: `llm`, `theme`, `random`, or `skip`. |
| `ANSWER_THEME` | unset | Default theme prompt; required for noninteractive theme mode. |
| `MIN_OPTION` | `0` | First candidate index. |
| `MAX_OPTION` | all options | Exclusive end of candidate slice. |
| `OPEN_WAIT` | `5` seconds | Delay before a new activity is answered (`10` on Heroku Scheduler). |
| `CLOSED_WAIT` | `5` seconds | Delay between idle checks. |
| `POLL_REQUEST_TIMEOUT` | `15` seconds | Fetch, CSRF, and submit request timeout. |
| `POLL_RETRY_LIMIT` | `3` | Maximum attempts for fetch, generation, and explicit rate limits. |
| `POLL_RETRY_BACKOFF` | `2` seconds | Initial retry delay, doubled per retry. |

Only a confirmed submission is recorded as answered. Transient failures before
submission receive bounded retries; a submission timeout or unclear server
response is held as attempted and is not resent, to avoid duplicate answers.
The `--check-login` command checks the connection without loading a model.
The standard Heroku dependency set has no local inference package or GGUF
model; `ANSWER_MODE=llm` needs a separately provisioned host with both. A
small Heroku dyno is unlikely to fit an in-process model unchanged. For Heroku,
use the existing random/skip mode, or separately build and provision an
authorized model service or API backend before selecting it in code.

## Verification

Run the isolated unit and mocked HTTP tests with
`python -m unittest discover -s tests -v`. These tests never contact Poll
Everywhere. `python -m scripts.benchmark_local_model /path/to/questions.json`
can measure cold model load, answer latency, invalid output rate, accuracy on
a permissioned JSON question
set, and peak memory after a local GGUF model is installed. Compare its latency
with `OPEN_WAIT` before using the model in a live poll.
Each JSON item needs `kind` (`multiple_choice` or `open_ended`) and `question`;
MCQ items also need ordered `options` and may include a zero-based
`expected_index` for accuracy. Keep private question sets outside this repo.

A live end-to-end test requires a private host with a multiple-choice activity
and a separately verified open-ended fetch/submission route. This repository
does not yet have such a route or captured fixture. The mocked open-ended tests
exercise an injected transport contract, not a Poll Everywhere endpoint.

## Heroku

**pollevbot** can be scheduled to run at specific dates/times (UTC timezone) using [Heroku](http://heroku.com/):

[![Deploy](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy?template=https://github.com/danielqiang/pollevbot)

Required configuration variables:

* `DAY_OF_WEEK`: [cron](https://apscheduler.readthedocs.io/en/stable/modules/triggers/cron.html) string
specifying weekdays to run pollevbot (e.g. `mon,wed` is Monday and Wednesday).
* `HOUR`: [cron](https://apscheduler.readthedocs.io/en/stable/modules/triggers/cron.html) string
(UTC time) specifying which hours to run pollevbot.
* `LIFETIME`: Time to run pollevbot before terminating (in seconds). Set to `inf` to run forever.
* `LOGIN_TYPE`: Login protocol to use (`uw` or `pollev` on Heroku; `nus` locally).
* `MINUTE`: [cron](https://apscheduler.readthedocs.io/en/stable/modules/triggers/cron.html) string
specifying what minutes to run pollevbot.
* `PASSWORD`: PollEv account password.
* `POLLHOST`: PollEv host name.
* `USERNAME`: PollEv account username.

**Example**

Suppose you want to answer polls made by poll host `teacher123` every Monday and Wednesday 
from 11:30 AM to 12:30 PM PST (6:30 PM to 7:30 PM UTC) in your timezone on your UW account. To do this, set the config 
variables as follows:

* `DAY_OF_WEEK`: `mon,wed`
* `HOUR`: `18`
* `LIFETIME`: `3600`
* `LOGIN_TYPE`: `uw`
* `MINUTE`: `30`
* `POLLHOST`: `teacher123`

Set `USERNAME` and `PASSWORD` as private Heroku config vars for the authorized
test account.

Then click `Deploy App` and wait for the app to finish building. 
**pollevbot** is now deployed to Heroku! 

## Disclaimer

I do not promote or condone the usage of this script for any kind of academic misconduct 
or dishonesty. I wrote this script for the sole purpose of educating myself on cybersecurity 
and web protocol automation, and cannot be held liable for any indirect, incidental, consequential, 
special, or exemplary damages arising out of or in connection with the usage of this script.

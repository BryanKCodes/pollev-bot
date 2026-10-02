# pollevbot

**pollevbot** is a bot that automatically responds to polls on [pollev.com](https://pollev.com/). 
It continually checks if a specified host has opened any polls. Multiple-choice
answers use random selection by default; a local model can be selected instead.

Requires Python 3.8 or later for the local model dependency.

## Dependencies

[Requests](https://pypi.org/project/requests/), 
[BeautifulSoup](https://pypi.org/project/beautifulsoup4/). 

[APScheduler](https://pypi.org/project/APScheduler/) to deploy to Heroku.

## Usage

Clone this repository and install its base dependencies:
```
python -m pip install -r requirements.txt
```

Set `USERNAME`, `PASSWORD`, `POLLHOST`, and `LOGIN_TYPE` in a local `.env` file
or environment.
Use `LOGIN_TYPE=nus` for the browser-based NUS flow; its password is entered
only in the browser. Run `python -m pollevbot.main`. The Python API accepts the
same settings as `PollBot` constructor arguments.

## NUS SSO on a local computer

NUS accounts sign in through a browser and may require MFA. Use Python 3.10 or
newer and Google Chrome, then install the optional browser dependency:

```
pip install -r requirements-nus.txt
```

Set `POLLHOST` to the presenter name from the course URL and `LOGIN_TYPE=nus`
in `.env`. `PASSWORD` is not used for NUS sign-in; enter your credentials only
in the Chrome window opened by the bot. Run a connection check before starting
the polling loop:

```
python -m pollevbot.main --check-login
```

Complete NUS sign-in, MFA, and any respondent name prompt in that window. The
dedicated browser profile in `.pollev-auth/` preserves the SSO session between
runs. The bot checks the Poll Everywhere identity session, participant cookie,
and course host connection before reporting success. Set `NUS_BROWSER_PROFILE`
to use another profile directory or
`NUS_LOGIN_TIMEOUT` to change the 300-second sign-in window. The profile
contains session credentials and is ignored by Git. NUS browser sign-in is
intended for a local computer with a desktop browser, not a Heroku dyno.

## Local answer provider

The local LLM provider is available as `LlamaCppProvider` in
`pollevbot.llama_cpp_provider`. Set `ANSWER_MODE=llm` to use it in `PollBot`,
or use `ANSWER_MODE=random` (the default) or `ANSWER_MODE=skip`. Install the
optional inference dependency with `pip install -r requirements-local.txt`.
Keep a GGUF instruct model outside this repository and set `LLM_MODEL_PATH` to
its absolute path. The provider loads it on the first answer request, then
reuses it in the process. A starting model to evaluate is
[Qwen2.5-1.5B-Instruct GGUF, Q4_K_M](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF).
No model is downloaded automatically.

On an Apple Silicon Mac, create a Python 3.12 environment and build the local
inference package with Metal support:

```
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-nus.txt
CMAKE_ARGS='-DGGML_METAL=on' python -m pip install --no-binary llama-cpp-python -r requirements-local.txt
```

Download a GGUF model from its publisher, verify its checksum, and place it
outside the repository. For the Qwen model linked above, set these values in
`.env` (use your own absolute model path):

```
ANSWER_MODE=llm
LLM_BACKEND=llama_cpp
LLM_MODEL_PATH=/absolute/path/to/qwen2.5-1.5b-instruct-q4_k_m.gguf
LLM_GPU_LAYERS=-1
```

Use `python -m pollevbot.main --check-login` to confirm the NUS session, then
`caffeinate -i python -m pollevbot.main` to keep the Mac awake while the bot
polls. The check-login command does not load the model or submit answers. The
bot loads the model when it first needs an answer. On a new sign-in or expired
session, complete NUS authentication and MFA in the Chrome window; later runs
reuse the saved browser profile while its session remains valid.

The terminal reports when polling starts, then gives one idle status update
about every minute. When an activity arrives, it prints the question, the
selected option or generated text, and whether Poll Everywhere accepted the
response. It reports retries and skips, and distinguishes an uncertain
submission from an accepted one. Press Ctrl+C to stop. An activity is answered
at most once during a single bot run; this bookkeeping is in memory, so a new
run does not remember which activity IDs the previous run handled.

The provider accepts these environment settings:

| Setting | Default | Purpose |
| --- | --- | --- |
| `LLM_BACKEND` | `llama_cpp` | The supported local backend. |
| `LLM_MODEL_PATH` | required | Local GGUF file outside the repository. |
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
and submission route is still unverified. The bot safely skips those activities
unless a separately verified `OpenEndedTransport` is injected; it never guesses
a submission URL or field.

`PollBot` fetches and classifies each activity, then passes its cleaned question
and ordered candidate text to the selected provider. `MIN_OPTION` and
`MAX_OPTION` optionally filter the candidate list (zero-based, inclusive and
exclusive respectively); the selected position is mapped to its original
Poll Everywhere option ID before submission. Local LLM mode considers all
options by default. The Heroku Scheduler launcher retains its older first-three
options default in random mode; set `MAX_OPTION` to override it.

| Setting | Default | Purpose |
| --- | --- | --- |
| `ANSWER_MODE` | `random` | `llm`, `random`, or `skip`. |
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

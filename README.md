# pollevbot

**pollevbot** is a bot that automatically responds to polls on [pollev.com](https://pollev.com/). 
It continually checks if a specified host has opened any polls. Multiple-choice
answers use random selection by default; a local model can be selected instead.

Requires Python 3.7 or later.
## Dependencies

[Requests](https://pypi.org/project/requests/), 
[BeautifulSoup](https://pypi.org/project/beautifulsoup4/). 

[APScheduler](https://pypi.org/project/APScheduler/) to deploy to Heroku.

## Usage

Install `pollevbot`:
```
pip install pollevbot
```

Set your username, password, and desired poll host:
```python
user = 'My Username'
password = 'My Password'
host = 'PollEverywhere URL Extension e.g. "uwpsych"'
```

And run the script.
```python
from pollevbot import PollBot

user = 'My Username'
password = 'My Password'
host = 'PollEverywhere URL Extension e.g. "uwpsych"'

# If you're using a non-UW PollEv account,
# add the argument "login_type='pollev'"
with PollBot(user, password, host) as bot:
    bot.run()
```
Alternatively, clone this repo, set your account details in a local `.env` file,
and run `python -m pollevbot.main`.

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
model; `ANSWER_MODE=llm` needs a separately provisioned host with both.

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
* `PASSWORD`: `yourpassword`
* `POLLHOST`: `teacher123`
* `USERNAME`: `yourusername`

Then click `Deploy App` and wait for the app to finish building. 
**pollevbot** is now deployed to Heroku! 

## Disclaimer

I do not promote or condone the usage of this script for any kind of academic misconduct 
or dishonesty. I wrote this script for the sole purpose of educating myself on cybersecurity 
and web protocol automation, and cannot be held liable for any indirect, incidental, consequential, 
special, or exemplary damages arising out of or in connection with the usage of this script.

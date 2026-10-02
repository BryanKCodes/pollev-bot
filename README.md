# pollevbot

**pollevbot** is a bot that automatically responds to polls on [pollev.com](https://pollev.com/). 
It continually checks if a specified host has opened any polls. Once a poll has been opened, 
it submits a random response. 

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

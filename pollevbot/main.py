import os
import argparse
import sys
from contextlib import nullcontext
from dotenv import load_dotenv

from pollevbot import PollBot
from pollevbot.pollbot import LoginError, RetryablePollError
from pollevbot.nus_auth import NusLoginError
from requests import RequestException
from pollevbot.runtime_config import answer_provider_from_env, bot_options_from_env
from pollevbot.launcher_settings import (BROWSER_PROFILE, ENV_PATH, LauncherSettings,
                                         normalize_host, saved_login_lock)


def _choose_mode(default):
    """Ask for a mode only in an interactive local terminal."""
    if not sys.stdin.isatty():
        return default
    choices = {'1': 'llm', '2': 'theme', '3': 'random',
               'llm': 'llm', 'theme': 'theme', 'random': 'random'}
    while True:
        choice = input('Select mode [1. LLM] [2. Theme] [3. Random]: ').strip().lower()
        if choice in choices:
            return choices[choice]
        print('Choose 1, 2, or 3.')


def main():
    parser = argparse.ArgumentParser(description='Poll Everywhere participant bot')
    parser.add_argument('--check-login', action='store_true',
                        help='verify login and host connection without polling or answering')
    parser.add_argument('--mode', choices=('llm', 'theme', 'random', 'skip'),
                        help='choose an answer mode without the interactive menu')
    parser.add_argument('--theme', help='course theme for theme mode')
    parser.add_argument('--host', help='presenter name or pollev.com URL')
    args = parser.parse_args()
    load_dotenv(ENV_PATH)
    login_type = os.getenv("LOGIN_TYPE", "nus")
    user = os.getenv('USERNAME', '') if login_type.lower() == 'nus' else os.environ['USERNAME']
    password = os.getenv("PASSWORD", "") if login_type.lower() == 'nus' else os.environ["PASSWORD"]
    settings = LauncherSettings.load()
    values = settings.runtime_values() if login_type.lower() == 'nus' else dict(os.environ)
    try:
        host_input = args.host or (settings.host if login_type.lower() == 'nus' else '') or os.getenv('POLLHOST', '')
        if not host_input and sys.stdin.isatty():
            host_input = input('Presenter / host: ')
        host = normalize_host(host_input)
        lifetime = float(os.getenv('LIFETIME', str(settings.lifetime)))
        if lifetime <= 0 or lifetime != lifetime:
            raise ValueError('LIFETIME must be positive seconds.')
    except ValueError as exc:
        parser.error(str(exc))
    except EOFError:
        parser.exit(1, 'Enter a host with --host or save it in the launcher.\n')
    except KeyboardInterrupt:
        print('\nStopped by user.')
        return

    options = bot_options_from_env(values)
    if args.check_login:
        options['answer_mode'] = 'skip'
        options.pop('answer_theme', None)
    else:
        try:
            options['answer_mode'] = args.mode or _choose_mode(options['answer_mode'])
            if options['answer_mode'] == 'theme':
                theme = args.theme or options.get('answer_theme', '')
                if sys.stdin.isatty() and args.theme is None:
                    typed = input('Course theme{}: '.format(
                        ' [{}]'.format(theme) if theme else '')).strip()
                    theme = typed or theme
                if not theme:
                    parser.error('Theme mode needs a theme prompt or ANSWER_THEME.')
                options['answer_theme'] = theme
            else:
                options.pop('answer_theme', None)
        except EOFError:
            parser.exit(1, 'Interactive mode selection needs terminal input.\n')
        except KeyboardInterrupt:
            print('\nStopped by user.')
            return
    try:
        values.update(ANSWER_MODE=options['answer_mode'], ANSWER_THEME=options.get('answer_theme', ''))
        provider = answer_provider_from_env(values)
        lock = saved_login_lock() if login_type.lower() == 'nus' else nullcontext()
        with lock, PollBot(user, password, host, login_type=login_type,
                     lifetime=lifetime,
                     browser_profile=str(BROWSER_PROFILE) if login_type.lower() == 'nus' else None,
                     answer_provider=provider,
                     keep_browser_open=os.getenv('NUS_KEEP_BROWSER_OPEN', str(settings.keep_browser_open)).strip().lower()
                     in ('1', 'true', 'yes', 'on'),
                     login_timeout=float(os.getenv('NUS_LOGIN_TIMEOUT', '300')),
                     **options) as bot:
            if args.check_login:
                bot.login()
                token = bot.get_firehose_token()
                current_activity = bot.get_new_poll_id(token)
                if token or current_activity:
                    print('Poll Everywhere login and host activity feed verified.')
                else:
                    print('Poll Everywhere login verified; the host is idle, so '
                          'live activity access is not yet verified.')
            else:
                bot.run()
    except KeyboardInterrupt:
        print('\nStopped by user.')
    except (LoginError, NusLoginError, RetryablePollError, ValueError, RequestException, RuntimeError) as exc:
        parser.exit(1, 'Poll Everywhere connection failed: {}\n'.format(exc))


if __name__ == '__main__':
    main()

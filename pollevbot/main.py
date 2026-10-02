import os
import argparse
from dotenv import load_dotenv

from pollevbot import PollBot
from pollevbot.runtime_config import bot_options_from_env


def main():
    parser = argparse.ArgumentParser(description='Poll Everywhere participant bot')
    parser.add_argument('--check-login', action='store_true',
                        help='verify login and host connection without polling or answering')
    args = parser.parse_args()
    load_dotenv()
    login_type = os.getenv("LOGIN_TYPE", "uw")
    user = os.getenv('USERNAME', '') if login_type.lower() == 'nus' else os.environ['USERNAME']
    password = os.getenv("PASSWORD", "") if login_type.lower() == 'nus' else os.environ["PASSWORD"]
    host = os.environ["POLLHOST"]

    options = bot_options_from_env()
    if args.check_login:
        options['answer_mode'] = 'skip'
    with PollBot(user, password, host, login_type=login_type,
                 browser_profile=os.getenv('NUS_BROWSER_PROFILE'),
                 login_timeout=float(os.getenv('NUS_LOGIN_TIMEOUT', '300')),
                 **options) as bot:
        if args.check_login:
            bot.login()
            bot.get_firehose_token()
            print('Poll Everywhere login and host connection verified.')
        else:
            bot.run()


if __name__ == '__main__':
    main()

import os
from dotenv import load_dotenv

from pollevbot import PollBot


def main():
    user = os.getenv("USER")
    password = os.getenv("PASSWORD")
    host = os.getenv("HOST")

    # If you're using a non-uw PollEv account,
    # add the argument "login_type='pollev'"
    with PollBot(user, password, host) as bot:
        bot.run()


if __name__ == '__main__':
    main()

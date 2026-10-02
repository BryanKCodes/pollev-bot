from sys import version_info

assert version_info >= (3, 7), "pollevbot requires python 3.7 or later"

from .pollbot import PollBot
import logging

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')

"""Shared launcher settings for answer mode and polling behavior."""

import os
from typing import Mapping, Optional


def bot_options_from_env(env: Optional[Mapping[str, str]] = None,
                         random_max_option: Optional[int] = None,
                         default_open_wait: float = 5) -> dict:
    values = os.environ if env is None else env
    mode = values.get('ANSWER_MODE', 'random').lower()
    max_option = values.get('MAX_OPTION')
    if max_option is None:
        max_option = random_max_option if mode == 'random' else None
    else:
        max_option = int(max_option)
    return {
        'answer_mode': mode,
        'min_option': int(values.get('MIN_OPTION', '0')),
        'max_option': max_option,
        'closed_wait': float(values.get('CLOSED_WAIT', '5')),
        'open_wait': float(values.get('OPEN_WAIT', str(default_open_wait))),
        'request_timeout': float(values.get('POLL_REQUEST_TIMEOUT', '15')),
        'retry_limit': int(values.get('POLL_RETRY_LIMIT', '3')),
        'retry_backoff': float(values.get('POLL_RETRY_BACKOFF', '2')),
    }

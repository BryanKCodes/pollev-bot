"""Shared launcher settings for answer mode and polling behavior."""

import logging
import os
from typing import Mapping, Optional
from .answer_providers import ResilientAnswerProvider

logger = logging.getLogger(__name__)


def with_answer_fallbacks(primary, theme=''):
    if isinstance(primary, ResilientAnswerProvider):
        return primary
    themed = None
    if theme and getattr(primary, 'config', None) is not None:
        from .llama_cpp_provider import ThemeProvider
        themed = ThemeProvider(primary.config, theme)
    return ResilientAnswerProvider(primary, theme, themed)


def answer_provider_from_env(values):
    """Build a local provider from explicit settings without changing os.environ."""
    mode = values.get('ANSWER_MODE', 'random').strip().lower()
    if mode in ('llm', 'theme'):
        from .llama_cpp_provider import LlamaCppProvider
        try:
            primary = LlamaCppProvider.from_env(values)
        except (ValueError, ImportError) as exc:
            logger.warning('Local model unavailable (%s); response fallbacks remain active.',
                           type(exc).__name__)
            primary = None
        return with_answer_fallbacks(primary, values.get('ANSWER_THEME', ''))
    return None


def bot_options_from_env(env: Optional[Mapping[str, str]] = None,
                         random_max_option: Optional[int] = None,
                         default_open_wait: float = 5) -> dict:
    values = os.environ if env is None else env
    mode = values.get('ANSWER_MODE', 'random').strip().lower()
    if mode == 'theme':
        mode = 'llm'
    max_option = values.get('MAX_OPTION')
    if max_option is None:
        max_option = random_max_option if mode == 'random' else None
    else:
        max_option = int(max_option)
    options = {
        'answer_mode': mode,
        'min_option': int(values.get('MIN_OPTION', '0')),
        'max_option': max_option,
        'closed_wait': float(values.get('CLOSED_WAIT', '5')),
        'open_wait': float(values.get('OPEN_WAIT', str(default_open_wait))),
        'request_timeout': float(values.get('POLL_REQUEST_TIMEOUT', '15')),
        'retry_limit': int(values.get('POLL_RETRY_LIMIT', '3')),
        'retry_backoff': float(values.get('POLL_RETRY_BACKOFF', '2')),
    }
    if mode == 'llm' and values.get('ANSWER_THEME'):
        options['answer_theme'] = values['ANSWER_THEME']
    return options

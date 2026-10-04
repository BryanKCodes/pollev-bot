"""Validation and ID mapping at the boundary between answers and activities."""

import html
import re
from typing import Optional, Sequence, Tuple

from .activity import ActivityOption, OptionId
from .answerer import OptionSelection


class InvalidAnswer(ValueError):
    """Generated output cannot be safely used as an answer."""


def candidate_options(options: Sequence[ActivityOption],
                      min_option: int = 0,
                      max_option: Optional[int] = None) -> Tuple[ActivityOption, ...]:
    """Apply the legacy inclusive/exclusive option bounds without losing IDs."""
    if (isinstance(min_option, bool) or not isinstance(min_option, int)
            or min_option < 0):
        raise ValueError('min_option must be a non-negative integer')
    if (max_option is not None and
            (isinstance(max_option, bool) or not isinstance(max_option, int)
             or max_option < min_option)):
        raise ValueError('max_option must be at least min_option')
    return tuple(options[min_option:max_option])


def parse_option_selection(raw: str, option_count: int) -> OptionSelection:
    """Accept only one 1-based number or one letter, then return a 0-based index."""
    if not isinstance(raw, str) or option_count < 1:
        raise InvalidAnswer('invalid_choice')
    token = raw.strip()
    if re.fullmatch(r'[1-9][0-9]*', token):
        index = int(token) - 1
    elif re.fullmatch(r'[A-Za-z]', token):
        index = ord(token.upper()) - ord('A')
    else:
        raise InvalidAnswer('invalid_choice')
    if index >= option_count:
        raise InvalidAnswer('choice_out_of_range')
    return OptionSelection(index)


def resolve_option_id(selection: OptionSelection,
                      candidates: Sequence[ActivityOption]) -> OptionId:
    """Map a validated candidate position to its original Poll Everywhere ID."""
    index = selection.index
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(candidates):
        raise InvalidAnswer('choice_out_of_range')
    return candidates[index].option_id


def clean_open_ended_answer(raw: str, max_chars: int) -> str:
    """Remove simple labels/markdown and require one short, direct sentence."""
    if not isinstance(raw, str) or max_chars < 1 or '```' in raw:
        raise InvalidAnswer('invalid_text')
    if re.search(r'\n\s*(?:[-*]|[0-9]+[.)])\s+', raw):
        raise InvalidAnswer('list_output')
    text = html.unescape(raw).strip().replace('**', '').replace('`', '')
    text = re.sub(r'^\s*(?:#{1,6}\s+|[-*]\s+|[0-9]+[.)]\s+)', '', text)
    text = text.strip(' \t\r\n"\'_*')
    text = re.sub(r'^(?:final answer|answer|response)[*_]*\s*:\s*', '',
                  text, flags=re.I)
    text = text.strip(' \t\r\n"\'_*')
    text = ' '.join(text.split())
    if not text or re.match(
            r'^(?:as an ai|i (?:cannot|can\'t)|here(?: is|\'s)|'
            r'(?:the|my) answer is)\b', text, re.I):
        raise InvalidAnswer('empty_or_model_commentary')
    # Multiple sentences are retried rather than silently truncating an answer.
    if re.search(r'[.!?]\s+\S', text):
        raise InvalidAnswer('multiple_sentences')
    if text[-1] not in '.!?':
        text += '.'
    if len(text) > max_chars:
        raise InvalidAnswer('text_too_long')
    return text


def clean_theme_answer(raw: str, max_chars: int = 64) -> str:
    """Require one plain two or three word phrase for a theme response."""
    if not isinstance(raw, str):
        raise InvalidAnswer('invalid_theme_text')
    text = ' '.join(raw.strip().split())
    words = text.split(' ')
    if (len(text) > max_chars or len(words) not in (2, 3)
            or any(not re.fullmatch(r"[\w]+(?:[-/'][\w]+)*", word, re.UNICODE)
                   for word in words)):
        raise InvalidAnswer('invalid_theme_phrase')
    return text

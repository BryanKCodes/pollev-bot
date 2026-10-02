"""Normalize Poll Everywhere participant JSON without making answer decisions.

The current host has no active activity from which to confirm the exact JSON
type field. These field/value aliases reflect Poll Everywhere product and older
API names. Unknown metadata stays unsupported until a redacted live fixture
confirms it. Only the existing multiple-choice participant route is accepted
as endpoint evidence for a payload without type metadata.
"""

import html
import logging
import re
from collections.abc import Mapping
from typing import Any, Optional

from bs4 import BeautifulSoup

from .activity import Activity, ActivityKind, ActivityOption

logger = logging.getLogger(__name__)

MULTIPLE_CHOICE_ENDPOINT = 'multiple_choice_polls'
_TYPE_FIELDS = ('activity_type', 'poll_type', 'type')
_TYPE_VALUES = {
    'multiple_choice': ActivityKind.MULTIPLE_CHOICE,
    'multiple_choice_poll': ActivityKind.MULTIPLE_CHOICE,
    'open_ended': ActivityKind.OPEN_ENDED,
    'open_ended_text': ActivityKind.OPEN_ENDED,
    'free_text': ActivityKind.OPEN_ENDED,
    'free_text_poll': ActivityKind.OPEN_ENDED,
}


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ''
    plain = BeautifulSoup(value, 'html.parser').get_text(' ', strip=True)
    return ' '.join(html.unescape(plain).split())


def _type_token(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    separated = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', value.strip())
    return re.sub(r'[^a-z0-9]+', '_', separated.casefold()).strip('_')


def _metadata_kind(payload: Mapping, metadata: Optional[Mapping]):
    # A specific field takes precedence over a generic top-level `type`.
    # Conflicting values for the same field are not safe to classify.
    for field in _TYPE_FIELDS:
        tokens = []
        for source in (metadata, payload):
            if not isinstance(source, Mapping) or field not in source:
                continue
            value = source[field]
            if value is None or value == '':
                continue
            token = _type_token(value)
            if token is None:
                return ActivityKind.UNSUPPORTED, 'unknown_type_metadata'
            tokens.append(token)
        if not tokens:
            continue
        kinds = {_TYPE_VALUES.get(token, ActivityKind.UNSUPPORTED) for token in tokens}
        if len(kinds) != 1:
            return ActivityKind.UNSUPPORTED, 'conflicting_type_metadata'
        kind = kinds.pop()
        if kind is ActivityKind.UNSUPPORTED:
            return kind, 'unknown_type_metadata'
        return kind, None
    return None, 'missing_type_metadata'


def _question(payload: Mapping) -> str:
    for field in ('title', 'question', 'prompt'):
        value = payload.get(field)
        if isinstance(value, str):
            text = _clean_text(value)
            if text:
                return text
    return ''


def _options(payload: Mapping):
    raw_options = payload.get('options')
    if not isinstance(raw_options, list) or not raw_options:
        return None
    result = []
    seen_ids = set()
    for item in raw_options:
        if not isinstance(item, Mapping):
            return None
        option_id = item.get('id')
        if (isinstance(option_id, bool) or not isinstance(option_id, (int, str))
                or option_id == '' or option_id in seen_ids):
            return None
        seen_ids.add(option_id)
        text = ''
        for field in ('text', 'title', 'label'):
            if isinstance(item.get(field), str):
                text = _clean_text(item[field])
                if text:
                    break
        result.append(ActivityOption(option_id=option_id, text=text))
    return tuple(result)


def normalize_activity(activity_id: str, payload: Mapping,
                       *, metadata: Optional[Mapping] = None,
                       successful_endpoint: Optional[str] = None) -> Activity:
    """Classify a fetched activity, returning UNSUPPORTED when evidence is unsafe.

    `successful_endpoint` must identify a route that actually returned this
    payload. The only verified endpoint fallback here is `multiple_choice_polls`.
    Raw Poll Everywhere JSON remains in this module, outside the activity model.
    """
    if not isinstance(activity_id, str) or not activity_id:
        raise ValueError('activity_id must be a non-empty string')
    if not isinstance(payload, Mapping):
        logger.warning('Skipping activity: malformed_payload')
        return Activity(activity_id, ActivityKind.UNSUPPORTED, '')

    question = _question(payload)
    kind, reason = _metadata_kind(payload, metadata)
    if kind is None:
        if successful_endpoint == MULTIPLE_CHOICE_ENDPOINT:
            kind = ActivityKind.MULTIPLE_CHOICE
        else:
            kind = ActivityKind.UNSUPPORTED
    if kind is ActivityKind.OPEN_ENDED and successful_endpoint == MULTIPLE_CHOICE_ENDPOINT:
        kind, reason = ActivityKind.UNSUPPORTED, 'conflicting_endpoint_evidence'

    if kind is ActivityKind.MULTIPLE_CHOICE:
        options = _options(payload)
        if options is not None:
            return Activity(activity_id, kind, question, options)
        kind, reason = ActivityKind.UNSUPPORTED, 'missing_or_malformed_options'

    if kind is ActivityKind.UNSUPPORTED:
        logger.info('Skipping activity: %s', reason or 'unsupported_type')
    return Activity(activity_id, kind, question)

"""Transport-independent representation of a Poll Everywhere activity."""

from dataclasses import dataclass
from enum import Enum
from typing import Tuple, Union


class ActivityKind(str, Enum):
    MULTIPLE_CHOICE = 'multiple_choice'
    OPEN_ENDED = 'open_ended'
    UNSUPPORTED = 'unsupported'


OptionId = Union[int, str]


@dataclass(frozen=True)
class ActivityOption:
    option_id: OptionId
    text: str


@dataclass(frozen=True)
class Activity:
    activity_id: str
    kind: ActivityKind
    question: str
    options: Tuple[ActivityOption, ...] = ()

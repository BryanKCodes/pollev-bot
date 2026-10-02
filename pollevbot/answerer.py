"""Answer generation contract, independent of Poll Everywhere transport.

Providers receive question text and ordered option text only. A selection is
an index into that supplied list; the caller later resolves it to the original
Poll Everywhere option ID. The caller also validates results before submission.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class OptionSelection:
    index: int


@dataclass(frozen=True)
class TextAnswer:
    text: str


class AnswerProvider(ABC):
    @abstractmethod
    def select_option(self, question: str,
                      options: Sequence[str]) -> OptionSelection:
        """Select one position from the supplied ordered option texts."""

    @abstractmethod
    def answer_open_ended(self, question: str) -> TextAnswer:
        """Produce a short response to the supplied question text."""

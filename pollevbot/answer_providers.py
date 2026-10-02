"""Non-LLM answer providers for legacy behavior and deterministic fixtures."""

import random
from typing import List, Optional, Sequence, Tuple

from .answerer import AnswerProvider, OptionSelection, TextAnswer


class UnsupportedAnswerKind(RuntimeError):
    """A provider cannot generate this kind of answer."""


class LegacyRandomProvider(AnswerProvider):
    """Choose uniformly among the candidate options, as the old bot does.

    The caller supplies the already-filtered ordered candidates. The returned
    index refers to that list, which the caller maps back to the original ID.
    """

    def __init__(self, rng: Optional[random.Random] = None):
        self._rng = rng if rng is not None else random

    def select_option(self, question: str,
                      options: Sequence[str]) -> OptionSelection:
        return OptionSelection(self._rng.choice(range(len(options))))

    def answer_open_ended(self, question: str) -> TextAnswer:
        raise UnsupportedAnswerKind('Legacy random mode supports multiple choice only.')


class FakeAnswerProvider(AnswerProvider):
    """Return fixed answers and record inputs for future orchestration tests."""

    def __init__(self, choice_index: int = 0,
                 open_ended_text: str = 'Test response.'):
        self.choice_index = choice_index
        self.open_ended_text = open_ended_text
        self.choice_requests: List[Tuple[str, Tuple[str, ...]]] = []
        self.open_ended_requests: List[str] = []

    def select_option(self, question: str,
                      options: Sequence[str]) -> OptionSelection:
        self.choice_requests.append((question, tuple(options)))
        return OptionSelection(self.choice_index)

    def answer_open_ended(self, question: str) -> TextAnswer:
        self.open_ended_requests.append(question)
        return TextAnswer(self.open_ended_text)

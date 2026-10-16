"""Non-LLM answer providers for legacy behavior and deterministic fixtures."""

import logging
import random
import re
from typing import List, Optional, Sequence, Tuple

from .answerer import AnswerProvider, OptionSelection, TextAnswer
from .answer_validation import clean_submission_answer

RANDOM_TEXT_RESPONSE = 'yes.'
logger = logging.getLogger(__name__)


class UnsupportedAnswerKind(RuntimeError):
    """A provider cannot generate this kind of answer."""


class LegacyRandomProvider(AnswerProvider):
    """Choose random MCQ options and return a fixed text reply without a model.

    The caller supplies the already-filtered ordered candidates. The returned
    index refers to that list, which the caller maps back to the original ID.
    """

    def __init__(self, rng: Optional[random.Random] = None):
        self._rng = rng if rng is not None else random

    def select_option(self, question: str,
                      options: Sequence[str]) -> OptionSelection:
        return OptionSelection(self._rng.choice(range(len(options))))

    def answer_open_ended(self, question: str) -> TextAnswer:
        return TextAnswer(RANDOM_TEXT_RESPONSE)


class ResilientAnswerProvider(AnswerProvider):
    """Try the model, then a theme, and always retain a usable local fallback."""

    def __init__(self, primary=None, theme='', theme_provider=None):
        self.primary = primary
        self.theme = ' '.join(theme.split())
        self.theme_provider = theme_provider
        self.config = getattr(primary, 'config', None)
        self.max_open_chars = getattr(self.config, 'max_open_chars', 280)
        self.random = LegacyRandomProvider()

    def select_option(self, question: str, options: Sequence[str]) -> OptionSelection:
        if not options:
            raise ValueError('No candidate options are available.')
        if self.primary is not None and question and all(options):
            try:
                answer = self.primary.select_option(question, options)
                if (not isinstance(answer, OptionSelection) or isinstance(answer.index, bool)
                        or not isinstance(answer.index, int) or not 0 <= answer.index < len(options)):
                    raise ValueError('Invalid model option selection.')
                return answer
            except Exception as exc:
                logger.warning('MCQ model answer unavailable (%s); selecting a random option.',
                               type(exc).__name__)
        else:
            logger.info('Question or choice text unavailable; selecting a random option.')
        return self.random.select_option(question, options)

    def _local_theme_answer(self):
        if re.search(r'\bmcq\b|multiple[ -]choice', self.theme, re.IGNORECASE):
            letters = []
            for first, last in re.findall(r'\b([A-Z])\s*[-–—]\s*([A-Z])\b', self.theme):
                step = 1 if first <= last else -1
                letters.extend(chr(code) for code in range(ord(first), ord(last) + step, step))
            letters.extend(re.findall(r'\b[A-Z]\b', self.theme))
            letters = tuple(dict.fromkeys(letters)) or tuple('ABCD')
            return random.choice(letters)
        words = re.findall(r"[\w]+(?:[-/'][\w]+)*", self.theme, re.UNICODE)
        if not words:
            return RANDOM_TEXT_RESPONSE
        return ' '.join(words[:3]) if len(words) > 1 else words[0] + ' concepts'

    def answer_open_ended(self, question: str) -> TextAnswer:
        if self.primary is not None and question:
            try:
                answer = self.primary.answer_open_ended(question)
                return TextAnswer(clean_submission_answer(answer.text, self.max_open_chars))
            except Exception as exc:
                logger.warning('Text model answer unavailable (%s); using fallback.',
                               type(exc).__name__)
        if self.theme:
            # MCQ is a reply-format instruction, not a two-word course topic.
            is_mcq = re.search(r'\bmcq\b|multiple[ -]choice', self.theme, re.IGNORECASE)
            if self.theme_provider is not None and not is_mcq:
                try:
                    answer = self.theme_provider.answer_open_ended('')
                    text = clean_submission_answer(answer.text, self.max_open_chars)
                    logger.info('Using the fallback theme for this text response.')
                    return TextAnswer(text)
                except Exception as exc:
                    logger.warning('Theme generation unavailable (%s); using theme words.',
                                   type(exc).__name__)
            text = self._local_theme_answer()
            try:
                text = clean_submission_answer(text, self.max_open_chars)
            except ValueError:
                text = RANDOM_TEXT_RESPONSE[:self.max_open_chars]
            logger.info('Using a local theme fallback for this text response.')
            return TextAnswer(text)
        logger.info('Using the default text fallback: %s', RANDOM_TEXT_RESPONSE)
        return TextAnswer(RANDOM_TEXT_RESPONSE[:self.max_open_chars])


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

"""Local GGUF answer generation through llama-cpp-python.

Importing this module does not import llama-cpp-python or load a model. The
model is loaded on the first answer request and shared by providers using the
same model and runtime settings in this process.
"""

import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .answer_providers import LegacyRandomProvider
from .answer_validation import (InvalidAnswer, clean_open_ended_answer,
                                clean_theme_answer,
                                parse_option_selection)
from .answerer import AnswerProvider, OptionSelection, TextAnswer
from .model_setup import DEFAULT_MODEL_PATH, resolve_model_path

logger = logging.getLogger(__name__)

_MODEL_CACHE: Dict[Tuple[str, int, int, int, int], Any] = {}
_MODEL_LOCK = threading.RLock()


class ModelConfigurationError(ValueError):
    """The local model or its settings are unavailable."""


class AnswerUnavailable(RuntimeError):
    """No validated answer was generated."""


class InferenceFailed(RuntimeError):
    """The local model failed or exceeded the soft generation deadline."""


@dataclass(frozen=True)
class LlamaCppConfig:
    model_path: Path
    context_size: int = 2048
    threads: int = 4
    gpu_layers: int = 0
    mcq_max_tokens: int = 8
    open_max_tokens: int = 64
    temperature: float = 0.0
    inference_timeout: float = 30.0
    max_open_chars: int = 280
    failure_policy: str = 'skip'
    seed: int = 0

    def __post_init__(self):
        path = Path(self.model_path).expanduser().resolve()
        if path.suffix.lower() != '.gguf' or not path.is_file():
            raise ModelConfigurationError('LLM_MODEL_PATH must name an existing GGUF file.')
        object.__setattr__(self, 'model_path', path)
        for name in ('context_size', 'threads', 'mcq_max_tokens',
                     'open_max_tokens', 'max_open_chars'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ModelConfigurationError('{} must be a positive integer.'.format(name))
        if self.context_size < 128 or self.mcq_max_tokens >= self.context_size or self.open_max_tokens >= self.context_size:
            raise ModelConfigurationError('Generation limits must fit within the context size.')
        if isinstance(self.gpu_layers, bool) or not isinstance(self.gpu_layers, int) or self.gpu_layers < -1:
            raise ModelConfigurationError('gpu_layers must be -1 or a non-negative integer.')
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ModelConfigurationError('seed must be an integer.')
        if not math.isfinite(self.temperature) or not 0.0 <= self.temperature <= 1.0:
            raise ModelConfigurationError('temperature must be between 0 and 1.')
        if not math.isfinite(self.inference_timeout) or self.inference_timeout <= 0:
            raise ModelConfigurationError('inference_timeout must be positive.')
        if self.failure_policy not in ('skip', 'random'):
            raise ModelConfigurationError('failure_policy must be skip or random.')

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> 'LlamaCppConfig':
        values = os.environ if env is None else env
        if values.get('LLM_BACKEND', 'llama_cpp') != 'llama_cpp':
            raise ModelConfigurationError('Only LLM_BACKEND=llama_cpp is supported.')
        model_path = values.get('LLM_MODEL_PATH') or DEFAULT_MODEL_PATH
        try:
            return cls(
                model_path=resolve_model_path(model_path),
                context_size=int(values.get('LLM_CONTEXT_SIZE', '2048')),
                threads=int(values.get('LLM_THREADS', str(max(1, (os.cpu_count() or 2) // 2)))),
                gpu_layers=int(values.get('LLM_GPU_LAYERS', '0')),
                mcq_max_tokens=int(values.get('LLM_MCQ_MAX_TOKENS', '8')),
                open_max_tokens=int(values.get('LLM_OPEN_MAX_TOKENS', '64')),
                temperature=float(values.get('LLM_TEMPERATURE', '0')),
                inference_timeout=float(values.get('LLM_INFERENCE_TIMEOUT', '30')),
                max_open_chars=int(values.get('LLM_MAX_OPEN_CHARS', '280')),
                failure_policy=values.get('LLM_FAILURE_POLICY', 'skip'),
                seed=int(values.get('LLM_SEED', '0')),
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ModelConfigurationError):
                raise
            raise ModelConfigurationError('Invalid LLM configuration value.') from exc


def _model_for(config: LlamaCppConfig):
    key = (str(config.model_path), config.context_size, config.threads,
           config.gpu_layers, config.seed)
    with _MODEL_LOCK:
        if key not in _MODEL_CACHE:
            try:
                from llama_cpp import Llama
            except ImportError as exc:
                raise ModelConfigurationError(
                    'Install requirements-local.txt to use the local LLM.') from exc
            try:
                logger.info('Loading local model %s (GPU layers: %s).',
                            config.model_path.name, config.gpu_layers)
                _MODEL_CACHE[key] = Llama(
                    model_path=str(config.model_path), n_ctx=config.context_size,
                    n_threads=config.threads, n_gpu_layers=config.gpu_layers,
                    seed=config.seed, verbose=False)
                logger.info('Local model ready.')
            except Exception as exc:
                raise ModelConfigurationError(
                    'Could not load the GGUF model ({}).'.format(type(exc).__name__)) from exc
        return _MODEL_CACHE[key]


def _choice_grammar(labels: Sequence[str]):
    try:
        from llama_cpp import LlamaGrammar
    except ImportError as exc:
        raise ModelConfigurationError('llama-cpp-python grammar support is required.') from exc
    rules = ' | '.join('"{}"'.format(label) for label in labels)
    return LlamaGrammar.from_string('root ::= {}\n'.format(rules), verbose=False)


class LlamaCppProvider(AnswerProvider):
    def __init__(self, config: LlamaCppConfig,
                 random_fallback: Optional[LegacyRandomProvider] = None):
        self.config = config
        self._random_fallback = (random_fallback if random_fallback is not None
                                 else LegacyRandomProvider())

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> 'LlamaCppProvider':
        return cls(LlamaCppConfig.from_env(env))

    def _complete(self, messages, max_tokens: int, max_chars: int, grammar=None) -> str:
        model = _model_for(self.config)
        parts = []
        total_chars = 0
        finish_reason = None
        with _MODEL_LOCK:
            deadline = time.monotonic() + self.config.inference_timeout
            try:
                stream = model.create_chat_completion(
                    messages=messages, stream=True, max_tokens=max_tokens,
                    temperature=self.config.temperature, top_p=1.0,
                    seed=self.config.seed, grammar=grammar)
                try:
                    for chunk in stream:
                        if time.monotonic() > deadline:
                            raise InferenceFailed('inference_timeout')
                        choice = chunk['choices'][0]
                        content = choice.get('delta', {}).get('content')
                        if content is not None:
                            if not isinstance(content, str):
                                raise InvalidAnswer('malformed_model_output')
                            parts.append(content)
                            total_chars += len(content)
                            if total_chars > max_chars:
                                raise InvalidAnswer('model_output_too_long')
                        finish_reason = choice.get('finish_reason') or finish_reason
                finally:
                    if hasattr(stream, 'close'):
                        stream.close()
            except (InferenceFailed, InvalidAnswer):
                raise
            except Exception as exc:
                raise InferenceFailed('model_call_failed') from exc
        if finish_reason == 'length':
            raise InvalidAnswer('truncated_generation')
        return ''.join(parts)

    def _failed_choice(self, question: str,
                       options: Sequence[str], reason: str) -> OptionSelection:
        logger.warning('No validated multiple-choice model answer: %s', reason)
        if self.config.failure_policy == 'random':
            logger.warning('Using the configured random answer fallback.')
            return self._random_fallback.select_option(question, options)
        raise AnswerUnavailable('No validated multiple-choice answer.')

    def select_option(self, question: str,
                      options: Sequence[str]) -> OptionSelection:
        if not options:
            raise AnswerUnavailable('No candidate options.')
        if (not isinstance(question, str) or not question.strip()
                or any(not isinstance(option, str) or not option.strip()
                       for option in options)):
            return self._failed_choice(question, options, 'missing_question_or_option_text')

        if len(options) <= 26:
            labels = tuple(chr(ord('A') + index) for index in range(len(options)))
            label_instruction = 'letter from A to {}'.format(labels[-1])
        else:
            labels = tuple(str(index) for index in range(1, len(options) + 1))
            label_instruction = 'number from 1 to {}'.format(len(options))
        option_lines = '\n'.join('{}. {}'.format(label, text)
                                 for label, text in zip(labels, options))
        grammar = _choice_grammar(labels)
        for attempt in range(2):
            instruction = ('Choose the best answer. Return exactly one option {}, '
                           'with no other text.'.format(label_instruction))
            if attempt:
                instruction = ('Your entire reply must be one {}. '
                               'Do not add a word, label, or punctuation.'.format(
                                   label_instruction))
            messages = [
                {'role': 'system', 'content': instruction},
                {'role': 'user', 'content': 'Question:\n{}\nOptions:\n{}'.format(
                    question, option_lines)},
            ]
            try:
                raw = self._complete(messages, self.config.mcq_max_tokens, 64,
                                     grammar=grammar)
                return parse_option_selection(raw, len(options))
            except InvalidAnswer as exc:
                reason = str(exc)
                if attempt == 0:
                    continue
            except InferenceFailed as exc:
                reason = str(exc)
                break
        return self._failed_choice(question, options, reason)

    def answer_open_ended(self, question: str) -> TextAnswer:
        if not isinstance(question, str) or not question.strip():
            raise AnswerUnavailable('No question text.')
        for attempt in range(2):
            instruction = ('Answer the question in one direct sentence. Do not include '
                           'a preamble, label, markdown, or commentary about the task.')
            if attempt:
                instruction = ('Write only one short sentence answering the question. '
                               'No heading, list, explanation, or extra sentence. '
                               'Keep it under {} characters.'.format(
                                   self.config.max_open_chars))
            messages = [
                {'role': 'system', 'content': instruction},
                {'role': 'user', 'content': question},
            ]
            try:
                raw = self._complete(messages, self.config.open_max_tokens,
                                     self.config.max_open_chars * 2)
                clean = clean_open_ended_answer(raw, self.config.max_open_chars)
                return TextAnswer(clean)
            except InvalidAnswer as exc:
                reason = str(exc)
                if attempt == 0:
                    continue
            except InferenceFailed as exc:
                reason = str(exc)
                break
        logger.warning('No validated open-ended model answer: %s', reason)
        raise AnswerUnavailable('No validated open-ended answer.')


class ThemeProvider(LlamaCppProvider):
    """Generate a short theme phrase when the presenter hides the prompt.

    Multiple-choice answers retain the legacy random behavior; there is no
    question text from which to judge them in this mode.
    """

    def __init__(self, config: LlamaCppConfig, theme: str):
        if not isinstance(theme, str) or not 1 <= len(theme.strip()) <= 120:
            raise ModelConfigurationError('ANSWER_THEME must contain 1 to 120 characters.')
        super().__init__(config)
        self.theme = ' '.join(theme.split())

    @classmethod
    def from_env(cls, theme: str, env: Optional[Mapping[str, str]] = None) -> 'ThemeProvider':
        return cls(LlamaCppConfig.from_env(env), theme)

    def select_option(self, question: str,
                      options: Sequence[str]) -> OptionSelection:
        return self._random_fallback.select_option(question, options)

    def answer_open_ended(self, question: str) -> TextAnswer:
        context = ('Question visible to participant: {}\n'.format(question)
                   if isinstance(question, str) and question.strip() else '')
        for attempt in range(2):
            instruction = ('Return exactly a two or three word phrase related to the '
                           'given course theme. If a question is shown, make the phrase '
                           'fit it. No punctuation, labels, or explanation.')
            if attempt:
                instruction = ('Output only two or three topic words separated by spaces. '
                               'No full sentence, punctuation, or extra text.')
            messages = [
                {'role': 'system', 'content': instruction},
                {'role': 'user', 'content': '{}Course theme: {}'.format(context, self.theme)},
            ]
            try:
                raw = self._complete(messages, min(16, self.config.open_max_tokens), 80)
                return TextAnswer(clean_theme_answer(
                    raw, min(64, self.config.max_open_chars)))
            except InvalidAnswer as exc:
                reason = str(exc)
                if attempt == 0:
                    continue
            except InferenceFailed as exc:
                reason = str(exc)
                break
        logger.warning('No validated theme phrase: %s', reason)
        raise AnswerUnavailable('No validated theme phrase.')

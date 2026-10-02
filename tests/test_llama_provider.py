import random
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from pollevbot.answer_providers import LegacyRandomProvider
from pollevbot.answer_validation import InvalidAnswer
from pollevbot.llama_cpp_provider import (AnswerUnavailable, LlamaCppConfig,
                                         InferenceFailed, LlamaCppProvider,
                                         ModelConfigurationError,
                                         _MODEL_CACHE)


class LlamaProviderTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.model_path = Path(self.folder.name) / 'sample.gguf'
        self.model_path.write_bytes(b'fixture only')
        _MODEL_CACHE.clear()
        self.addCleanup(_MODEL_CACHE.clear)

    def config(self, **overrides):
        return LlamaCppConfig(model_path=self.model_path, **overrides)

    def test_environment_maps_all_model_controls(self):
        values = {
            'LLM_BACKEND': 'llama_cpp', 'LLM_MODEL_PATH': str(self.model_path),
            'LLM_CONTEXT_SIZE': '4096', 'LLM_THREADS': '2',
            'LLM_GPU_LAYERS': '-1', 'LLM_MCQ_MAX_TOKENS': '5',
            'LLM_OPEN_MAX_TOKENS': '40', 'LLM_TEMPERATURE': '0.2',
            'LLM_INFERENCE_TIMEOUT': '7', 'LLM_MAX_OPEN_CHARS': '120',
            'LLM_FAILURE_POLICY': 'random', 'LLM_SEED': '17',
        }
        config = LlamaCppConfig.from_env(values)
        self.assertEqual(config.model_path, self.model_path.resolve())
        self.assertEqual((config.context_size, config.threads, config.gpu_layers),
                         (4096, 2, -1))
        self.assertEqual((config.mcq_max_tokens, config.open_max_tokens), (5, 40))
        self.assertEqual((config.temperature, config.inference_timeout,
                          config.max_open_chars, config.failure_policy,
                          config.seed), (0.2, 7.0, 120, 'random', 17))
        self.assertEqual(LlamaCppConfig.from_env({
            'LLM_MODEL_PATH': str(self.model_path)}).failure_policy, 'skip')

    def test_invalid_configuration_fails_before_loading(self):
        for changes in ({'LLM_BACKEND': 'unknown'},
                        {'LLM_CONTEXT_SIZE': 'not-an-int'},
                        {'LLM_FAILURE_POLICY': 'always_submit'},
                        {'LLM_MODEL_PATH': '/missing/sample.gguf'}):
            with self.subTest(changes=changes):
                values = {'LLM_MODEL_PATH': str(self.model_path), **changes}
                with self.assertRaises(ModelConfigurationError):
                    LlamaCppConfig.from_env(values)

    def test_mcq_retries_once_with_stricter_prompt(self):
        provider = LlamaCppProvider(self.config())
        with patch('pollevbot.llama_cpp_provider._choice_grammar',
                   return_value='grammar'), patch.object(
                       provider, '_complete', side_effect=['Option B', 'B']) as complete:
            choice = provider.select_option('Which answer?', ['First', 'Second'])
        self.assertEqual(choice.index, 1)
        self.assertEqual(complete.call_count, 2)
        self.assertEqual(complete.call_args_list[0].args[1:3], (8, 64))
        self.assertEqual(complete.call_args_list[0].kwargs['grammar'], 'grammar')
        prompt = complete.call_args_list[0].args[0][1]['content']
        self.assertIn('A. First', prompt)
        self.assertIn('B. Second', prompt)
        self.assertNotIn('opaque-id', prompt)
        self.assertIn('entire reply', complete.call_args_list[1].args[0][0]['content'])

    def test_invalid_mcq_skips_unless_random_explicitly_enabled(self):
        provider = LlamaCppProvider(self.config())
        with patch('pollevbot.llama_cpp_provider._choice_grammar',
                   return_value=None), patch.object(
                       provider, '_complete', side_effect=['A or B', 'neither']) as complete:
            with self.assertRaises(AnswerUnavailable):
                provider.select_option('Choose', ['One', 'Two'])
        self.assertEqual(complete.call_count, 2)
        fallback = LlamaCppProvider(
            self.config(failure_policy='random'),
            random_fallback=LegacyRandomProvider(random.Random(0)))
        with patch('pollevbot.llama_cpp_provider._choice_grammar',
                   return_value=None), patch.object(
                       fallback, '_complete', side_effect=['bad', 'bad']):
            self.assertEqual(fallback.select_option(
                'Choose', ['One', 'Two']).index, 1)

    def test_open_ended_retries_and_rejects_long_or_multiple_sentences(self):
        provider = LlamaCppProvider(self.config(max_open_chars=55))
        with patch.object(provider, '_complete',
                          side_effect=['First. Second.',
                                       '**Answer:**  It is useful']) as complete:
            answer = provider.answer_open_ended('Why?')
        self.assertEqual(answer.text, 'It is useful.')
        self.assertEqual(complete.call_count, 2)
        self.assertEqual(complete.call_args_list[0].args[1:3], (64, 110))
        with patch.object(provider, '_complete',
                          side_effect=['x' * 100, 'y' * 100]):
            with self.assertRaises(AnswerUnavailable):
                provider.answer_open_ended('Why?')

    def test_model_is_lazy_and_cached_once_per_process(self):
        calls = []

        class FakeLlama:
            def __init__(self, **kwargs):
                calls.append(('load', kwargs))

            def create_chat_completion(self, **kwargs):
                calls.append(('infer', kwargs))
                return iter([{'choices': [{'delta': {'content': 'A'},
                                           'finish_reason': 'stop'}]}])

        fake_module = types.ModuleType('llama_cpp')
        fake_module.Llama = FakeLlama
        config = self.config()
        first = LlamaCppProvider(config)
        second = LlamaCppProvider(config)
        self.assertEqual(calls, [])
        with patch.dict('sys.modules', {'llama_cpp': fake_module}):
            self.assertEqual(first._complete([], 8, 64), 'A')
            self.assertEqual(second._complete([], 8, 64), 'A')
        self.assertEqual([kind for kind, _ in calls], ['load', 'infer', 'infer'])
        self.assertEqual(calls[1][1]['stream'], True)
        self.assertEqual(calls[1][1]['temperature'], 0.0)

    def test_truncated_generation_is_invalid(self):
        provider = LlamaCppProvider(self.config())

        class TruncatedModel:
            def create_chat_completion(self, **kwargs):
                return iter([{'choices': [{'delta': {'content': 'B'},
                                           'finish_reason': 'length'}]}])

        with patch('pollevbot.llama_cpp_provider._model_for',
                   return_value=TruncatedModel()):
            with self.assertRaises(InvalidAnswer):
                provider._complete([], 1, 10)

    def test_streaming_deadline_stops_after_a_late_token(self):
        provider = LlamaCppProvider(self.config(inference_timeout=1))

        class SlowModel:
            def create_chat_completion(self, **kwargs):
                return iter([{'choices': [{'delta': {'content': 'A'},
                                           'finish_reason': None}]}])

        with patch('pollevbot.llama_cpp_provider._model_for',
                   return_value=SlowModel()), patch(
                       'pollevbot.llama_cpp_provider.time.monotonic',
                       side_effect=[0, 2]):
            with self.assertRaises(InferenceFailed):
                provider._complete([], 8, 10)


if __name__ == '__main__':
    unittest.main()

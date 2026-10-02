import unittest
from unittest.mock import patch

from pollevbot.answerer import OptionSelection
from pollevbot.llama_cpp_provider import AnswerUnavailable
from scripts.benchmark_local_model import benchmark


class BenchmarkReportingTests(unittest.TestCase):
    def test_reports_latency_accuracy_failures_and_memory_without_answer_text(self):
        class FakeProvider:
            def __init__(self, config):
                pass

            def select_option(self, question, options):
                return OptionSelection(1)

            def answer_open_ended(self, question):
                raise AnswerUnavailable('synthetic failure')

        questions = [
            {'kind': 'multiple_choice', 'question': 'Synthetic choice?',
             'options': ['One', 'Two'], 'expected_index': 1},
            {'kind': 'open_ended', 'question': 'Synthetic prompt?'},
        ]
        with patch('scripts.benchmark_local_model.LlamaCppProvider', FakeProvider), patch(
                'scripts.benchmark_local_model._model_for'), patch(
                    'scripts.benchmark_local_model.time.monotonic',
                    side_effect=[0, 1, 2, 2.3, 3, 3.8]), patch(
                        'scripts.benchmark_local_model.peak_memory_mb',
                        return_value=123.4), patch(
                            'scripts.benchmark_local_model.bot_options_from_env',
                            return_value={'open_wait': 0.5}):
            result = benchmark(questions, object())
        self.assertEqual(result['cold_load_seconds'], 1)
        self.assertEqual(result['invalid_output_rate'], 0.5)
        self.assertEqual(result['mcq_accuracy'], 1)
        self.assertEqual(result['labeled_mcq_count'], 1)
        self.assertEqual(result['peak_memory_mb'], 123.4)
        self.assertFalse(result['all_answers_within_open_wait'])
        self.assertNotIn('Synthetic', str(result))


if __name__ == '__main__':
    unittest.main()

"""Measure a local GGUF provider on a permissioned question set.

Input JSON is a list of objects with ``kind``, ``question``, and for MCQ
``options``. Optional ``expected_index`` (zero-based) enables accuracy
measurement. Question and answer text is never printed.
"""

import argparse
import json
import resource
import statistics
import sys
import time
from pathlib import Path

from pollevbot.llama_cpp_provider import (AnswerUnavailable, LlamaCppConfig,
                                         LlamaCppProvider, _model_for)
from pollevbot.runtime_config import bot_options_from_env


def peak_memory_mb():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024 if sys.platform == 'darwin' else 1024)


def benchmark(questions, config):
    provider = LlamaCppProvider(config)
    started = time.monotonic()
    _model_for(config)
    cold_load_seconds = time.monotonic() - started
    latencies = []
    failed = 0
    labeled = 0
    correct = 0
    for item in questions:
        kind = item['kind']
        question = item['question']
        if kind not in ('multiple_choice', 'open_ended') or not isinstance(question, str):
            raise ValueError('Each item needs a supported kind and question text.')
        started = time.monotonic()
        try:
            if kind == 'multiple_choice':
                options = item['options']
                if (not isinstance(options, list) or not options
                        or any(not isinstance(option, str) for option in options)):
                    raise ValueError('MCQ options must be a non-empty text list.')
                selection = provider.select_option(question, options)
                if 'expected_index' in item:
                    expected = item['expected_index']
                    if (isinstance(expected, bool) or not isinstance(expected, int)
                            or not 0 <= expected < len(options)):
                        raise ValueError('expected_index is outside the option list.')
                    labeled += 1
                    correct += selection.index == expected
            else:
                provider.answer_open_ended(question)
        except AnswerUnavailable:
            failed += 1
        finally:
            latencies.append(time.monotonic() - started)
    open_wait = bot_options_from_env()['open_wait']
    return {
        'questions': len(questions),
        'cold_load_seconds': round(cold_load_seconds, 3),
        'mean_answer_seconds': round(statistics.mean(latencies), 3) if latencies else None,
        'max_answer_seconds': round(max(latencies), 3) if latencies else None,
        'invalid_output_rate': round(failed / len(questions), 3) if questions else None,
        'mcq_accuracy': round(correct / labeled, 3) if labeled else None,
        'labeled_mcq_count': labeled,
        'peak_memory_mb': round(peak_memory_mb(), 1),
        'open_wait_seconds': open_wait,
        'all_answers_within_open_wait': all(value <= open_wait for value in latencies),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('questions', type=Path,
                        help='permissioned JSON question set; never committed with course data')
    args = parser.parse_args()
    questions = json.loads(args.questions.read_text())
    if not isinstance(questions, list):
        parser.error('The question set must be a JSON array.')
    print(json.dumps(benchmark(questions, LlamaCppConfig.from_env()), indent=2))


if __name__ == '__main__':
    main()
